"""Backwards-compatibility shim.

The monolithic ``mediakit`` module was split into :mod:`media_ai.core`,
:mod:`media_ai.providers`, :mod:`media_ai.media`, and :mod:`media_ai.credentials`.
This module re-exports the stable, still-useful helpers from their new homes so
external code that imported them keeps working. New code should import from the
new modules directly.
"""

from __future__ import annotations

from .core.errors import ErrorCategory, MediaError
from .core.geometry import video_dims
from .core.registry import build as _build
from .core.result import GenerationResult
from .core.usage import record_usage, summarize_usage, usage_log_path
from .media.ffmpeg import DEFAULT_FPS, DEFAULT_H, DEFAULT_W, concat_clips, ffmpeg_exe, has_audio

DEFAULT_VIDEO_SECONDS = 5


def get_backend(name: str | None = None):
    """Deprecated: use :func:`media_ai.core.registry.build`. Returns a provider."""
    provider, _ = _build(provider=name)
    return provider


__all__ = [
    "MediaError",
    "ErrorCategory",
    "GenerationResult",
    "record_usage",
    "summarize_usage",
    "usage_log_path",
    "video_dims",
    "concat_clips",
    "ffmpeg_exe",
    "has_audio",
    "get_backend",
    "DEFAULT_W",
    "DEFAULT_H",
    "DEFAULT_FPS",
    "DEFAULT_VIDEO_SECONDS",
]
