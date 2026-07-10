"""ffmpeg helpers: turn an image into a short clip (mock video) and concatenate
per-shot clips into a final film. Uses a system ffmpeg if present, else the
bundled ``imageio-ffmpeg`` binary.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..core.errors import ErrorCategory, MediaError

DEFAULT_W = 768
DEFAULT_H = 432  # 16:9
DEFAULT_FPS = 24


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise MediaError(
            "ffmpeg not found. Install it (`apt install ffmpeg`) or `pip install imageio-ffmpeg`.",
            category=ErrorCategory.IO,
        ) from exc


def run_ffmpeg(args: list[str]) -> None:
    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-8:]
        raise MediaError("ffmpeg failed:\n" + "\n".join(tail), category=ErrorCategory.IO)


def image_to_clip(image: Path, out: Path, *, seconds: int, fps: int, w: int, h: int) -> None:
    ensure_parent(out)
    total = max(1, seconds * fps)
    vf = f"scale={w * 2}:{h * 2},zoompan=z='min(zoom+0.0012,1.12)':d={total}:s={w}x{h}:fps={fps},format=yuv420p"
    common = ["-loop", "1", "-i", str(image), "-t", str(seconds), "-r", str(fps)]
    tail = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)]
    try:
        run_ffmpeg([*common, "-vf", vf, *tail])
    except MediaError:
        run_ffmpeg([*common, "-vf", f"scale={w}:{h},format=yuv420p", *tail])


def has_audio(path: Path) -> bool:
    """True if the file carries an audio stream (uses the bundled ffmpeg, no ffprobe)."""
    try:
        proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    except Exception:  # noqa: BLE001 - treat probe failure as "no audio"
        return False
    return "Audio:" in (proc.stderr or "")


def concat_clips(inputs: list[Path], out: Path, *, w: int = DEFAULT_W, h: int = DEFAULT_H, fps: int = DEFAULT_FPS) -> Path:
    inputs = [Path(p) for p in inputs]
    if not inputs:
        raise MediaError("concat needs at least one input clip", category=ErrorCategory.VALIDATION)
    for p in inputs:
        if not p.is_file():
            raise MediaError(f"input clip not found: {p}", category=ErrorCategory.IO)
    ensure_parent(out)
    # Preserve audio when every clip has a track; mixed audio/no-audio can't merge
    # without synthesizing silence, so fall back to a video-only join there.
    keep_audio = all(has_audio(p) for p in inputs)
    args: list[str] = []
    for p in inputs:
        args += ["-i", str(p)]
    filters, labels = [], ""
    for i in range(len(inputs)):
        filters.append(f"[{i}:v]scale={w}:{h},fps={fps},format=yuv420p,setsar=1[v{i}]")
        labels += f"[v{i}]"
        if keep_audio:
            filters.append(f"[{i}:a]aresample=async=1[a{i}]")
            labels += f"[a{i}]"
    n = len(inputs)
    fc = ";".join(filters) + (
        f";{labels}concat=n={n}:v=1:a=1[outv][outa]" if keep_audio else f";{labels}concat=n={n}:v=1:a=0[outv]"
    )
    args += ["-filter_complex", fc, "-map", "[outv]"]
    if keep_audio:
        args += ["-map", "[outa]", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)]
    run_ffmpeg(args)
    return out
