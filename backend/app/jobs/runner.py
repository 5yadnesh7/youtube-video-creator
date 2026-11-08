import asyncio
import json
import logging
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.config import settings
from app.jobs import store
from app.schemas import CreateJobRequest, Job, RenderRequest, Research, SceneMedia, Script, Stage
from app.services import (
    media,
    openrouter,
    render,
    research as research_svc,
    script as script_svc,
    seo as seo_svc,
    tts,
)

log = logging.getLogger(__name__)

SCRIPT_STAGES = [("research", "Researching the topic"), ("script", "Writing the script")]
RENDER_STAGES = [
    ("voice", "Generating voiceover"),
    ("visuals", "Finding visuals"),
    ("clips", "Rendering scene clips"),
    ("merge", "Joining scenes with transitions"),
    ("finish", "Mixing audio and exporting"),
    ("seo", "Writing title, description and tags"),
]

# job id -> (running task, flag that tells the render thread to kill ffmpeg)
_runs: dict[str, tuple[asyncio.Task, threading.Event]] = {}


def _spawn(job_id: str, make_coro: Callable[[threading.Event], object]) -> None:
    cancel = threading.Event()
    task = asyncio.ensure_future(make_coro(cancel))
    _runs[job_id] = (task, cancel)

    def forget(t: asyncio.Task) -> None:
        if _runs.get(job_id, (None, None))[0] is t:
            _runs.pop(job_id, None)
        if t.cancelled():
            # Cancelled before its first step ran, so the coroutine's own cleanup never executed.
            job = store.get(job_id)
            if job and job.status in ("scripting", "rendering"):
                _finish_cancelled(job)

    task.add_done_callback(forget)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Stages:
    """Keeps the per-step checklist on a job current; safe to call from the render thread."""

    def __init__(self, job: Job):
        self.job = job
        self._lock = threading.Lock()

    @staticmethod
    def init(job: Job, defs: list[tuple[str, str]]) -> "Stages":
        job.stages = [Stage(key=k, label=label) for k, label in defs]
        return Stages(job)

    def _stage(self, key: str) -> Stage:
        return next(st for st in self.job.stages if st.key == key)

    def start(self, key: str, detail: str = "", parallel: bool = False) -> None:
        with self._lock:
            st = self._stage(key)
            if st.status == "running":
                return
            if not parallel:
                for other in self.job.stages:
                    if other.status == "running":
                        self._close(other, "done")
            st.status, st.started_at, st.detail = "running", _now(), detail
            store.save(self.job)

    def fail(self, key: str) -> None:
        with self._lock:
            self._close(self._stage(key), "failed")
            store.save(self.job)

    def update(self, key: str, *, done: int | None = None, total: int | None = None,
               percent: int | None = None, detail: str | None = None) -> None:
        with self._lock:
            st = self._stage(key)
            if done is not None:
                st.done = done
            if total is not None:
                st.total = total
            if percent is not None:
                st.percent = max(0, min(100, percent))
            elif st.total:
                st.percent = int(100 * st.done / st.total)
            if detail is not None:
                st.detail = detail
            store.save(self.job)

    def finish(self, key: str, detail: str | None = None) -> None:
        with self._lock:
            st = self._stage(key)
            if st.started_at is None:
                st.started_at = _now()
            if detail is not None:
                st.detail = detail
            self._close(st, "done")
            store.save(self.job)

    def stop_running(self, status: str) -> None:
        with self._lock:
            for st in self.job.stages:
                if st.status == "running":
                    self._close(st, status)
            store.save(self.job)

    @staticmethod
    def _close(st: Stage, status: str) -> None:
        st.status, st.finished_at = status, _now()
        if status == "done":
            st.percent = 100
            if st.total:
                st.done = st.total


def _update(job: Job, **fields) -> None:
    for key, value in fields.items():
        setattr(job, key, value)
    store.save(job)


def create(req: CreateJobRequest) -> Job:
    job = Job(
        id=uuid.uuid4().hex[:12],
        topic=req.topic.strip(),
        topic_url=req.topic_url,
        topic_angle=req.topic_angle,
        settings=req.settings,
        status="scripting",
        step="Researching topic",
        created_at=store.now_iso(),
    )
    Stages.init(job, SCRIPT_STAGES).start("research")
    store.save(job)
    _spawn(job.id, lambda cancel: _generate_script(job.id))
    return job


def regenerate(job: Job) -> Job:
    needs_research = not (job.research and job.research.grounded)
    tr = Stages.init(job, SCRIPT_STAGES)
    if needs_research:
        tr.start("research")
    else:
        tr.finish("research", "Reused the earlier research")
        tr.start("script")
    _update(
        job,
        status="scripting",
        step="Researching topic" if needs_research else "Writing script",
        progress=0,
        error=None,
        seo=None,
    )
    _spawn(job.id, lambda cancel: _generate_script(job.id))
    return job


def start_render(job: Job, req: RenderRequest) -> Job:
    if req.voice_id:
        job.settings.voice_id = req.voice_id
    Stages.init(job, RENDER_STAGES).start("voice")
    _update(job, status="rendering", step="Starting", progress=0, error=None)
    _spawn(job.id, lambda cancel: _render(job.id, cancel))
    return job


def cancel(job: Job) -> Job:
    """Asks the running generation to stop; the run finalises the job itself a moment later."""
    run = _runs.get(job.id)
    if run is None:
        _finish_cancelled(job)  # nothing is actually running (stale state), just settle the job
        return job
    task, flag = run
    flag.set()
    task.cancel()
    return job


def _finish_cancelled(job: Job) -> None:
    Stages(job).stop_running("cancelled")
    if job.script:
        stopped = "Rendering" if job.status == "rendering" else "Script regeneration"
        _update(job, status="script_ready", step="Stopped", progress=0, error=f"{stopped} was stopped.")
    else:
        _update(job, status="cancelled", step="Stopped", progress=0, error=None)


async def _resolve_model(job: Job) -> str:
    return job.settings.model or await openrouter.default_model()


async def _generate_script(job_id: str) -> None:
    job = store.get(job_id)
    if not job:
        return
    tr = Stages(job)
    try:
        model = await _resolve_model(job)
        if not (job.research and job.research.grounded):
            try:
                research = await research_svc.research_topic(
                    job.topic, job.topic_url, job.topic_angle, model
                )
            except Exception:
                log.exception("research failed for %s", job_id)
                research = Research(brief="", grounded=False, notes="Research step crashed")
            found = len(research.sources)
            plural = "" if found == 1 else "s"
            tr.finish("research", f"{found} source{plural} used" if research.grounded else "No sources found")
            tr.start("script")
            _update(job, research=research, step="Writing script")
        script = await script_svc.generate_script(
            job.topic, job.settings, model, research=job.research
        )
        tr.finish("script", f"{len(script.scenes)} scenes")
        _update(job, script=script, status="script_ready", step="Script ready", progress=100)
    except asyncio.CancelledError:
        _finish_cancelled(job)
    except Exception as exc:
        log.exception("script generation failed for %s", job_id)
        tr.stop_running("failed")
        _update(job, status="failed", step="Script generation failed", error=str(exc))


def _scaled(job: Job, lo: int, hi: int, tr: Stages | None = None, key: str = "", noun: str = "scenes"
            ) -> Callable[[int, int], None]:
    def cb(done: int, total: int) -> None:
        job.progress = lo + int((hi - lo) * done / max(total, 1))
        if tr:
            tr.update(key, done=done, total=total, detail=f"{done} of {total} {noun}")
        else:
            store.save(job)

    return cb


def _estimated_durations(scenes, language: str) -> list[float]:
    """Spoken length per scene before any audio exists; slightly high so clips are long enough."""
    wps = script_svc.WPM[language] / 60
    return [max(2.0, len(sc.narration.split()) / wps * 1.1) for sc in scenes]


async def _gather_or_cancel_siblings(*coros):
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _settle_failure(tr: "Stages") -> None:
    # The stage that raised is already marked failed; anything still running was only cut short.
    failed_already = any(st.status == "failed" for st in tr.job.stages)
    tr.stop_running("cancelled" if failed_already else "failed")


def _media_key(scenes) -> list[list[str]]:
    return [[sc.visual_query, sc.media_type] for sc in scenes]


def _load_media_manifest(path: Path, scenes) -> list[SceneMedia] | None:
    try:
        data = json.loads(path.read_text())
        items = [SceneMedia.model_validate(m) for m in data["items"]]
        same_scenes = data["key"] == _media_key(scenes)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not same_scenes or len(items) != len(scenes) or not all(Path(m.path).exists() for m in items):
        return None
    return items


async def _render(job_id: str, cancel: threading.Event) -> None:
    job = store.get(job_id)
    if not job or not job.script:
        return
    tr = Stages(job)
    work = settings.jobs_dir / job_id
    out_path = store.video_path(job_id)
    try:
        model = await _resolve_model(job)
        scenes = job.script.scenes
        n = len(scenes)

        _update(job, step="Generating voiceover and finding visuals", progress=2)
        manifest = work / "media.json"
        cached = _load_media_manifest(manifest, scenes)
        fractions = {"voice": 0.0, "visuals": 1.0 if cached else 0.0}

        def progress_for(key: str, noun: str) -> Callable[[int, int], None]:
            def cb(done: int, total: int) -> None:
                fractions[key] = done / max(total, 1)
                job.progress = 2 + int(43 * (fractions["voice"] + fractions["visuals"]) / 2)
                tr.update(key, done=done, total=total, detail=f"{done} of {total} {noun}")

            return cb

        async def make_audio():
            tr.start("voice", parallel=True)
            tr.update("voice", done=0, total=n, detail=f"0 of {n} scenes voiced")
            try:
                audio = await tts.synthesize_scenes(
                    scenes, job.settings, work / "audio", model,
                    on_progress=progress_for("voice", "scenes voiced"),
                )
            except Exception:
                tr.fail("voice")
                raise
            tr.finish("voice", f"{n} scenes voiced, {sum(a.duration for a in audio):.0f}s of audio")
            return audio

        async def make_visuals():
            tr.start("visuals", parallel=True)
            if cached is not None:
                tr.finish("visuals", f"Reused {n} clips from the previous run")
                return cached
            tr.update("visuals", done=0, total=n, detail=f"0 of {n} scenes matched")
            try:
                got = await media.fetch_media(
                    scenes,
                    _estimated_durations(scenes, job.settings.language),
                    job.settings,
                    work / "media",
                    on_progress=progress_for("visuals", "scenes matched"),
                )
            except Exception:
                tr.fail("visuals")
                raise
            work.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                json.dumps({"key": _media_key(scenes), "items": [m.model_dump() for m in got]})
            )
            clips = sum(1 for v in got if v.kind == "video")
            tr.finish("visuals", f"{clips} video clips, {n - clips} images")
            return got

        audio, visuals = await _gather_or_cancel_siblings(make_audio(), make_visuals())

        _update(job, step="Rendering video", progress=45)
        tr.start("clips", "Preparing scene clips")
        loop = asyncio.get_running_loop()

        def on_render(pct: int) -> None:
            if cancel.is_set():
                return
            job.progress = 45 + int(pct * 0.5)
            if pct < 50:
                tr.start("clips")
                tr.update("clips", percent=pct * 2, detail=f"{pct * 2}% of scene clips")
            elif pct < 92:
                tr.start("merge")
                tr.update("merge", percent=int((pct - 50) / 42 * 100), detail="Crossfading scenes together")
            else:
                tr.start("finish")
                tr.update("finish", percent=int((pct - 92) / 8 * 100), detail="Normalising audio and saving")

        starts = await loop.run_in_executor(
            None,
            lambda: render.render_video(
                audio, visuals, job.settings, out_path, work / "tmp",
                on_progress=on_render, cancel=cancel,
            ),
        )
        tr.finish("clips", f"{n} scene clips")
        tr.finish("merge", f"{max(n - 1, 0)} transitions")
        tr.finish("finish", "Final video saved")

        _update(job, step="Writing SEO", progress=96)
        tr.start("seo")
        try:
            seo = await seo_svc.generate_seo(job.script, job.settings, starts, model)
            tr.finish("seo")
        except Exception:
            log.exception("SEO generation failed for %s", job_id)
            seo = None
            tr.finish("seo", "Skipped: could not generate (the video is fine)")
        shutil.rmtree(work / "tmp", ignore_errors=True)
        _update(job, seo=seo, status="done", step="Done", progress=100)
    except (asyncio.CancelledError, render.RenderCancelled):
        cancel.set()
        _finish_cancelled(job)
    except tts.VoiceUnavailableError as exc:
        log.warning("voice unavailable for %s: %s", job_id, exc)
        _settle_failure(tr)
        _update(job, status="script_ready", step="Voice unavailable", progress=0, error=str(exc))
    except Exception as exc:
        log.exception("render failed for %s", job_id)
        _settle_failure(tr)
        _update(job, status="failed", step="Render failed", error=str(exc))


def save_script(job: Job, script: Script) -> Job:
    _update(job, script=script)
    return job
