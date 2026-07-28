"""Live smoke tests — hit the REAL APIs.

These are the regression tests to run on merge to main (or on demand) with real
credentials. They are **double-gated** so they never run by accident and never fail
when nothing is configured:

  1. ``MEDIA_LIVE_TESTS=1`` must be set (opt in), AND
  2. the binding under test must be reachable *on this machine* — configured in
     ``config.toml`` with a credential that resolves.

The second gate is asked of the CLI rather than of the environment. There is no list
of provider keys here, because a key is not what makes a call possible: a **binding**
is, and only ``media-ai capabilities --configured`` knows which ones this machine has.
That also means these tests need no edit when a binding is added — a configured one is
picked up, an unconfigured one skips green.

Which bindings to exercise comes from the same source: every configured binding
serving the scene under test, one small artifact each, to bound cost. The video smoke
is gated again behind ``MEDIA_LIVE_VIDEO=1`` since it is slow and costlier.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.live

_LIVE = os.getenv("MEDIA_LIVE_TESTS", "").lower() in {"1", "true", "yes", "on"}
_VIDEO = os.getenv("MEDIA_LIVE_VIDEO", "").lower() in {"1", "true", "yes", "on"}

#: Bindings that produce nothing real. Running a live test against `mock/mock` would
#: pass while proving nothing, which is the one outcome worse than skipping.
_OFFLINE = {"mock/mock", "local/ffmpeg"}


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "media_ai", *args], capture_output=True, text=True)


def _last_json(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _configured_for(scene: str) -> list[str]:
    """Binding ids this machine can actually call for one scene, or [] when opted out."""
    if not _LIVE:
        return []
    proc = _cli("capabilities", "--scene", scene, "--configured")
    if proc.returncode != 0:
        return []
    return [b["binding"] for b in _last_json(proc).get("bindings", []) if b["binding"] not in _OFFLINE]


def _params(scene: str):
    """Parametrize over the reachable bindings, or one skip marker naming what is missing.

    An empty parametrize list would silently collect *nothing*, which reads as a green
    run that tested something. One explicitly skipped case says which scene had no
    binding, which is the difference between "nothing to run" and "nothing ran".
    """
    found = _configured_for(scene)
    if found:
        return [pytest.param(b, id=b) for b in found]
    reason = f"set MEDIA_LIVE_TESTS=1 and configure a binding for {scene}"
    return [pytest.param(None, id="none-configured", marks=pytest.mark.skip(reason=reason))]


def _assert_artifact(proc: subprocess.CompletedProcess, binding: str, out) -> dict:
    assert proc.returncode == 0, f"{binding} failed (exit {proc.returncode}): {proc.stderr or proc.stdout}"
    res = _last_json(proc)
    assert res["ok"] and out.is_file() and res["artifacts"][0]["bytes"] > 0
    assert res["meta"]["binding"] == binding
    return res


@pytest.mark.parametrize("binding", _params("image.text_to_image"))
def test_live_text_to_image(binding, tmp_path):
    out = tmp_path / f"{binding.replace('/', '_')}.png"
    proc = _cli("image", "generate", "--binding", binding,
                "--prompt", "a single small red circle centered on a plain white background",
                "--output", str(out))
    res = _assert_artifact(proc, binding, out)
    assert res["modality"] == "image" and res["meta"]["scene"] == "image.text_to_image"


@pytest.mark.parametrize("binding", _params("speech.text_to_speech"))
def test_live_text_to_speech(binding, tmp_path):
    # No --voice: each binding declares its own default, and a voice id from one
    # account is meaningless on another.
    out = tmp_path / f"{binding.replace('/', '_')}.wav"
    proc = _cli("speech", "generate", "--binding", binding,
                "--text", "The first move is what sets everything in motion.",
                "--output", str(out))
    res = _assert_artifact(proc, binding, out)
    assert res["modality"] == "audio" and res["meta"]["scene"] == "speech.text_to_speech"


@pytest.mark.skipif(not _VIDEO, reason="set MEDIA_LIVE_VIDEO=1 (slow and costlier than the rest)")
@pytest.mark.parametrize("binding", _params("video.text_to_video"))
def test_live_text_to_video(binding, tmp_path):
    out = tmp_path / f"{binding.replace('/', '_')}.mp4"
    proc = _cli("video", "generate", "--binding", binding, "--prompt", "a calm ocean wave rolling in",
                "--output", str(out), "--duration", "3", "--resolution", "480p")
    res = _assert_artifact(proc, binding, out)
    assert res["modality"] == "video" and res["meta"]["scene"] == "video.text_to_video"
