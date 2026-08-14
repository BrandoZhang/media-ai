"""ffmpeg helpers: turn an image into a short clip (mock video) and concatenate
per-shot clips into a final film. Uses a system ffmpeg if present, else the
bundled ``imageio-ffmpeg`` binary.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from ..core import telemetry
from ..core.errors import ErrorCategory, MediaError

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")

DEFAULT_W = 768
DEFAULT_H = 432  # 16:9
DEFAULT_FPS = 24

#: Input options pinning ffmpeg to the local filesystem. Placed before an ``-i``, they
#: apply to the input that follows.
#:
#: Every input this module opens is a local file by construction — a clip the caller
#: named, a frame we wrote, a download that already finished — while ffmpeg's default is
#: to open whatever protocol an input string asks for, and to follow a playlist that
#: names one. ``local/ffmpeg`` is *the* binding that promises no network, so the promise
#: is worth enforcing at the encoder as well as in the check in front of it: a second
#: line means one forgetful call site is not a hole.
LOCAL_ONLY_INPUT = ("-protocol_whitelist", "file")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Run ffmpeg with its stdin closed. **Every call site in this module goes through here.**

    ``subprocess.run(capture_output=True)`` redirects only stdout and stderr, so a child
    otherwise *inherits* — and may consume — whatever stdin the caller had: the rest of
    the list in ``while read f; do media-ai … "$f"; done < list.txt``, or keystrokes typed
    ahead at a terminal. ffmpeg enables interactive keyboard control on stdin by default,
    which is why it ships ``-nostdin`` to turn it off.

    This closes that hole rather than fixing a reproduction: the bundled ffmpeg 7.0.2 left
    both a piped stdin and a type-ahead tty alone when measured. Whether an encoder reads
    stdin is a question about its build and the conditions, and the answer is only
    interesting if it is *no every time* — while the invariant here is flat. The CLI reads
    no stdin of its own (inputs are named on argv, ``--output`` is required), so nothing it
    spawns holds the caller's.

    Both halves are deliberate: ``stdin=DEVNULL`` is what enforces it, in one place a new
    call site cannot forget, and ``-nostdin`` says so on the command line that a failure
    message prints.

    Being the single spawn site makes it the single place to time one, too. A local
    encode has no HTTP span to account for it, so without this a ``video concat`` or an
    animation export is a minute of wall clock a trace cannot explain. The command line
    is *not* an attribute: it carries the caller's paths, and a span is not where those
    should turn up.
    """
    started = time.monotonic()
    with telemetry.span("subprocess.ffmpeg", **{"process.executable.name": "ffmpeg", "argv.count": len(args)}) as sp:
        proc = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-nostdin", *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        sp.set(**{"process.exit_code": proc.returncode, "duration_ms": round(elapsed_ms, 1)})
        telemetry.observe("media_ai.subprocess.duration", elapsed_ms, process="ffmpeg",
                          outcome="ok" if proc.returncode == 0 else "error")
        return proc


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
    proc = _run(["-y", "-loglevel", "error", *args])
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-8:]
        raise MediaError("ffmpeg failed:\n" + "\n".join(tail), category=ErrorCategory.IO)


def image_to_clip(image: Path, out: Path, *, seconds: int, fps: int, w: int, h: int) -> None:
    ensure_parent(out)
    total = max(1, seconds * fps)
    vf = f"scale={w * 2}:{h * 2},zoompan=z='min(zoom+0.0012,1.12)':d={total}:s={w}x{h}:fps={fps},format=yuv420p"
    common = ["-loop", "1", *LOCAL_ONLY_INPUT, "-i", str(image), "-t", str(seconds), "-r", str(fps)]
    tail = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)]
    try:
        run_ffmpeg([*common, "-vf", vf, *tail])
    except MediaError:
        run_ffmpeg([*common, "-vf", f"scale={w}:{h},format=yuv420p", *tail])


def has_audio(path: Path) -> bool:
    """True if the file carries an audio stream (uses the bundled ffmpeg, no ffprobe)."""
    try:
        proc = _run([*LOCAL_ONLY_INPUT, "-i", str(path)])
    except Exception:  # noqa: BLE001 - treat probe failure as "no audio"
        return False
    return "Audio:" in (proc.stderr or "")


def probe_duration(path: Path) -> float:
    """Best-effort media duration in seconds (parses ``ffmpeg -i``; no ffprobe needed).

    Returns ``0.0`` when the duration can't be determined (missing ffmpeg, unreadable
    file, no ``Duration:`` line) — callers use it as a cost-accounting hint, never a
    hard dependency."""
    try:
        proc = _run([*LOCAL_ONLY_INPUT, "-i", str(path)])
    except Exception:  # noqa: BLE001 - probe failure -> unknown duration
        return 0.0
    m = _DURATION_RE.search(proc.stderr or "")
    if not m:
        return 0.0
    hours, minutes, seconds = m.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


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
        args += [*LOCAL_ONLY_INPUT, "-i", str(p)]
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
