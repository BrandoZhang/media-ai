"""Audio helpers for the offline mock provider.

Synthesizes a deterministic placeholder WAV tone with the stdlib ``wave`` module —
no ffmpeg or extra dependency — so the audio path stays green on a bare box. Also
builds a fake per-character alignment (evenly spaced) so the ``--timestamps`` path
is exercisable offline.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

_SAMPLE_RATE = 22050
_MAX_SECONDS = 30.0


def tone_seconds(char_count: int) -> float:
    """A deterministic clip length from text length (~15 chars/sec, clamped)."""
    return max(0.4, min(_MAX_SECONDS, char_count / 15.0))


def write_tone_wav(out: Path, seconds: float, *, freq: float = 220.0, sample_rate: int = _SAMPLE_RATE) -> Path:
    """Write a mono 16-bit PCM WAV sine tone. Deterministic given the inputs."""
    out.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(seconds * sample_rate))
    amp = 12000  # ~0.37 of full scale; audible but not clipping
    frames = bytearray()
    for i in range(n):
        frames += struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * (i / sample_rate))))
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(frames))
    return out


def fake_alignment(text: str, seconds: float) -> dict:
    """Evenly spaced per-character timing for the whole string (mock stand-in)."""
    chars = list(text)
    n = len(chars) or 1
    step = seconds / n
    starts = [round(i * step, 4) for i in range(len(chars))]
    ends = [round((i + 1) * step, 4) for i in range(len(chars))]
    return {"characters": chars, "character_start_times_seconds": starts, "character_end_times_seconds": ends}
