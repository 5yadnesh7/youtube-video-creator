import asyncio
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
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
    await openrouter.throttle()
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
        minute_wait = openrouter.minute_limit_wait(resp)
        if minute_wait is not None:
            raise openrouter.RateLimitError(minute_wait)
        if resp.status_code == 404:
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


class VoiceUnavailableError(RuntimeError):
    """The voice the user picked cannot be generated; it is never swapped silently for another voice."""


VOICE_ATTEMPTS = 5


async def _devanagari_batch(texts: list[str], model: Optional[str]) -> list[str]:
    try:
        from app.services.script import to_devanagari_batch

        return await to_devanagari_batch(texts, model)
    except Exception as e:
        log.warning("Devanagari conversion unavailable, using original text: %s", e)
        return list(texts)


async def _speak_openrouter(text: str, chosen: tuple[str, str], dst: Path) -> None:
    provider, voice = chosen
    model = OPENROUTER_MODELS[provider]
    name = f"{model['label']} voice '{voice}'"
    if not app_settings.openrouter_api_key:
        raise VoiceUnavailableError(f"{name} needs OPENROUTER_API_KEY. Pick a Microsoft Edge voice instead.")
    if _openrouter_disabled.get(model["id"]):
        raise VoiceUnavailableError(f"{name} is no longer available on OpenRouter. Pick another voice.")
    blocked = openrouter.daily_limit_error()
    if blocked:
        raise VoiceUnavailableError(f"{name} cannot be used right now: {blocked}")
    last: Optional[Exception] = None
    for attempt in range(VOICE_ATTEMPTS):
        try:
            await _openrouter_tts(text, model, voice, dst)
            return
        except openrouter.DailyLimitError as e:
            raise VoiceUnavailableError(f"{name} stopped: {e}") from e
        except openrouter.RateLimitError as e:
            last = e
            log.warning("%s rate limited, waiting %.0fs", name, e.wait)
            await asyncio.sleep(e.wait)
        except Exception as e:
            last = e
            log.warning("%s attempt %d failed: %s", name, attempt + 1, e)
            await asyncio.sleep(1.5 * (attempt + 1))
    raise VoiceUnavailableError(
        f"{name} could not be generated ({last}). "
        "Pick another voice, for example a Microsoft Edge voice, and generate again."
    )


async def _speak(
    text: str, language: str, gender: str, voice_id: Optional[str], dst: Path
) -> None:
    chosen = parse_voice_id(voice_id)
    if chosen and chosen[0] != "edge":
        await _speak_openrouter(text, chosen, dst)
        return

    edge_voice = chosen[1] if chosen else EDGE_VOICES[(language, gender)]
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


def _scene_path(out_dir: Path, i: int, cfg: JobSettings, spoken: str) -> Path:
    key = hashlib.sha1(f"{cfg.language}|{cfg.voice_id or cfg.voice}|{spoken}".encode()).hexdigest()[:10]
    return out_dir / f"scene_{i:03d}_{key}.mp3"


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
    spoken = [s.narration for s in scenes]
    if settings.language == "hinglish":
        spoken = await _devanagari_batch(spoken, model or settings.model)
    # Keyed by text + voice so a retry after a failure reuses finished scenes.
    paths = [_scene_path(out_dir, i, settings, t) for i, t in enumerate(spoken)]
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def work(i: int) -> SceneAudio:
        nonlocal done
        dst = paths[i]
        async with sem:
            duration = await probe_duration(dst) if dst.exists() else 0.0
            if duration < MIN_DURATION:
                await _speak(spoken[i], settings.language, settings.voice, settings.voice_id, dst)
                duration = await probe_duration(dst)
        done += 1
        if on_progress:
            on_progress(done, total)
        return SceneAudio(path=str(dst.resolve()), duration=duration)

    tasks = [asyncio.ensure_future(work(i)) for i in range(total)]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    keep = {p.name for p in paths}
    for f in out_dir.glob("scene_*.mp3"):
        if f.name not in keep:
            f.unlink(missing_ok=True)
    return list(results)


@dataclass
class VoiceFallback:
    voice_id: str
    label: str
    reason: str


def fallback_voice_id(language: str, chosen: tuple[str, str], default_gender: str) -> str:
    provider, voice = chosen
    gender = OPENROUTER_MODELS[provider]["voices"].get(voice, "unknown")
    if gender not in ("male", "female"):
        gender = default_gender
    return f"edge:{EDGE_VOICES[(language, gender)]}"


def voice_label(voice_id: str, language: str) -> str:
    for v in list_voices(language):
        if v["id"] == voice_id:
            return v["name"]
    return voice_id


async def synthesize_scenes_with_fallback(
    scenes: list[Scene],
    settings: JobSettings,
    out_dir: Path,
    model: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[list[SceneAudio], Optional[VoiceFallback]]:
    """Like synthesize_scenes, but if the chosen OpenRouter voice is unavailable the WHOLE video is
    voiced again with one Edge voice, so a video never mixes two voices."""
    try:
        return await synthesize_scenes(scenes, settings, out_dir, model, on_progress), None
    except VoiceUnavailableError as exc:
        chosen = parse_voice_id(settings.voice_id)
        if not app_settings.tts_fallback_to_edge or chosen is None or chosen[0] == "edge":
            raise
        fallback_id = fallback_voice_id(settings.language, chosen, settings.voice)
        log.warning("voice %s unavailable (%s); voicing the whole video with %s", settings.voice_id, exc, fallback_id)
        cfg = settings.model_copy(update={"voice_id": fallback_id})
        audio = await synthesize_scenes(scenes, cfg, out_dir, model, on_progress)
        return audio, VoiceFallback(fallback_id, voice_label(fallback_id, settings.language), str(exc))
