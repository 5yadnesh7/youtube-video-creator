from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings
from app.jobs import runner, store
from app.schemas import (
    CreateJobRequest,
    Job,
    Language,
    ModelsResponse,
    RenderRequest,
    Trend,
    UpdateScriptRequest,
    VoiceGender,
    VoicePreviewRequest,
    VoicesResponse,
)
from app.services import openrouter, trends, tts

router = APIRouter(prefix="/api")


def _job_or_404(job_id: str) -> Job:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/models", response_model=ModelsResponse)
async def models() -> ModelsResponse:
    return ModelsResponse(
        models=await openrouter.list_free_models(),
        default=await openrouter.default_model(),
    )


@router.get("/trends", response_model=list[Trend])
async def get_trends(force: bool = False) -> list[Trend]:
    try:
        return await trends.get_top_trends(limit=10, force=force)
    except Exception as exc:
        raise HTTPException(502, f"Could not fetch trends: {exc}") from exc


@router.post("/jobs", response_model=Job)
async def create_job(req: CreateJobRequest) -> Job:
    if not req.topic.strip():
        raise HTTPException(422, "Topic is required")
    return runner.create(req)


@router.get("/jobs", response_model=list[Job])
async def list_jobs() -> list[Job]:
    return store.list_all()


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    return _job_or_404(job_id)


@router.put("/jobs/{job_id}/script", response_model=Job)
async def update_script(job_id: str, req: UpdateScriptRequest) -> Job:
    job = _job_or_404(job_id)
    if job.status in ("scripting", "rendering"):
        raise HTTPException(409, f"Cannot edit script while {job.status}")
    if not req.script.scenes:
        raise HTTPException(422, "Script needs at least one scene")
    return runner.save_script(job, req.script)


@router.post("/jobs/{job_id}/script/regenerate", response_model=Job)
async def regenerate_script(job_id: str) -> Job:
    job = _job_or_404(job_id)
    if job.status in ("scripting", "rendering"):
        raise HTTPException(409, f"Job is busy ({job.status})")
    return runner.regenerate(job)


@router.post("/jobs/{job_id}/render", response_model=Job)
async def render_job(job_id: str, req: Optional[RenderRequest] = None) -> Job:
    job = _job_or_404(job_id)
    if job.status in ("scripting", "rendering"):
        raise HTTPException(409, f"Job is busy ({job.status})")
    if not job.script or not job.script.scenes:
        raise HTTPException(409, "No script to render")
    req = req or RenderRequest()
    if req.voice_id and tts.parse_voice_id(req.voice_id) is None:
        raise HTTPException(422, f"Unknown voice: {req.voice_id}")
    return runner.start_render(job, req)


@router.post("/jobs/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: str) -> Job:
    job = _job_or_404(job_id)
    if job.status not in ("scripting", "rendering"):
        raise HTTPException(409, f"Nothing to stop: the job is {job.status}")
    return runner.cancel(job)


@router.get("/voices", response_model=VoicesResponse)
async def voices(language: Language = "english", gender: VoiceGender = "male") -> VoicesResponse:
    return VoicesResponse(
        voices=tts.list_voices(language),
        default_id=tts.default_voice_id(language, gender),
    )


@router.post("/voices/preview")
async def voice_preview(req: VoicePreviewRequest) -> FileResponse:
    if tts.parse_voice_id(req.voice_id) is None:
        raise HTTPException(422, f"Unknown voice: {req.voice_id}")
    dst = settings.data_dir / "previews" / f"v2_{req.language}_{req.voice_id.replace(':', '_')}.mp3"
    if not dst.exists():
        try:
            await tts.synthesize_preview(req.voice_id, req.language, dst)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Voice preview failed: {exc}") from exc
    return FileResponse(dst, media_type="audio/mpeg")


@router.get("/jobs/{job_id}/video")
async def get_video(job_id: str, download: bool = False) -> FileResponse:
    _job_or_404(job_id)
    path = store.video_path(job_id)
    if not path.exists():
        raise HTTPException(404, "Video not ready")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"video-{job_id}.mp4" if download else None,
    )
