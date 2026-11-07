import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas import JobSettings, Scene  # noqa: E402
from app.services import media  # noqa: E402


def _pexels_video(vid, w, h, dur):
    return {"id": vid, "width": w, "height": h, "duration": dur, "video_files": [
        {"link": f"https://cdn/v{vid}_4k.mp4", "width": 3840, "height": 2160, "file_type": "video/mp4"},
        {"link": f"https://cdn/v{vid}_hd.mp4", "width": w, "height": h, "file_type": "video/mp4"},
        {"link": f"https://cdn/v{vid}_sd.mp4", "width": w // 2, "height": h // 2, "file_type": "video/mp4"},
    ]}


class Fake:
    def __init__(self, handler):
        self.calls = []
        self.handler = handler

    def __call__(self, request: httpx.Request):
        self.calls.append(request)
        if request.url.host == "cdn":
            return httpx.Response(200, content=b"data")
        return self.handler(request)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(media, "BACKOFF_BASE", 0)
    monkeypatch.setattr(media.app_settings, "pexels_api_key", "pk")
    monkeypatch.setattr(media.app_settings, "pixabay_api_key", "xk")


def run(fake, scenes, tmp_path, fmt="vertical", durations=None, monkeypatch=None):
    transport = httpx.MockTransport(fake)
    media._client_factory = lambda: httpx.AsyncClient(transport=transport)
    progress = []
    res = asyncio.run(media.fetch_media(
        scenes, durations or [4.0] * len(scenes), JobSettings(format=fmt), tmp_path / "out",
        lambda d, t: progress.append((d, t))))
    return res, progress


@pytest.fixture(autouse=True)
def restore_factory():
    orig = media._client_factory
    yield
    media._client_factory = orig


def test_pexels_video_orientation_hd_and_progress(tmp_path):
    def h(req):
        assert req.headers["Authorization"] == "pk"
        assert req.url.params["orientation"] == "portrait"
        return httpx.Response(200, json={"videos": [
            _pexels_video(1, 1080, 1920, 10),
            _pexels_video(2, 1920, 1080, 10),  # wrong orientation, must be skipped
        ]})

    fake = Fake(h)
    res, progress = run(fake, [Scene(narration="n", visual_query="city night")], tmp_path)
    assert res[0].source == "pexels" and res[0].kind == "video"
    assert res[0].path.endswith(".mp4") and Path(res[0].path).is_absolute()
    assert Path(res[0].path).stat().st_size > 0
    urls = [str(r.url) for r in fake.calls if r.url.host == "cdn"]
    assert urls == ["https://cdn/v1_hd.mp4"]
    assert progress == [(1, 1)]


def test_horizontal_orientation_param(tmp_path):
    seen = {}

    def h(req):
        seen["o"] = req.url.params["orientation"]
        return httpx.Response(200, json={"photos": [
            {"id": 5, "width": 4000, "height": 3000, "src": {"large2x": "https://cdn/p5.jpg"}}]})

    res, _ = run(Fake(h), [Scene(narration="n", visual_query="x", media_type="image")], tmp_path, fmt="horizontal")
    assert seen["o"] == "landscape" and res[0].kind == "image" and res[0].path.endswith(".jpg")


def test_dedup_across_scenes(tmp_path):
    def h(req):
        if "videos" in req.url.path:
            return httpx.Response(200, json={"videos": [_pexels_video(i, 1080, 1920, 10) for i in (1, 2, 3)]})
        return httpx.Response(200, json={"photos": []})

    scenes = [Scene(narration="n", visual_query="same query") for _ in range(3)]
    res, _ = run(Fake(h), scenes, tmp_path)
    assert len({r.path for r in res}) == 3
    assert all(r.source == "pexels" for r in res)


def test_falls_back_to_pixabay_and_other_type(tmp_path):
    def h(req):
        if req.url.host == "api.pexels.com":
            return httpx.Response(200, json={"videos": [], "photos": []})
        if req.url.path.startswith("/api/videos"):
            return httpx.Response(200, json={"hits": []})
        assert req.url.params["orientation"] == "vertical"
        return httpx.Response(200, json={"hits": [
            {"id": 9, "imageWidth": 4000, "imageHeight": 3000, "largeImageURL": "https://cdn/h.jpg"},  # landscape
            {"id": 10, "imageWidth": 3000, "imageHeight": 4000, "largeImageURL": "https://cdn/v.jpg"}]})

    res, _ = run(Fake(h), [Scene(narration="n", visual_query="a b c")], tmp_path)
    assert (res[0].source, res[0].kind) == ("pixabay", "image")


def test_pixabay_video_ratio_filter(tmp_path):
    def h(req):
        if req.url.host == "api.pexels.com":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"hits": [{"id": 1, "duration": 9, "videos": {
            "large": {"url": "https://cdn/l.mp4", "width": 1920, "height": 1080},
            "medium": {"url": "https://cdn/m.mp4", "width": 1080, "height": 1920}}}]}) \
            if req.url.path.startswith("/api/videos") else httpx.Response(200, json={"hits": []})

    fake = Fake(h)
    res, _ = run(fake, [Scene(narration="n", visual_query="q")], tmp_path)
    assert res[0].source == "pixabay" and res[0].kind == "video"
    assert [str(r.url) for r in fake.calls if r.url.host == "cdn"] == ["https://cdn/m.mp4"]


def test_query_fallback_chain(tmp_path):
    queries = []

    def h(req):
        q = req.url.params.get("query") or req.url.params.get("q")
        queries.append(q)
        if q == "abstract background":
            return httpx.Response(200, json={"videos": [_pexels_video(1, 1080, 1920, 8)]}) \
                if "videos" in req.url.path else httpx.Response(200, json={"photos": []})
        return httpx.Response(200, json={"videos": [], "photos": [], "hits": []})

    res, _ = run(Fake(h), [Scene(narration="n", visual_query="one two three", mood="calm")], tmp_path)
    assert res[0].source == "pexels"
    order = list(dict.fromkeys(queries))
    assert order == ["one two three", "one two", "calm", "abstract background"]


def test_all_keys_missing_uses_ffmpeg_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(media.app_settings, "pexels_api_key", "")
    monkeypatch.setattr(media.app_settings, "pixabay_api_key", "")
    fake = Fake(lambda r: pytest.fail("no HTTP expected"))
    res, progress = run(fake, [Scene(narration="n", visual_query="q")] * 2, tmp_path)
    assert all(r.source == "fallback" and r.kind == "image" for r in res)
    assert all(Path(r.path).stat().st_size > 0 for r in res)
    assert progress[-1] == (2, 2) and not fake.calls


def test_one_key_skips_other_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(media.app_settings, "pexels_api_key", "")

    def h(req):
        assert req.url.host == "pixabay.com"
        return httpx.Response(200, json={"hits": []})

    res, _ = run(Fake(h), [Scene(narration="n", visual_query="q")], tmp_path)
    assert res[0].source == "fallback"


def test_retry_on_429_then_success(tmp_path):
    state = {"n": 0}

    def h(req):
        if "videos" in req.url.path:
            state["n"] += 1
            if state["n"] < 3:
                return httpx.Response(429)
            return httpx.Response(200, json={"videos": [_pexels_video(1, 1080, 1920, 8)]})
        return httpx.Response(200, json={})

    res, _ = run(Fake(h), [Scene(narration="n", visual_query="q")], tmp_path)
    assert res[0].source == "pexels" and state["n"] == 3


def test_prefers_clip_long_enough(tmp_path):
    def h(req):
        return httpx.Response(200, json={"videos": [_pexels_video(1, 1080, 1920, 3),
                                                    _pexels_video(2, 1080, 1920, 20)]})

    for _ in range(5):
        fake = Fake(h)
        run(fake, [Scene(narration="n", visual_query="q")], tmp_path, durations=[10.0])
        assert [str(r.url) for r in fake.calls if r.url.host == "cdn"] == ["https://cdn/v2_hd.mp4"]


@pytest.mark.skipif(not (media.app_settings.pexels_api_key or media.app_settings.pixabay_api_key),
                    reason="no real API keys configured; live smoke test skipped")
def test_real_smoke(tmp_path):
    asyncio.run(asyncio.sleep(0))
    res = asyncio.run(media.fetch_media(
        [Scene(narration="n", visual_query="ocean waves")], [5.0], JobSettings(), tmp_path / "live"))
    assert Path(res[0].path).stat().st_size > 0
