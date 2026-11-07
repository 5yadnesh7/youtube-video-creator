import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from app.config import settings
from app.jobs import store
from app.schemas import CreateJobRequest, Job, RenderRequest, Research, SceneMedia, Script
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

_tasks: set[asyncio.Task] = set()


def _spawn(coro: Awaitable) -> None:
    task = asyncio.ensure_future(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


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
    store.save(job)
    _spawn(_generate_script(job.id))
    return job


def regenerate(job: Job) -> Job:
    needs_research = not (job.research and job.research.grounded)
    _update(
        job,
        status="scripting",
        step="Researching topic" if needs_research else "Writing script",
        progress=0,
        error=None,
        seo=None,
    )
    _spawn(_generate_script(job.id))
    return job


def start_render(job: Job, req: RenderRequest) -> Job:
    if req.voice_id:
        job.settings.voice_id = req.voice_id
    _update(job, status="rendering", step="Starting", progress=0, error=None)
    _spawn(_render(job.id, req.reuse_media))
    return job


async def _resolve_model(job: Job) -> str:
    return job.settings.model or await openrouter.default_model()


async def _generate_script(job_id: str) -> None:
    job = store.get(job_id)
    if not job:
        return
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
            _update(job, research=research, step="Writing script")
        script = await script_svc.generate_script(
            job.topic, job.settings, model, research=job.research
        )
        _update(job, script=script, status="script_ready", step="Script ready", progress=100)
    except Exception as exc:
        log.exception("script generation failed for %s", job_id)
        _update(job, status="failed", step="Script generation failed", error=str(exc))


def _scaled(job: Job, lo: int, hi: int) -> Callable[[int, int], None]:
    def cb(done: int, total: int) -> None:
        job.progress = lo + int((hi - lo) * done / max(total, 1))
        store.save(job)

    return cb


def _load_media_manifest(path: Path, count: int) -> list[SceneMedia] | None:
    try:
        items = [SceneMedia.model_validate(m) for m in json.loads(path.read_text())]
    except (OSError, ValueError):
        return None
    if len(items) != count or not all(Path(m.path).exists() for m in items):
        return None
    return items


async def _render(job_id: str, reuse_media: bool = False) -> None:
    job = store.get(job_id)
    if not job or not job.script:
        return
    work = settings.jobs_dir / job_id
    out_path = store.video_path(job_id)
    try:
        model = await _resolve_model(job)
        scenes = job.script.scenes

        _update(job, step="Generating voiceover", progress=2)
        audio = await tts.synthesize_scenes(
            scenes, job.settings, work / "audio", model, on_progress=_scaled(job, 2, 25)
        )

        manifest = work / "media.json"
        visuals = _load_media_manifest(manifest, len(scenes)) if reuse_media else None
        if visuals is None:
            _update(job, step="Finding visuals", progress=25)
            visuals = await media.fetch_media(
                scenes,
                [a.duration for a in audio],
                job.settings,
                work / "media",
                on_progress=_scaled(job, 25, 45),
            )
            manifest.write_text(json.dumps([m.model_dump() for m in visuals]))

        _update(job, step="Rendering video", progress=45)
        loop = asyncio.get_running_loop()

        def on_render(pct: int) -> None:
            job.progress = 45 + int(pct * 0.5)
            store.save(job)

        starts = await loop.run_in_executor(
            None,
            lambda: render.render_video(
                audio, visuals, job.settings, out_path, work / "tmp", on_progress=on_render
            ),
        )

        _update(job, step="Writing SEO", progress=96)
        try:
            seo = await seo_svc.generate_seo(job.script, job.settings, starts, model)
        except Exception:
            log.exception("SEO generation failed for %s", job_id)
            seo = None
        shutil.rmtree(work / "tmp", ignore_errors=True)
        _update(job, seo=seo, status="done", step="Done", progress=100)
    except Exception as exc:
        log.exception("render failed for %s", job_id)
        _update(job, status="failed", step="Render failed", error=str(exc))


def save_script(job: Job, script: Script) -> Job:
    _update(job, script=script)
    return job
