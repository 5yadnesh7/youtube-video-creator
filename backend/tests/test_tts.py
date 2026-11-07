import asyncio
import subprocess
from pathlib import Path

import httpx
import pytest

from app.schemas import JobSettings, Scene
from app.services import tts


def _make_mp3(path: Path, secs: float = 1.0):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=d={secs}", str(path)],
        check=True,
    )


@pytest.fixture
def fake_edge(monkeypatch):
    calls = []

    async def fake(text, voice, dst):
        calls.append((text, voice))
        _make_mp3(dst, 1.0)

    monkeypatch.setattr(tts, "_edge_tts", fake)
    return calls


def test_edge_fallback_no_key(tmp_path, monkeypatch, fake_edge):
    monkeypatch.setattr(tts.app_settings, "openrouter_api_key", "")
    progress = []
    scenes = [Scene(narration="hi", visual_query="x"), Scene(narration="yo", visual_query="y")]
    res = asyncio.run(
        tts.synthesize_scenes(
            scenes, JobSettings(voice="female"), tmp_path / "o",
            on_progress=lambda d, t: progress.append((d, t)),
        )
    )
    assert len(res) == 2 and all(Path(r.path).is_absolute() and r.duration > 0.9 for r in res)
    assert progress[-1] == (2, 2)
    assert {v for _, v in fake_edge} == {"en-US-JennyNeural"}


def test_hinglish_uses_edge_hindi(tmp_path, monkeypatch, fake_edge):
    monkeypatch.setattr(tts.app_settings, "openrouter_api_key", "k")
    called = []

    async def boom(*a, **k):
        called.append(1)
        raise AssertionError("OpenRouter should be skipped for hinglish")

    monkeypatch.setattr(tts, "_openrouter_tts", boom)
    asyncio.run(
        tts.synthesize_scenes(
            [Scene(narration="namaste", visual_query="x")],
            JobSettings(language="hinglish", voice="male"), tmp_path,
        )
    )
    assert not called and fake_edge[0][1] == "hi-IN-MadhurNeural"


def test_openrouter_failure_falls_back(tmp_path, monkeypatch, fake_edge):
    monkeypatch.setattr(tts.app_settings, "openrouter_api_key", "k")
    tts._openrouter_disabled.clear()

    async def fail(text, model, voice, dst):
        raise RuntimeError("OpenRouter 429")

    monkeypatch.setattr(tts, "_openrouter_tts", fail)
    res = asyncio.run(
        tts.synthesize_scenes(
            [Scene(narration="a", visual_query="x")], JobSettings(voice_id="heygen:bram"), tmp_path
        )
    )
    assert res[0].duration > 0.9 and len(fake_edge) == 1


def test_openrouter_success(tmp_path, monkeypatch, fake_edge):
    monkeypatch.setattr(tts.app_settings, "openrouter_api_key", "k")
    tts._openrouter_disabled.clear()

    async def ok(text, model, voice, dst):
        _make_mp3(dst, 2.0)

    monkeypatch.setattr(tts, "_openrouter_tts", ok)
    res = asyncio.run(
        tts.synthesize_scenes(
            [Scene(narration="a", visual_query="x")], JobSettings(voice_id="heygen:bram"), tmp_path
        )
    )
    assert 1.9 < res[0].duration < 2.2 and not fake_edge


def test_explicit_voice_is_used_first(tmp_path, monkeypatch, fake_edge):
    monkeypatch.setattr(tts.app_settings, "openrouter_api_key", "k")
    tts._openrouter_disabled.clear()
    seen = []

    async def ok(text, model, voice, dst):
        seen.append((model["id"], voice))
        _make_mp3(dst, 1.0)

    monkeypatch.setattr(tts, "_openrouter_tts", ok)
    asyncio.run(
        tts.synthesize_scenes(
            [Scene(narration="a", visual_query="x")],
            JobSettings(voice_id="heygen:gareth"), tmp_path,
        )
    )
    assert seen == [("heygen/heygen-voice-1-turbo:free", "gareth")]


def test_explicit_edge_voice_skips_openrouter(tmp_path, monkeypatch, fake_edge):
    monkeypatch.setattr(tts.app_settings, "openrouter_api_key", "k")

    async def boom(*a, **k):
        raise AssertionError("OpenRouter must not be called for an edge voice")

    monkeypatch.setattr(tts, "_openrouter_tts", boom)
    asyncio.run(
        tts.synthesize_scenes(
            [Scene(narration="a", visual_query="x")],
            JobSettings(voice_id="edge:en-GB-RyanNeural"), tmp_path,
        )
    )
    assert fake_edge[0][1] == "en-GB-RyanNeural"


def test_parse_voice_id_rejects_unknown_and_traversal():
    assert tts.parse_voice_id("edge:en-US-GuyNeural") == ("edge", "en-US-GuyNeural")
    assert tts.parse_voice_id("heygen:bram") == ("heygen", "bram")
    for bad in (None, "", "bram", "heygen:nobody", "edge:../../x", "nope:bram"):
        assert tts.parse_voice_id(bad) is None


def test_voice_catalog_and_default(monkeypatch):
    monkeypatch.setattr(tts.app_settings, "openrouter_api_key", "k")
    ids = {v["id"] for v in tts.list_voices("english")}
    assert {"heygen:bram", "heygen:nadine", "edge:en-US-GuyNeural"} <= ids
    assert tts.default_voice_id("english", "male") == "edge:en-US-GuyNeural"
    assert tts.default_voice_id("hinglish", "female") == "edge:hi-IN-SwaraNeural"
    assert all(v["experimental"] for v in tts.list_voices("hinglish") if v["provider"] != "edge")


def test_pcm_rate_comes_from_content_type(tmp_path, monkeypatch):
    monkeypatch.setattr(tts.app_settings, "openrouter_api_key", "k")
    pcm = b"\x00\x10" * 44100
    rates = []

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            return httpx.Response(200, content=pcm, headers={"content-type": "audio/pcm;rate=44100;channels=1"})

    real_to_mp3 = tts._to_mp3

    async def spy(src, dst, pcm_rate=None):
        rates.append(pcm_rate)
        await real_to_mp3(src, dst, pcm_rate)

    monkeypatch.setattr(tts.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(tts, "_to_mp3", spy)
    dst = tmp_path / "a.mp3"
    asyncio.run(tts._openrouter_tts("hi", tts.OPENROUTER_MODELS["heygen"], "bram", dst))
    assert rates == [44100]
    assert 0.9 < asyncio.run(tts.probe_duration(dst)) < 1.2


def test_pcm_conversion(tmp_path):
    pcm = tmp_path / "a.raw"
    pcm.write_bytes(b"\x00\x10" * 24000)
    dst = tmp_path / "a.mp3"
    asyncio.run(tts._to_mp3(pcm, dst, 24000))
    assert 0.9 < asyncio.run(tts.probe_duration(dst)) < 1.2


def test_probe_bad_file(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"nope")
    assert asyncio.run(tts.probe_duration(bad)) == 0.0


@pytest.mark.parametrize("lang", ["english", "hinglish"])
@pytest.mark.parametrize("voice", ["male", "female"])
def test_real_edge_smoke(tmp_path, monkeypatch, lang, voice):
    monkeypatch.setattr(tts.app_settings, "openrouter_api_key", "")
    text = (
        "Hello, this is a short test of the narration."
        if lang == "english"
        else "Namaste dosto, aaj hum ek mazedaar topic dekhenge."
    )
    res = asyncio.run(
        tts.synthesize_scenes(
            [Scene(narration=text, visual_query="x")],
            JobSettings(language=lang, voice=voice), tmp_path,
        )
    )
    print(f"SMOKE {lang} {voice}: {res[0].duration:.2f}s")
    assert res[0].duration > 0.5


def test_cap_duration_trims_long_audio_and_keeps_short(tmp_path):
    long_f, short_f = tmp_path / "long.mp3", tmp_path / "short.mp3"
    _make_mp3(long_f, 8.0)
    _make_mp3(short_f, 2.0)
    asyncio.run(tts._cap_duration(long_f, 5.0))
    asyncio.run(tts._cap_duration(short_f, 5.0))
    assert 4.8 < asyncio.run(tts.probe_duration(long_f)) <= 5.15
    assert 1.9 < asyncio.run(tts.probe_duration(short_f)) < 2.2


def test_preview_is_capped_and_leaves_no_temp_files(tmp_path, monkeypatch):
    async def fake_edge(text, voice, dst):
        _make_mp3(dst, 9.0)

    monkeypatch.setattr(tts, "_edge_tts", fake_edge)
    dst = tmp_path / "p" / "v2_english_edge_en-US-GuyNeural.mp3"
    asyncio.run(tts.synthesize_preview("edge:en-US-GuyNeural", "english", dst))
    assert asyncio.run(tts.probe_duration(dst)) <= 5.15
    assert [p.name for p in dst.parent.iterdir()] == [dst.name]


def _daily_limit_response():
    return httpx.Response(
        429,
        json={"error": {"message": "Rate limit exceeded: free-models-per-day", "metadata": {
            "headers": {"X-RateLimit-Reset": "1794096000000"}}}},
    )


def test_daily_limit_skips_openrouter_and_falls_back_to_edge(tmp_path, monkeypatch, fake_edge):
    from app.services import openrouter

    monkeypatch.setattr(tts.app_settings, "openrouter_api_key", "k")
    monkeypatch.setattr(openrouter, "_daily_block", None)
    calls = []

    async def limited(text, model, voice, dst):
        calls.append(model["id"])
        raise openrouter.note_daily_limit(_daily_limit_response())

    monkeypatch.setattr(tts, "_openrouter_tts", limited)
    scenes = [Scene(narration=n, visual_query="x") for n in ("a", "b", "c")]
    res = asyncio.run(
        tts.synthesize_scenes(scenes, JobSettings(voice_id="heygen:bram"), tmp_path)
    )
    assert len(res) == 3 and len(fake_edge) == 3
    assert len(calls) <= 3  # concurrent first wave only; later scenes skip OpenRouter entirely
    assert openrouter.daily_limit_error() is not None
    monkeypatch.setattr(openrouter, "_daily_block", None)


def test_note_daily_limit_ignores_other_429s(monkeypatch):
    from app.services import openrouter

    monkeypatch.setattr(openrouter, "_daily_block", None)
    assert openrouter.note_daily_limit(httpx.Response(429, text="slow down")) is None
    assert openrouter.daily_limit_error() is None
    err = openrouter.note_daily_limit(_daily_limit_response())
    assert "daily limit" in str(err) and openrouter.daily_limit_error() is not None
    monkeypatch.setattr(openrouter, "_daily_block", None)
