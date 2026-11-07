import json
import subprocess
from pathlib import Path

import pytest

from app.schemas import JobSettings, SceneAudio, SceneMedia
from app.services.render import render_video


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def assets(tmp_path_factory):
    d = tmp_path_factory.mktemp("assets")
    vids = []
    for i, (size, secs) in enumerate([("640x360", 3), ("360x640", 1), ("800x800", 4)]):
        p = d / f"v{i}.mp4"
        _ff("-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={secs}",
            "-pix_fmt", "yuv420p", str(p))
        vids.append(p)
    img = d / "img.png"
    _ff("-f", "lavfi", "-i", "testsrc=size=500x300", "-frames:v", "1", str(img))
    auds = []
    for i, secs in enumerate([2.0, 2.5, 1.5, 2.2]):
        p = d / f"a{i}.wav"
        _ff("-f", "lavfi", "-i", f"sine=frequency={300 + 100 * i}:duration={secs}", str(p))
        auds.append((p, secs))
    music = d / "music.mp3"
    _ff("-f", "lavfi", "-i", "sine=frequency=100:duration=1", str(music))
    return vids, img, auds, music


def _build(assets, n):
    vids, img, auds, _ = assets
    media = [
        SceneMedia(path=str(vids[0]), kind="video"),
        SceneMedia(path=str(img), kind="image"),
        SceneMedia(path=str(vids[1]), kind="video"),  # shorter than audio -> must loop
        SceneMedia(path=str(vids[2]), kind="video"),
    ][:n]
    audio = [SceneAudio(path=str(p), duration=s) for p, s in auds[:n]]
    return audio, media


def _check(out: Path, w: int, h: int, expected: float):
    info = _probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"]) == (w, h)
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
    assert a["codec_name"] == "aac"
    assert abs(float(info["format"]["duration"]) - expected) <= 0.5


def test_vertical_four_scenes(assets, tmp_path):
    audio, media = _build(assets, 4)
    progress: list[int] = []
    out = tmp_path / "out.mp4"
    starts = render_video(audio, media, JobSettings(format="vertical"), out,
                          tmp_path / "work", progress.append)
    _check(out, 1080, 1920, sum(a.duration for a in audio))
    assert len(starts) == 4 and starts[0] == 0
    assert all(b > a for a, b in zip(starts, starts[1:]))
    assert progress == sorted(progress) and progress[-1] == 100


def test_horizontal_three_scenes_with_music(assets, tmp_path):
    audio, media = _build(assets, 3)
    out = tmp_path / "out.mp4"
    starts = render_video(audio, media, JobSettings(format="horizontal"), out,
                          tmp_path / "work", music_path=assets[3])
    _check(out, 1920, 1080, sum(a.duration for a in audio))
    assert all(b > a for a, b in zip(starts, starts[1:]))


def test_mismatched_inputs_raise(assets, tmp_path):
    audio, media = _build(assets, 3)
    with pytest.raises(ValueError):
        render_video(audio, media[:2], JobSettings(), tmp_path / "o.mp4", tmp_path / "w")
