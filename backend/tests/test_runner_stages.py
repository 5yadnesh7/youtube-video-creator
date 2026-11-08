import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.jobs import runner, store
from app.schemas import (
    CreateJobRequest,
    Job,
    JobSettings,
    RenderRequest,
    Research,
    Scene,
    SceneAudio,
    SceneMedia,
    Script,
    Seo,
)
from app.services import media, render, research as research_svc, script as script_svc, seo as seo_svc, tts

SCRIPT = Script(
    title="T",
    hook="H",
    scenes=[Scene(narration="one two", visual_query="moon"), Scene(narration="three four", visual_query="sun")],
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(store, "_conn", None)
    runner._runs.clear()
    yield
    runner._runs.clear()


def make_job(status="script_ready", script=True, job_id="job1") -> Job:
    job = Job(
        id=job_id,
        topic="topic",
        settings=JobSettings(),
        status=status,
        script=SCRIPT if script else None,
        created_at=store.now_iso(),
    )
    store.save(job)
    return job


async def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached in time")


def stage_status(job_id: str) -> dict[str, str]:
    return {s.key: s.status for s in store.get(job_id).stages}


def patch_fast_pipeline(monkeypatch, *, voice=None, visuals=None, render_fn=None):
    async def fake_voice(scenes, cfg, out_dir, model, on_progress=None):
        for i in range(len(scenes)):
            on_progress(i + 1, len(scenes))
        return [SceneAudio(path="a.mp3", duration=3.0) for _ in scenes]

    async def fake_visuals(scenes, durations, cfg, out_dir, on_progress=None):
        for i in range(len(scenes)):
            on_progress(i + 1, len(scenes))
        return [SceneMedia(path="v.mp4", kind="video") for _ in scenes]

    def fake_render(audio, visuals_, cfg, out_path, work_dir, on_progress=None, music_path=None, cancel=None):
        for pct in (10, 49, 60, 91, 95, 100):
            on_progress(pct)
        return [0.0, 3.0]

    async def fake_seo(script, cfg, starts, model):
        return Seo(title="t", description="d")

    async def fake_model(job):
        return "m"

    monkeypatch.setattr(tts, "synthesize_scenes", voice or fake_voice)
    monkeypatch.setattr(media, "fetch_media", visuals or fake_visuals)
    monkeypatch.setattr(render, "render_video", render_fn or fake_render)
    monkeypatch.setattr(seo_svc, "generate_seo", fake_seo)
    monkeypatch.setattr(runner, "_resolve_model", fake_model)
    monkeypatch.setattr(runner, "_load_media_manifest", lambda path, scenes: None)


def test_render_reports_every_stage_and_finishes(monkeypatch):
    patch_fast_pipeline(monkeypatch)

    async def main():
        runner.start_render(make_job(), RenderRequest())
        await wait_until(lambda: store.get("job1").status == "done")

    asyncio.run(main())
    job = store.get("job1")
    assert [s.key for s in job.stages] == ["voice", "visuals", "clips", "merge", "finish", "seo"]
    assert all(s.status == "done" and s.percent == 100 for s in job.stages)
    voice, visuals = job.stages[0], job.stages[1]
    assert (voice.done, voice.total) == (2, 2) and "voiced" in voice.detail
    assert "2 video clips" in visuals.detail
    assert all(s.started_at and s.finished_at for s in job.stages)


def test_stage_order_is_never_out_of_sequence(monkeypatch):
    patch_fast_pipeline(monkeypatch)
    seen = []
    real_save = store.save

    def spy(job):
        seen.append([s.status for s in job.stages])
        real_save(job)

    monkeypatch.setattr(store, "save", spy)

    async def main():
        runner.start_render(make_job(), RenderRequest())
        await wait_until(lambda: store.get("job1").status == "done")

    asyncio.run(main())
    for snapshot in seen:
        assert snapshot.count("running") <= 1
        running = snapshot.index("running") if "running" in snapshot else None
        if running is not None:
            assert all(s == "done" for s in snapshot[:running])
            assert all(s == "pending" for s in snapshot[running + 1:])


def test_cancel_during_voiceover_returns_to_script_screen(monkeypatch):
    async def slow_voice(scenes, cfg, out_dir, model, on_progress=None):
        on_progress(1, len(scenes))
        await asyncio.sleep(30)

    patch_fast_pipeline(monkeypatch, voice=slow_voice)

    async def main():
        job = runner.start_render(make_job(), RenderRequest())
        await wait_until(lambda: store.get("job1").stages[0].done == 1)
        runner.cancel(store.get("job1"))
        await wait_until(lambda: store.get("job1").status != "rendering")

    asyncio.run(main())
    job = store.get("job1")
    assert job.status == "script_ready" and job.step == "Stopped"
    assert "stopped" in job.error and job.script is not None
    assert stage_status("job1") == {
        "voice": "cancelled", "visuals": "pending", "clips": "pending",
        "merge": "pending", "finish": "pending", "seo": "pending",
    }


def test_cancel_reaches_the_render_thread_and_stops_it(monkeypatch):
    started, stopped = threading.Event(), threading.Event()

    def blocking_render(audio, visuals, cfg, out_path, work_dir, on_progress=None, music_path=None, cancel=None):
        started.set()
        while not cancel.is_set():
            time.sleep(0.01)
        stopped.set()
        raise render.RenderCancelled()

    patch_fast_pipeline(monkeypatch, render_fn=blocking_render)

    async def main():
        runner.start_render(make_job(), RenderRequest())
        await wait_until(started.is_set)
        runner.cancel(store.get("job1"))
        await wait_until(lambda: store.get("job1").status != "rendering")
        await wait_until(stopped.is_set)

    asyncio.run(main())
    assert store.get("job1").status == "script_ready"
    assert store.get("job1").stages[2].status == "cancelled"  # clips was the running stage
    assert stopped.is_set()


def test_progress_after_cancel_does_not_overwrite_the_stopped_state(monkeypatch):
    late = {}

    def render_fn(audio, visuals, cfg, out_path, work_dir, on_progress=None, music_path=None, cancel=None):
        while not cancel.is_set():
            time.sleep(0.01)
        on_progress(70)  # a straggling callback after the stop request
        late["called"] = True
        raise render.RenderCancelled()

    patch_fast_pipeline(monkeypatch, render_fn=render_fn)

    async def main():
        runner.start_render(make_job(), RenderRequest())
        await wait_until(lambda: store.get("job1").stages[2].status == "running")
        runner.cancel(store.get("job1"))
        await wait_until(lambda: late.get("called"))
        await wait_until(lambda: store.get("job1").status != "rendering")

    asyncio.run(main())
    assert store.get("job1").status == "script_ready"
    assert stage_status("job1")["merge"] == "pending"


def test_cancel_first_script_generation_marks_job_cancelled(monkeypatch):
    async def slow_research(topic, url, angle, model):
        await asyncio.sleep(30)

    async def fake_model(job):
        return "m"

    monkeypatch.setattr(research_svc, "research_topic", slow_research)
    monkeypatch.setattr(runner, "_resolve_model", fake_model)

    async def main():
        job = runner.create(CreateJobRequest(topic="topic"))
        assert [s.key for s in job.stages] == ["research", "script"] and job.stages[0].status == "running"
        runner.cancel(store.get(job.id))
        await wait_until(lambda: store.get(job.id).status != "scripting")
        return job.id

    job_id = asyncio.run(main())
    job = store.get(job_id)
    assert job.status == "cancelled" and job.step == "Stopped" and job.error is None
    assert job.script is None
    assert stage_status(job_id) == {"research": "cancelled", "script": "pending"}


def test_cancel_regeneration_keeps_the_previous_script(monkeypatch):
    async def slow_script(topic, cfg, model, research=None):
        await asyncio.sleep(30)

    async def fake_model(job):
        return "m"

    monkeypatch.setattr(script_svc, "generate_script", slow_script)
    monkeypatch.setattr(runner, "_resolve_model", fake_model)
    job = make_job()
    job.research = Research(brief="b", grounded=True)
    store.save(job)

    async def main():
        runner.regenerate(store.get("job1"))
        await wait_until(lambda: store.get("job1").stages[1].status == "running")
        assert store.get("job1").stages[0].detail == "Reused the earlier research"
        runner.cancel(store.get("job1"))
        await wait_until(lambda: store.get("job1").status != "scripting")

    asyncio.run(main())
    job = store.get("job1")
    assert job.status == "script_ready" and job.script.title == "T"
    assert job.error == "Script regeneration was stopped."


def test_cancel_with_nothing_running_settles_a_stale_job():
    job = make_job(status="rendering")
    runner.cancel(job)
    assert store.get("job1").status == "script_ready"
    assert store.get("job1").step == "Stopped"


def test_failure_marks_the_running_stage_failed(monkeypatch):
    async def broken_voice(scenes, cfg, out_dir, model, on_progress=None):
        raise RuntimeError("boom")

    patch_fast_pipeline(monkeypatch, voice=broken_voice)

    async def main():
        runner.start_render(make_job(), RenderRequest())
        await wait_until(lambda: store.get("job1").status != "rendering")

    asyncio.run(main())
    job = store.get("job1")
    assert job.status == "failed" and "boom" in job.error
    assert stage_status("job1")["voice"] == "failed"


def test_cancel_endpoint_rules(monkeypatch):
    from app.main import app

    make_job(status="done", job_id="finished")
    with TestClient(app) as client:
        assert client.post("/api/jobs/nope/cancel").status_code == 404
        r = client.post("/api/jobs/finished/cancel")
        assert r.status_code == 409 and "Nothing to stop" in r.json()["detail"]
