"""Redaction filter applied to every output sink — logs, the structured JSON
result, and error messages.

Two layers of masking:
  1. **Known-value masking** — every live secret value (registered when a
     :class:`~media_ai.credentials.secret.Secret` is created) is replaced with
     ``***`` wherever it appears. This is the authoritative layer.
  2. **Shape-based backstop** — common key shapes (``sk-…``, ``Bearer …``,
     ``AIza…``) are masked even if never registered, so a value that slips in
     from an unexpected place is still caught.

Sensitive header/field names are dropped from structured objects entirely.
"""

from __future__ import annotations

import re
import threading

_LIVE_SECRETS: set[str] = set()
_LOCK = threading.Lock()

# Backstop patterns for common credential shapes (masked even if unregistered).
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),  # OpenAI-style
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),  # Google API key
    re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._\-]{8,}"),  # Authorization: Bearer <tok>
)

# Structured-object keys whose values must never be serialized.
_SENSITIVE_KEYS = frozenset(
    {"authorization", "api_key", "api-key", "apikey", "x-api-key", "x-goog-api-key", "xi-api-key", "token", "secret",
     "password", "ark_api_key", "openai_api_key", "gemini_api_key", "elevenlabs_api_key", "access_token", "x-media-session"}
)

_MASK = "***"


def register_secret(value: str) -> None:
    """Record a live secret so :func:`redact` masks it everywhere. No-op for very
    short values (too collision-prone to safely string-replace)."""
    if value and len(value) >= 6:
        with _LOCK:
            _LIVE_SECRETS.add(value)


def redact(text: str) -> str:
    """Mask any known secret value or credential-shaped token in ``text``."""
    if not text:
        return text
    with _LOCK:
        live = tuple(_LIVE_SECRETS)
    for s in live:
        if s in text:
            text = text.replace(s, _MASK)
    text = _PATTERNS[2].sub(r"\1 " + _MASK, text)
    for pat in _PATTERNS[:2]:
        text = pat.sub(_MASK, text)
    return text


def redact_obj(obj):
    """Recursively redact strings in a JSON-able object and drop sensitive keys."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                out[k] = _MASK
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj
