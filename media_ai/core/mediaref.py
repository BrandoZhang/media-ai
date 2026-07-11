"""Materialize a :class:`MediaRef` into whatever form a provider needs.

Providers accept inputs differently — inline base64 data-URIs (Volc/Gemini),
multipart file uploads (OpenAI edits), or Files-API URIs (Gemini large media).
This module hides those transports behind a few helpers so adapters stay small.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from .errors import ErrorCategory, MediaError
from .types import MediaRef

_EXT_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".gif": "image/gif", ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
}


def guess_mime(path: Path | str, *, media: str = "image") -> str:
    ext = Path(path).suffix.lower()
    if ext in _EXT_MIME:
        return _EXT_MIME[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or f"{media}/{'png' if media == 'image' else 'octet-stream'}"


def read_bytes(ref: MediaRef) -> tuple[bytes, str]:
    """Read a *local* ref's bytes + mime. Raises if the ref is remote or missing."""
    if ref.is_remote:
        raise MediaError(f"expected a local file, got remote ref {ref.raw!r}", category=ErrorCategory.VALIDATION)
    p = ref.path()
    if not p.is_file():
        role = f" ({ref.role})" if ref.role else ""
        raise MediaError(f"input media not found{role}: {p}", category=ErrorCategory.IO)
    return p.read_bytes(), guess_mime(p)


def to_data_uri(ref: MediaRef, media: str = "image") -> str:
    """Pass through remote/data refs; base64-encode a local file as a data-URI."""
    if ref.is_remote:
        return ref.raw
    data, mime = read_bytes(ref)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def to_base64(ref: MediaRef) -> tuple[str, str]:
    """Return ``(base64_str, mime)`` for a local file (Gemini/Veo inline inputs)."""
    data, mime = read_bytes(ref)
    return base64.b64encode(data).decode("ascii"), mime
