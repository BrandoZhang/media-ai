"""``media/ffmpeg.py`` — the invariant every call site here shares.

**Every input this module opens is a local file**: a clip the caller named and we checked,
a frame we wrote, a download that already finished. ffmpeg's default is the opposite — it
opens whatever protocol an input string asks for, and follows a playlist that names one —
and ``local/ffmpeg`` is the binding whose whole pitch is "no key, no cost, no network". So
the input is pinned at the encoder too, not only by the check in front of it.
"""

from __future__ import annotations

import subprocess

import pytest
from media_ai.core.errors import MediaError
from media_ai.media import ffmpeg


class _Done:
    """What ffmpeg prints for a two-second clip with sound (it reports to stderr)."""

    returncode = 0
    stdout = ""
    stderr = ("Duration: 00:00:02.00, start: 0.000\n"
              "  Stream #0:0: Video: h264\n  Stream #0:1: Audio: aac\n")


@pytest.fixture
def commands(monkeypatch):
    """Capture every ffmpeg command line this module builds, running none of them."""
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return _Done()

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    monkeypatch.setattr(ffmpeg, "ffmpeg_exe", lambda: "/fake/ffmpeg")
    return seen


def _inputs_are_pinned(cmd: list[str]) -> bool:
    pin = list(ffmpeg.LOCAL_ONLY_INPUT)
    at = [i for i, a in enumerate(cmd) if a == "-i"]
    assert at, f"no input in {cmd}"
    return all(cmd[i - len(pin):i] == pin for i in at)


def test_concat_pins_every_one_of_its_inputs(tmp_path, commands):
    clips = []
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        p = tmp_path / name
        p.write_bytes(b"clip")
        clips.append(p)
    ffmpeg.concat_clips(clips, tmp_path / "out.mp4")
    assert commands, "nothing ran"
    for cmd in commands:  # the audio probe per clip, then the join
        assert _inputs_are_pinned(cmd), cmd
    assert sum(cmd.count("-i") for cmd in commands) == 6  # 3 probes + 3 joined inputs


def test_the_probes_pin_their_input_too(tmp_path, commands):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"clip")
    assert ffmpeg.probe_duration(p) == 2.0
    assert ffmpeg.has_audio(p) is True
    assert [cmd for cmd in commands] and all(_inputs_are_pinned(cmd) for cmd in commands)


def test_image_to_clip_pins_its_input(tmp_path, commands):
    ffmpeg.image_to_clip(tmp_path / "a.png", tmp_path / "a.mp4", seconds=1, fps=8, w=64, h=64)
    assert all(_inputs_are_pinned(cmd) for cmd in commands)


def test_concat_still_refuses_an_input_that_is_not_a_file(tmp_path, commands):
    with pytest.raises(MediaError) as ei:
        ffmpeg.concat_clips([tmp_path / "missing.mp4"], tmp_path / "out.mp4")
    assert "not found" in str(ei.value) and not commands


def test_a_failed_run_reports_the_tail_of_stderr(monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "\n".join(f"line {i}" for i in range(20))

    monkeypatch.setattr(ffmpeg, "ffmpeg_exe", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(ffmpeg.subprocess, "run", lambda cmd, **kw: Failed())
    with pytest.raises(MediaError) as ei:
        ffmpeg.run_ffmpeg(["-i", "x.mp4", "y.mp4"])
    assert "line 19" in str(ei.value) and "line 11" not in str(ei.value)  # last 8 lines only


def test_the_real_binary_accepts_the_pin(tmp_path):
    """The pin is only worth having if it does not break the ordinary local case — and
    an unknown option would make ffmpeg refuse *every* call, so this runs the real one."""
    from conftest import have_media_stack

    if not have_media_stack():
        pytest.skip("needs Pillow + ffmpeg")
    from PIL import Image

    src = tmp_path / "a.png"
    Image.new("RGB", (32, 32), (10, 120, 10)).save(src)
    out = tmp_path / "a.mp4"
    ffmpeg.image_to_clip(src, out, seconds=1, fps=8, w=32, h=32)
    assert out.is_file() and out.stat().st_size > 0
    assert ffmpeg.probe_duration(out) > 0  # the pinned probe still reads the file


def test_a_url_input_is_refused_by_ffmpeg_itself(tmp_path):
    """Second line, checked against the real binary: even handed a URL directly, a pinned
    input cannot open it — so a call site that forgot the check ahead of it fails locally
    instead of making a request."""
    from conftest import have_media_stack

    if not have_media_stack():
        pytest.skip("needs Pillow + ffmpeg")
    cmd = [ffmpeg.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           *ffmpeg.LOCAL_ONLY_INPUT, "-i", "http://127.0.0.1:1/nothing.mp4", "-f", "null", "-"]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert done.returncode != 0
    # ffmpeg's own words for a protocol it was not allowed to open; never a connection error.
    assert "Invalid argument" in done.stderr or "not on the whitelist" in done.stderr.lower()
