import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Callable, Optional

import httpx

from app.config import settings as app_settings
from app.schemas import JobSettings, Scene, SceneAudio
from app.services import openrouter

log = logging.getLogger(__name__)

CONCURRENCY = 3
MIN_DURATION = 0.3
OPENROUTER_TIMEOUT = 60.0
EDGE_ATTEMPTS = 3

PCM_FALLBACK_RATE = 44100

# Ids/voices verified against openrouter.ai/api/v1/models?output_modalities=speech.
# HeyGen is PCM-only and reports its real rate in the response Content-Type
# ("audio/pcm;rate=44100"); guessing 24 kHz made the voice slow and slurred.
# Hindi support is unverified on both, so Hinglish skips them unless the user
# picks one explicitly.
OPENROUTER_MODELS = {
    "heygen": {
        "id": "heygen/heygen-voice-1-turbo:free",
        "label": "HeyGen Voice Turbo (OpenRouter)",
        "format": "pcm",
        "voices": {
            "bram": "male", "gareth": "male", "lucian": "male", "weston": "male", "vincent": "male",
            "nadine": "female", "tabitha": "female", "noelle": "female", "meredith": "female", "sabrina": "female",
        },
        "auto_voices": {"male": "bram", "female": "nadine"},
        "hindi": False,
    },
    "fish": {
        "id": "fish-audio/s2.1-pro-free:free",
        "label": "Fish Audio S2.1 (OpenRouter)",
        "format": "mp3",
        "voices": {"default": "unknown"},
        "auto_voices": {},
        "hindi": False,
    },
}

EDGE_LABEL = "Microsoft Edge TTS"
EDGE_CATALOG = {
    "english": [
        ("en-US-GuyNeural", "Guy (US)", "male"),
        ("en-US-AndrewNeural", "Andrew (US)", "male"),
        ("en-US-BrianNeural", "Brian (US)", "male"),
        ("en-GB-RyanNeural", "Ryan (UK)", "male"),
        ("en-IN-PrabhatNeural", "Prabhat (India)", "male"),
        ("en-US-JennyNeural", "Jenny (US)", "female"),
        ("en-US-AriaNeural", "Aria (US)", "female"),
        ("en-US-EmmaNeural", "Emma (US)", "female"),
        ("en-GB-SoniaNeural", "Sonia (UK)", "female"),
        ("en-IN-NeerjaNeural", "Neerja (India)", "female"),
    ],
    "hinglish": [
        ("hi-IN-MadhurNeural", "Madhur (Hindi)", "male"),
        ("hi-IN-SwaraNeural", "Swara (Hindi)", "female"),
    ],
}

EDGE_VOICES = {
    ("english", "male"): "en-US-GuyNeural",
    ("english", "female"): "en-US-JennyNeural",
    ("hinglish", "male"): "hi-IN-MadhurNeural",
    ("hinglish", "female"): "hi-IN-SwaraNeural",
}

PREVIEW_MAX_SECONDS = 5.0
PREVIEW_TEXT = {
    "english": "Hi there! This is how I sound.",
    "hinglish": "नमस्ते दोस्तों! यह मेरी आवाज़ है।",
}

# model id -> True once it returned a permanent (400/404) error this process
_openrouter_disabled: dict[str, bool] = {}


def default_voice_id(language: str, gender: str) -> str:
    # OpenRouter voices cost one request per scene against a 50/day free quota,
    # so they are opt-in; edge-tts has no quota.
    return f"edge:{EDGE_VOICES[(language, gender)]}"


def list_voices(language: str) -> list[dict]:
    voices = []
    for provider, m in OPENROUTER_MODELS.items():
        for name, gender in m["voices"].items():
            voices.append({
                "id": f"{provider}:{name}",
                "provider": provider,
                "provider_label": m["label"],
                "name": name.capitalize(),
                "gender": gender,
                "experimental": language == "hinglish" and not m["hindi"],
            })
    for voice, name, gender in EDGE_CATALOG[language]:
        voices.append({
            "id": f"edge:{voice}",
            "provider": "edge",
            "provider_label": EDGE_LABEL,
            "name": name,
            "gender": gender,
            "experimental": False,
        })
    voices.sort(key=lambda v: v["provider"] != "edge")
    return voices


def parse_voice_id(voice_id: Optional[str]) -> Optional[tuple[str, str]]:
    if not voice_id or ":" not in voice_id:
        return None
    provider, voice = voice_id.split(":", 1)
    if provider == "edge" and any(voice == v for vs in EDGE_CATALOG.values() for v, _, _ in vs):
        return provider, voice
    if provider in OPENROUTER_MODELS and voice in OPENROUTER_MODELS[provider]["voices"]:
        return provider, voice
    return None


async def _run(*args: str) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out, err


async def probe_duration(path) -> float:
    try:
        code, out, _ = await _run(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        )
        if code != 0:
            return 0.0
        return float(out.decode().strip())
    except (ValueError, OSError):
        return 0.0


async def _to_mp3(src: Path, dst: Path, pcm_rate: Optional[int] = None) -> None:
    args = ["ffmpeg", "-y", "-v", "error"]
    if pcm_rate:
        args += ["-f", "s16le", "-ar", str(pcm_rate), "-ac", "1"]
    args += ["-i", str(src), "-ar", "44100", "-ac", "1", "-b:a", "128k", str(dst)]
    code, _, err = await _run(*args)
    if code != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace')[:200]}")


async def _validate(path: Path) -> float:
    dur = await probe_duration(path)
    if dur < MIN_DURATION:
        raise RuntimeError(f"audio too short or unreadable ({dur:.2f}s)")
    return dur


async def _openrouter_tts(text: str, model: dict, voice: Optional[str], dst: Path) -> None:
    body = {"model": model["id"], "input": text, "response_format": model["format"]}
    if voice and voice != "default":
        body["voice"] = voice
    async with httpx.AsyncClient(timeout=OPENROUTER_TIMEOUT) as client:
        resp = await client.post(
            f"{app_settings.openrouter_base_url}/audio/speech",
            json=body,
            headers={"Authorization": f"Bearer {app_settings.openrouter_api_key}"},
        )
    if resp.status_code != 200:
        limit = openrouter.note_daily_limit(resp)
        if limit:
            raise limit
        if resp.status_code in (400, 404):
            _openrouter_disabled[model["id"]] = True
        raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:150]}")
    if len(resp.content) < 1000:
        raise RuntimeError("OpenRouter returned empty/tiny body")
    pcm_rate = None
    if model["format"] == "pcm":
        match = re.search(r"rate=(\d+)", resp.headers.get("content-type", ""))
        pcm_rate = int(match.group(1)) if match else PCM_FALLBACK_RATE
    raw = dst.with_suffix(".raw")
    raw.write_bytes(resp.content)
    try:
        await _to_mp3(raw, dst, pcm_rate)
    finally:
        raw.unlink(missing_ok=True)
    await _validate(dst)


async def _edge_tts(text: str, voice: str, dst: Path) -> None:
    import edge_tts

    raw = dst.with_suffix(".edge.mp3")
    try:
        await edge_tts.Communicate(text, voice).save(str(raw))
        await _to_mp3(raw, dst)
    finally:
        raw.unlink(missing_ok=True)
    await _validate(dst)


async def _to_devanagari_safe(text: str, model: Optional[str]) -> str:
    try:
        from app.services.script import to_devanagari

        out = await to_devanagari(text, model)
        return out.strip() or text
    except Exception as e:
        log.info("to_devanagari unavailable, using original text: %s", e)
        return text


def _openrouter_candidates(
    language: str, gender: str, chosen: Optional[tuple[str, str]]
) -> list[tuple[dict, Optional[str]]]:
    if not chosen or chosen[0] == "edge":
        return []
    return [(OPENROUTER_MODELS[chosen[0]], chosen[1])]


async def _speak(
    text: str, language: str, gender: str, voice_id: Optional[str], dst: Path
) -> None:
    chosen = parse_voice_id(voice_id)
    if app_settings.openrouter_api_key and not openrouter.daily_limit_error():
        for m, voice in _openrouter_candidates(language, gender, chosen):
            if _openrouter_disabled.get(m["id"]):
                continue
            try:
                await _openrouter_tts(text, m, voice, dst)
                return
            except openrouter.DailyLimitError as e:
                log.warning("%s; using edge-tts instead", e)
                break
            except Exception as e:
                log.warning("OpenRouter TTS %s failed (%s); trying next", m["id"], e)

    edge_voice = chosen[1] if chosen and chosen[0] == "edge" else EDGE_VOICES[(language, gender)]
    last: Optional[Exception] = None
    for attempt in range(EDGE_ATTEMPTS):
        try:
            await _edge_tts(text, edge_voice, dst)
            return
        except Exception as e:
            last = e
            log.warning("edge-tts attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"TTS failed: {last}")


async def _synthesize_one(
    scene: Scene, cfg: JobSettings, dst: Path, model: Optional[str]
) -> SceneAudio:
    text = scene.narration
    if cfg.language == "hinglish":
        text = await _to_devanagari_safe(text, model)
    await _speak(text, cfg.language, cfg.voice, cfg.voice_id, dst)
    return SceneAudio(path=str(dst.resolve()), duration=await probe_duration(dst))


async def _cap_duration(path: Path, seconds: float) -> None:
    if await probe_duration(path) <= seconds:
        return
    capped = path.with_suffix(".cap.mp3")
    code, _, err = await _run(
        "ffmpeg", "-y", "-v", "error", "-i", str(path), "-t", str(seconds),
        "-af", f"afade=t=out:st={seconds - 0.3}:d=0.3", "-b:a", "128k", str(capped),
    )
    if code != 0:
        capped.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace')[:200]}")
    capped.replace(path)


async def synthesize_preview(voice_id: str, language: str, dst: Path) -> Path:
    chosen = parse_voice_id(voice_id)
    if chosen is None:
        raise ValueError(f"Unknown voice: {voice_id}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = PREVIEW_TEXT[language]
    provider, voice = chosen
    tmp = dst.with_name(f"{dst.stem}.{uuid.uuid4().hex[:8]}.part.mp3")
    try:
        if provider == "edge":
            await _edge_tts(text, voice, tmp)
        else:
            if not app_settings.openrouter_api_key:
                raise RuntimeError("OPENROUTER_API_KEY is not set")
            await _openrouter_tts(text, OPENROUTER_MODELS[provider], voice, tmp)
        await _cap_duration(tmp, PREVIEW_MAX_SECONDS)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst


async def synthesize_scenes(
    scenes: list[Scene],
    settings: JobSettings,
    out_dir: Path,
    model: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> list[SceneAudio]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(scenes)
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def work(i: int, scene: Scene) -> SceneAudio:
        nonlocal done
        async with sem:
            res = await _synthesize_one(
                scene, settings, out_dir / f"scene_{i:03d}.mp3", model or settings.model
            )
        done += 1
        if on_progress:
            on_progress(done, total)
        return res

    return list(await asyncio.gather(*(work(i, s) for i, s in enumerate(scenes))))
