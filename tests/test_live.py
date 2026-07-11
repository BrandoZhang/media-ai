"""Live provider smoke tests — hit the REAL APIs.

These are the regression tests to run on merge to main (or on demand) with real
credentials. They are **double-gated** so they never run by accident and never
fail when creds are absent:

  1. `MEDIA_LIVE_TESTS=1` must be set (opt in), AND
  2. the specific provider's key must be present.

Otherwise each test **skips** (green). Configure via env / `.env` (see
`.env.example`) and CI secrets (see `.github/workflows/live.yml`). Kept minimal
(one small image per provider) to bound cost; the video smoke is gated again
behind `MEDIA_LIVE_VIDEO=1` since it is slow and costlier.
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


def _has(*names: str) -> bool:
    return all(os.getenv(n) for n in names)


def _any(*names: str) -> bool:
    return any(os.getenv(n) for n in names)


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "media_ai", *args], capture_output=True, text=True)


def _last_json(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _smoke_image(provider: str, tmp_path) -> dict:
    out = tmp_path / f"{provider}.png"
    proc = _cli("image", "generate", "--provider", provider,
                "--prompt", "a single small red circle centered on a plain white background",
                "--output", str(out))
    assert proc.returncode == 0, f"{provider} live image failed (exit {proc.returncode}): {proc.stderr or proc.stdout}"
    res = _last_json(proc)
    assert res["ok"] and out.is_file() and res["artifacts"][0]["bytes"] > 0
    assert res["provider"] == provider
    return res


@pytest.mark.skipif(not (_LIVE and _has("OPENAI_API_KEY")),
                    reason="set MEDIA_LIVE_TESTS=1 and OPENAI_API_KEY")
def test_live_openai_image(tmp_path):
    _smoke_image("openai", tmp_path)


@pytest.mark.skipif(not (_LIVE and _any("GEMINI_API_KEY", "GOOGLE_API_KEY")),
                    reason="set MEDIA_LIVE_TESTS=1 and GEMINI_API_KEY/GOOGLE_API_KEY")
def test_live_gemini_image(tmp_path):
    _smoke_image("gemini", tmp_path)


@pytest.mark.skipif(not (_LIVE and _any("GEMINI_API_KEY", "GOOGLE_API_KEY")),
                    reason="set MEDIA_LIVE_TESTS=1 and GEMINI_API_KEY/GOOGLE_API_KEY")
def test_live_gemini_speech(tmp_path):
    out = tmp_path / "gemini.wav"
    proc = _cli("speech", "generate", "--provider", "gemini",
                "--text", "Say cheerfully: Have a wonderful day!", "--voice", "Kore",
                "--output", str(out))
    assert proc.returncode == 0, f"gemini live speech failed (exit {proc.returncode}): {proc.stderr or proc.stdout}"
    res = _last_json(proc)
    assert res["ok"] and out.is_file() and res["artifacts"][0]["bytes"] > 0
    assert res["provider"] == "gemini" and res["modality"] == "audio"


@pytest.mark.skipif(not (_LIVE and _has("ARK_API_KEY", "ARK_IMAGE_MODEL")),
                    reason="set MEDIA_LIVE_TESTS=1, ARK_API_KEY and ARK_IMAGE_MODEL (account-specific)")
def test_live_volc_image(tmp_path):
    _smoke_image("volc", tmp_path)


@pytest.mark.skipif(not (_LIVE and _VIDEO and _has("ARK_API_KEY", "ARK_VIDEO_MODEL")),
                    reason="set MEDIA_LIVE_TESTS=1, MEDIA_LIVE_VIDEO=1, ARK_API_KEY and ARK_VIDEO_MODEL")
def test_live_volc_video(tmp_path):
    out = tmp_path / "v.mp4"
    proc = _cli("video", "generate", "--provider", "volc", "--prompt", "a calm ocean wave rolling in",
                "--output", str(out), "--duration", "3", "--resolution", "480p")
    assert proc.returncode == 0, f"volc live video failed (exit {proc.returncode}): {proc.stderr or proc.stdout}"
    res = _last_json(proc)
    assert res["ok"] and out.is_file() and res["artifacts"][0]["bytes"] > 0
