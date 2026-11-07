import asyncio
import logging
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from app.config import settings as app_settings
from app.schemas import JobSettings, MediaType, Scene, SceneMedia

log = logging.getLogger(__name__)

PEXELS_VIDEOS = "https://api.pexels.com/videos/search"
PEXELS_PHOTOS = "https://api.pexels.com/v1/search"
PIXABAY_VIDEOS = "https://pixabay.com/api/videos/"
PIXABAY_PHOTOS = "https://pixabay.com/api/"

PER_PAGE = 15
CONCURRENCY = 3
MAX_ATTEMPTS = 4
BACKOFF_BASE = 1.0
TOP_PICK = 5
GENERIC_QUERY = "abstract background"

_MOOD_COLORS = {
    "dramatic": ("0x1b0a2a", "0x7a1f3d"),
    "calm": ("0x0f2740", "0x2a7f8e"),
    "energetic": ("0xff5e00", "0x8a00c2"),
    "dark": ("0x050510", "0x20204a"),
    "happy": ("0xffb347", "0xff6f91"),
}
_DEFAULT_COLORS = ("0x141e30", "0x35577d")


@dataclass
class _Candidate:
    key: str
    url: str
    ext: str
    kind: MediaType
    source: str
    duration: float = 0.0


def _client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True)


def _target(fmt: str) -> tuple[int, int]:
    return (1080, 1920) if fmt == "vertical" else (1920, 1080)


def _orientation_ok(w: int, h: int, fmt: str) -> bool:
    if not w or not h:
        return True
    return h >= w if fmt == "vertical" else w >= h


def _res_distance(w: int, h: int, fmt: str) -> int:
    tw, th = _target(fmt)
    return abs(w - tw) + abs(h - th)


def _query_chain(scene: Scene) -> list[str]:
    q = " ".join(scene.visual_query.split())
    chain = [q, " ".join(q.split()[:2]), scene.mood.strip(), GENERIC_QUERY]
    seen, out = set(), []
    for item in chain:
        if item and item.lower() not in seen and item.lower() != "neutral":
            seen.add(item.lower())
            out.append(item)
    return out


async def _get(client: httpx.AsyncClient, url: str, **kw) -> Optional[httpx.Response]:
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = await client.get(url, **kw)
        except httpx.TransportError as e:
            log.warning("request error %s: %s", url, e)
            resp = None
        if resp is not None and resp.status_code != 429 and resp.status_code < 500:
            return resp
        if attempt < MAX_ATTEMPTS - 1:
            await asyncio.sleep(BACKOFF_BASE * (2**attempt))
    return None


async def _json(client, url, **kw) -> dict:
    resp = await _get(client, url, **kw)
    if resp is None or resp.status_code != 200:
        if resp is not None:
            log.warning("%s -> HTTP %s", url, resp.status_code)
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


def _pick_video_file(files: list[dict], fmt: str) -> Optional[dict]:
    usable = [f for f in files if f.get("link") and (f.get("width") or 0) and (f.get("height") or 0)]
    usable = [f for f in usable if "mp4" in (f.get("file_type") or "video/mp4")]
    if not usable:
        return None
    sane = [f for f in usable if max(f["width"], f["height"]) <= 1920]
    pool = sane or [min(usable, key=lambda f: f["width"] * f["height"])]
    return min(pool, key=lambda f: _res_distance(f["width"], f["height"], fmt))


async def _pexels_videos(client, query, fmt, key) -> list[_Candidate]:
    data = await _json(
        client, PEXELS_VIDEOS,
        params={"query": query, "per_page": PER_PAGE,
                "orientation": "portrait" if fmt == "vertical" else "landscape"},
        headers={"Authorization": key},
    )
    out = []
    for v in data.get("videos", []):
        if not _orientation_ok(v.get("width", 0), v.get("height", 0), fmt):
            continue
        f = _pick_video_file(v.get("video_files", []), fmt)
        if f:
            out.append(_Candidate(f"pexels:video:{v['id']}", f["link"], ".mp4", "video", "pexels",
                                  float(v.get("duration") or 0)))
    return out


async def _pexels_photos(client, query, fmt, key) -> list[_Candidate]:
    data = await _json(
        client, PEXELS_PHOTOS,
        params={"query": query, "per_page": PER_PAGE,
                "orientation": "portrait" if fmt == "vertical" else "landscape"},
        headers={"Authorization": key},
    )
    out = []
    for p in data.get("photos", []):
        if not _orientation_ok(p.get("width", 0), p.get("height", 0), fmt):
            continue
        src = p.get("src", {})
        url = src.get("large2x") or src.get("large") or src.get("original")
        if url:
            out.append(_Candidate(f"pexels:image:{p['id']}", url, ".jpg", "image", "pexels"))
    return out


async def _pixabay_videos(client, query, fmt, key) -> list[_Candidate]:
    data = await _json(client, PIXABAY_VIDEOS,
                       params={"key": key, "q": query, "per_page": PER_PAGE, "safesearch": "true"})
    out = []
    for h in data.get("hits", []):
        files = []
        for v in (h.get("videos") or {}).values():
            if v.get("url"):
                files.append({"link": v["url"], "width": v.get("width", 0),
                              "height": v.get("height", 0), "file_type": "video/mp4"})
        files = [f for f in files if _orientation_ok(f["width"], f["height"], fmt)]
        f = _pick_video_file(files, fmt)
        if f:
            out.append(_Candidate(f"pixabay:video:{h['id']}", f["link"], ".mp4", "video", "pixabay",
                                  float(h.get("duration") or 0)))
    return out


async def _pixabay_photos(client, query, fmt, key) -> list[_Candidate]:
    data = await _json(client, PIXABAY_PHOTOS, params={
        "key": key, "q": query, "per_page": PER_PAGE, "image_type": "photo", "safesearch": "true",
        "orientation": "vertical" if fmt == "vertical" else "horizontal"})
    out = []
    for h in data.get("hits", []):
        if not _orientation_ok(h.get("imageWidth", 0), h.get("imageHeight", 0), fmt):
            continue
        url = h.get("largeImageURL") or h.get("webformatURL")
        if url:
            out.append(_Candidate(f"pixabay:image:{h['id']}", url, ".jpg", "image", "pixabay"))
    return out


def _choose(cands: list[_Candidate], used: set[str], needed: float) -> Optional[_Candidate]:
    fresh = [c for c in cands if c.key not in used]
    if not fresh:
        return None
    if fresh[0].kind == "video":
        long_enough = [c for c in fresh if c.duration >= needed]
        pool = long_enough or sorted(fresh, key=lambda c: -c.duration)
    else:
        pool = fresh
    return random.choice(pool[:TOP_PICK])


async def _download(client: httpx.AsyncClient, url: str, dest: Path) -> bool:
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with client.stream("GET", url) as resp:
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.TransportError(f"HTTP {resp.status_code}")
                if resp.status_code != 200:
                    return False
                with open(tmp, "wb") as fh:
                    async for chunk in resp.aiter_bytes(65536):
                        fh.write(chunk)
            if tmp.exists() and tmp.stat().st_size > 0:
                tmp.replace(dest)
                return True
            return False
        except (httpx.TransportError, OSError) as e:
            log.warning("download failed %s: %s", url, e)
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(BACKOFF_BASE * (2**attempt))
    tmp.unlink(missing_ok=True)
    return False


def _make_fallback_sync(dest: Path, fmt: str, mood: str) -> None:
    w, h = _target(fmt)
    c0, c1 = _MOOD_COLORS.get(mood.lower(), _DEFAULT_COLORS)
    base = ["ffmpeg", "-y", "-loglevel", "error"]
    attempts = [
        base + ["-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0={c0}:c1={c1}:x0=0:y0=0:x1={w}:y1={h}:d=1",
                "-frames:v", "1", "-q:v", "2", str(dest)],
        base + ["-f", "lavfi", "-i", f"color=c={c0}:s={w}x{h}", "-frames:v", "1", "-q:v", "2", str(dest)],
    ]
    for cmd in attempts:
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return
    raise RuntimeError("ffmpeg failed to create fallback background")


_SEARCH = {
    ("pexels", "video"): _pexels_videos,
    ("pexels", "image"): _pexels_photos,
    ("pixabay", "video"): _pixabay_videos,
    ("pixabay", "image"): _pixabay_photos,
}


async def _fetch_one(client, idx, scene, needed, fmt, out_dir, used, providers) -> SceneMedia:
    types: list[MediaType] = ["video", "image"] if scene.media_type == "video" else ["image", "video"]
    for query in _query_chain(scene):
        for kind in types:
            for name, key in providers:
                try:
                    cand = _choose(await _SEARCH[(name, kind)](client, query, fmt, key), used, needed)
                except Exception as e:  # a provider hiccup must not break the job
                    log.warning("%s %s search failed: %s", name, kind, e)
                    continue
                if cand is None:
                    continue
                used.add(cand.key)
                dest = out_dir / f"scene_{idx:03d}{cand.ext}"
                if await _download(client, cand.url, dest):
                    return SceneMedia(path=str(dest.resolve()), kind=cand.kind, source=cand.source)
                used.discard(cand.key)
    dest = out_dir / f"scene_{idx:03d}_fallback.jpg"
    await asyncio.to_thread(_make_fallback_sync, dest, fmt, scene.mood)
    return SceneMedia(path=str(dest.resolve()), kind="image", source="fallback")


async def fetch_media(
    scenes: list[Scene],
    durations: list[float],
    settings: JobSettings,
    out_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[SceneMedia]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    providers = []
    if app_settings.pexels_api_key:
        providers.append(("pexels", app_settings.pexels_api_key))
    if app_settings.pixabay_api_key:
        providers.append(("pixabay", app_settings.pixabay_api_key))

    total = len(scenes)
    results: list[Optional[SceneMedia]] = [None] * total
    used: set[str] = set()
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async with _client_factory() as client:

        async def run(i: int) -> None:
            nonlocal done
            async with sem:
                needed = durations[i] if i < len(durations) else 5.0
                results[i] = await _fetch_one(client, i, scenes[i], needed, settings.format,
                                              out_dir, used, providers)
            done += 1
            if on_progress:
                on_progress(done, total)

        await asyncio.gather(*(run(i) for i in range(total)))
    return results  # type: ignore[return-value]
