"""ffmpeg based renderer: per-scene clips -> xfade chain -> voice/music mix -> mux.

Timeline model: scene i starts at s_i = sum(audio durations before i). Clip i is
rendered with length d_i + T_i (T_i = overlap with the next scene), and the
xfade into scene i+1 is placed at offset s_{i+1}, so the transition plays during
the first T_i seconds of the next scene's narration. Final length == sum(d_i).
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from app.schemas import JobSettings, SceneAudio, SceneMedia

FPS = 30
TRANSITION_SECONDS = 0.5
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

TRANSITIONS = [
    "fade", "slideleft", "wipeleft", "circleopen", "smoothleft", "zoomin",
    "fadeblack", "dissolve", "slideright", "wiperight", "smoothright",
    "circleclose", "radial", "horzopen", "vertopen", "fadewhite",
    "slideup", "smoothup",
]

MAX_SCENE_WORKERS = 6  # measured: 8 scene clips take 29.7s with 3 workers, 26.2s with 6, no gain beyond
KEN_BURNS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]

_TIME_RE = re.compile(r"^out_time_(?:us|ms)=(\d+)")


class RenderCancelled(Exception):
    pass


def _run_ffmpeg(
    args: list[str],
    expected_seconds: float,
    log_path: Path,
    on_fraction: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    if cancel is not None and cancel.is_set():
        raise RenderCancelled()
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    with open(log_path, "wb") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.wait()
                raise RenderCancelled()
            m = _TIME_RE.match(line.strip())
            if m and on_fraction and expected_seconds > 0:
                on_fraction(min(1.0, int(m.group(1)) / 1e6 / expected_seconds))
        rc = proc.wait()
    if rc != 0:
        tail = log_path.read_text(errors="replace")[-2000:]
        raise RuntimeError(f"ffmpeg failed (exit {rc}): {tail}")


def _ken_burns_filter(mode: str, w: int, h: int, frames: int) -> str:
    n = max(frames, 1)
    zmax = 0.15
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"
    if mode == "zoom_in":
        z, x, y = f"1+{zmax}*on/{n}", cx, cy
    elif mode == "zoom_out":
        z, x, y = f"1+{zmax}-{zmax}*on/{n}", cx, cy
    else:
        z = f"1+{zmax}"
        x, y = cx, cy
        if mode == "pan_left":
            x = f"(iw-iw/zoom)*(1-on/{n})"
        elif mode == "pan_right":
            x = f"(iw-iw/zoom)*on/{n}"
        elif mode == "pan_up":
            y = f"(ih-ih/zoom)*(1-on/{n})"
        else:
            y = f"(ih-ih/zoom)*on/{n}"
    # Upscale 2x first: zoompan rounds crop offsets to integers, which jitters on small sources.
    return (
        f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},setsar=1,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={w}x{h}:fps={FPS},"
        f"setsar=1,format=yuv420p"
    )


def _render_scene(
    idx: int,
    media: SceneMedia,
    length: float,
    w: int,
    h: int,
    out: Path,
    work_dir: Path,
    on_fraction: Callable[[float], None],
    cancel: threading.Event | None = None,
) -> None:
    frames = max(2, math.ceil(length * FPS))
    is_image = media.kind == "image" or Path(media.path).suffix.lower() in IMAGE_EXTS
    enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
           "-pix_fmt", "yuv420p", "-r", str(FPS), "-frames:v", str(frames), str(out)]
    if is_image:
        vf = _ken_burns_filter(KEN_BURNS[idx % len(KEN_BURNS)], w, h, frames)
        args = ["-i", media.path, "-vf", vf, *enc]
    else:
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"setsar=1,fps={FPS},setpts=PTS-STARTPTS,format=yuv420p"
        )
        args = ["-stream_loop", "-1", "-i", media.path, "-t", f"{length:.3f}", "-vf", vf, *enc]
    _run_ffmpeg(args, length, work_dir / f"scene_{idx:03d}.log", on_fraction, cancel)


def _write_script(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def render_video(
    audio: list[SceneAudio],
    media: list[SceneMedia],
    settings: JobSettings,
    out_path: Path,
    work_dir: Path,
    on_progress: Callable[[int], None] | None = None,
    music_path: Optional[Path] = None,  # optional background music hook, mixed ~-20dB under voice
    cancel: threading.Event | None = None,  # set to abort: running ffmpeg is killed, RenderCancelled raised
) -> list[float]:
    if not audio or len(audio) != len(media):
        raise ValueError(f"need equal, non-empty audio/media lists (got {len(audio)}/{len(media)})")

    out_path = Path(out_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = (1080, 1920) if settings.format == "vertical" else (1920, 1080)
    n = len(audio)
    durs = [max(0.3, float(a.duration)) for a in audio]
    starts = [0.0]
    for d in durs[:-1]:
        starts.append(starts[-1] + d)
    total = starts[-1] + durs[-1]

    overlaps = [
        round(min(TRANSITION_SECONDS, 0.45 * min(durs[i], durs[i + 1])), 3) for i in range(n - 1)
    ]
    # Extra 2 frames so a clip never ends before its transition does (frame quantisation).
    lengths = [durs[i] + overlaps[i] + 2 / FPS for i in range(n - 1)] + [durs[-1]]

    lock = threading.Lock()
    frac = [0.0] * n
    last_pct = [-1]

    def report(pct: float) -> None:
        if on_progress is None:
            return
        p = max(0, min(100, int(pct)))
        with lock:
            if p > last_pct[0]:
                last_pct[0] = p
                on_progress(p)

    # Stage weights: scenes 0-50, join 50-92, audio+mux 92-100
    def scene_frac(i: int, f: float) -> None:
        frac[i] = f
        report(50 * sum(frac) / n)

    clips = [work_dir / f"scene_{i:03d}.mp4" for i in range(n)]
    workers = max(1, min(MAX_SCENE_WORKERS, (os.cpu_count() or 2) // 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [
            pool.submit(_render_scene, i, media[i], lengths[i], w, h, clips[i], work_dir,
                        lambda f, i=i: scene_frac(i, f), cancel)
            for i in range(n)
        ]
        for f in futs:
            f.result()
    report(50)

    joined = work_dir / "joined.mp4"
    if n == 1:
        graph = f"[0:v]settb=AVTB,fps={FPS},format=yuv420p,trim=duration={total:.3f}[vout]"
    else:
        parts = [
            f"[{i}:v]settb=AVTB,fps={FPS},format=yuv420p,setpts=PTS-STARTPTS[c{i}]" for i in range(n)
        ]
        prev = "c0"
        for i in range(1, n):
            label = "vout" if i == n - 1 else f"x{i}"
            tr = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
            parts.append(
                f"[{prev}][c{i}]xfade=transition={tr}:duration={overlaps[i - 1]:.3f}:"
                f"offset={starts[i]:.3f}[{label}]"
            )
            prev = label
        graph = ";\n".join(parts)
    vscript = _write_script(work_dir / "video_graph.txt", graph)
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    _run_ffmpeg(
        [*inputs, "-filter_complex_script", str(vscript), "-map", "[vout]", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-r", str(FPS), "-t", f"{total:.3f}", str(joined)],
        total, work_dir / "join.log", lambda f: report(50 + 42 * f), cancel,
    )
    report(92)

    afmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    ap = [
        f"[{i}:a]{afmt},apad=whole_dur={durs[i]:.3f},atrim=0:{durs[i]:.3f},asetpts=PTS-STARTPTS[a{i}]"
        for i in range(n)
    ]
    ap.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[voice]")
    ap.append(f"[voice]loudnorm=I=-16:TP=-1.5:LRA=11,{afmt},apad=whole_dur={total:.3f},"
              f"atrim=0:{total:.3f}[vn]")
    last = "vn"
    a_inputs: list[str] = []
    for a in audio:
        a_inputs += ["-i", a.path]
    if music_path is not None:
        a_inputs += ["-stream_loop", "-1", "-i", str(music_path)]
        ap.append(f"[{n}:a]{afmt},volume=-20dB,atrim=0:{total:.3f},asetpts=PTS-STARTPTS[mus]")
        ap.append("[vn][mus]amix=inputs=2:duration=first:normalize=0[mix]")
        last = "mix"
    fade = min(0.3, total / 4)
    ap.append(f"[{last}]afade=t=out:st={total - fade:.3f}:d={fade:.3f}[aout]")
    ascript = _write_script(work_dir / "audio_graph.txt", ";\n".join(ap))
    audio_out = work_dir / "audio.m4a"
    _run_ffmpeg(
        [*a_inputs, "-filter_complex_script", str(ascript), "-map", "[aout]",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total:.3f}", str(audio_out)],
        total, work_dir / "audio.log", lambda f: report(92 + 5 * f), cancel=cancel,
    )

    part = out_path.with_name(out_path.stem + ".part.mp4")
    try:
        _run_ffmpeg(
            ["-i", str(joined), "-i", str(audio_out), "-map", "0:v", "-map", "1:a",
             "-c", "copy", "-movflags", "+faststart", "-t", f"{total:.3f}", str(part)],
            total, work_dir / "mux.log", cancel=cancel,
        )
        part.replace(out_path)
    finally:
        part.unlink(missing_ok=True)
    report(100)

    for p in [*clips, joined, audio_out]:
        try:
            p.unlink()
        except OSError:
            pass
    return [round(s, 3) for s in starts]
