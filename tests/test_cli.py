"""End-to-end CLI tests: drive the tools the way an agent does — as processes.

These prove the ``console_scripts`` wiring (argparse, the ``python -m media_ai``
dispatcher, JSON-on-stdout contract) and run the full storyboard pipeline
offline on the mock backend. Skipped automatically if ffmpeg/Pillow aren't
available, so the suite stays green on a bare environment.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from media_ai import mediakit


def _have_media_stack() -> bool:
    try:
        import PIL  # noqa: F401

        mediakit.ffmpeg_exe()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _have_media_stack(), reason="needs Pillow + ffmpeg")


def run(env, name, *args):
    proc = subprocess.run(
        [sys.executable, "-m", "media_ai", name, *args],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"{name} failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@pytest.fixture
def env(tmp_path, monkeypatch):
    import os

    e = dict(os.environ)
    e["MEDIA_BACKEND"] = "mock"
    e["MEDIA_USAGE_LOG"] = str(tmp_path / "usage.jsonl")
    return e


def test_dispatcher_lists_commands():
    proc = subprocess.run([sys.executable, "-m", "media_ai"], capture_output=True, text=True)
    # no subcommand -> non-zero + a usage/help listing mentioning the tools
    assert proc.returncode != 0
    listing = (proc.stdout + proc.stderr).lower()
    for name in ("text2image", "text2video", "concat_video", "media_usage"):
        assert name in listing


def test_text2image_cli_contract(env, tmp_path):
    out = tmp_path / "ref.png"
    res = run(env, "text2image", "--prompt", "a red dune", "--output", str(out), "--width", "128", "--height", "128")
    assert res["ok"] and res["kind"] == "image"
    assert out.is_file() and res["bytes"] > 0
    assert res["usage"]["total_tokens"] > 0


def test_model_flag_flows_through_cli(env, tmp_path):
    res = run(
        env,
        "text2image",
        "--prompt",
        "p",
        "--output",
        str(tmp_path / "m.png"),
        "--width",
        "64",
        "--height",
        "64",
        "--model",
        "doubao-seedream-5-0-260128",
    )
    assert res["meta"]["model"] == "doubao-seedream-5-0-260128"


def test_full_pipeline(env, tmp_path):
    w = tmp_path
    ref = run(env, "text2image", "--prompt", "astronaut", "--output", str(w / "ref.png"), "--seed", "7")
    assert ref["ok"]

    img2 = run(
        env,
        "image2image",
        "--images",
        json.dumps([str(w / "ref.png")]),
        "--prompt",
        "low angle",
        "--output",
        str(w / "ref2.png"),
        "--seed",
        "7",
    )
    assert img2["ok"]

    shot1 = run(
        env,
        "image2video",
        "--first_frame",
        str(w / "ref.png"),
        "--prompt",
        "turns to camera",
        "--output",
        str(w / "shot1.mp4"),
        "--seconds",
        "1",
        "--resolution",
        "480p",
        "--return_last_frame",
        "true",
    )
    assert shot1["extra_paths"], "image2video --return_last_frame should emit a last frame"

    shot2 = run(
        env,
        "text2video",
        "--prompt",
        "twin suns",
        "--output",
        str(w / "shot2.mp4"),
        "--seconds",
        "1",
        "--resolution",
        "480p",
    )
    assert shot2["ok"]

    final = run(
        env,
        "concat_video",
        "--inputs",
        json.dumps([str(w / "shot1.mp4"), str(w / "shot2.mp4")]),
        "--output",
        str(w / "final.mp4"),
    )
    assert (w / "final.mp4").is_file() and final["bytes"] > 0

    usage = run(env, "media_usage")
    totals = usage["totals"]
    assert totals["total_tokens"] > 0
    assert totals["images_generated"] >= 2
    assert totals["video_seconds"] >= 2


def test_usage_ledger_is_per_log_path(tmp_path):
    """Two different MEDIA_USAGE_LOG paths give independent ledgers — the basis
    for per-run cost isolation on a shared filesystem."""
    import os

    def gen(log):
        e = dict(os.environ)
        e["MEDIA_BACKEND"] = "mock"
        e["MEDIA_USAGE_LOG"] = str(log)
        run(e, "text2image", "--prompt", "p", "--output", str(log.parent / "x.png"), "--width", "64", "--height", "64")
        return run(e, "media_usage")["totals"]["calls"]

    a = tmp_path / "a" / "usage.jsonl"
    b = tmp_path / "b" / "usage.jsonl"
    a.parent.mkdir()
    b.parent.mkdir()
    assert gen(a) == 1
    assert gen(b) == 1  # b's ledger saw only its own call, not a's
