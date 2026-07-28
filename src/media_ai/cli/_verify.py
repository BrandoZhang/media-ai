"""Optional key probes for ``media-ai init --verify``.

Off by default, because the probes are not uniformly free: three providers expose a
credential-free or read-only call, but OpenAI has none — verifying it costs a real
(small) image generation. ``init`` asks separately before spending that.

Classifying the outcome takes more than the exit code. A provider's error mapping
follows the HTTP status it actually returns, and Google answers an invalid API key
with 400 INVALID_ARGUMENT — which maps to ``validation`` (exit 3), not ``auth``. So a
bad key does *not* reliably surface as an auth error, and matching on category alone
would report a broken key as working.
"""

from __future__ import annotations

from ..core.errors import ErrorCategory, MediaError

__all__ = ["probe", "classify"]

# Substrings that mean "the key itself was rejected", whatever category it arrived as.
_BAD_KEY_MARKERS = (
    "api key not valid",
    "api_key_invalid",
    "invalid api key",
    "unauthenticated",
    "permission_denied",
    "invalid_api_key",
)
# "There is nothing to check", not "what you gave me was rejected". The env-var
# markers matter because storing a reference is one of the wizard's two offered modes:
# the variable is routinely not exported in the shell the wizard itself runs in, and
# telling that user their key is invalid sends them to rotate a perfectly good one.
_MISSING_MARKERS = (
    "no credential found for provider",
    "is unset",
    "not set",
    "no such account",
)


def classify(exc: MediaError | None) -> str:
    """Turn a probe outcome into one of: ok / missing / invalid / no-quota / unreachable."""
    if exc is None:
        return "ok"
    message = (str(exc) or "").lower()
    code = (getattr(exc, "code", "") or "").lower()

    if any(m in message for m in _MISSING_MARKERS):
        return "missing"
    if any(m in message or m in code for m in _BAD_KEY_MARKERS):
        return "invalid"
    if exc.category == ErrorCategory.AUTH:
        return "invalid"
    if exc.category == ErrorCategory.RATE_LIMIT:
        # The key authenticated; the account is just out of quota or throttled.
        return "no-quota"
    if exc.category in (ErrorCategory.PROVIDER, ErrorCategory.TIMEOUT):
        # A network or upstream fault says nothing about the key.
        return "unreachable"
    if exc.category == ErrorCategory.NOT_FOUND:
        # A probe that authenticated and then failed to find a made-up job id is the
        # success signal for the read-only probes below.
        return "ok"
    return "invalid"


def _adapter(provider: str):
    """The adapter for a provider's first configured binding.

    A probe checks the *credential*, which a binding owns — and every binding on one
    provider shares an endpoint, so any of them proves the key. Which one is arbitrary
    and does not need to be a choice.
    """
    from ..core.config import load_config
    from ..core.registry import build_adapter, catalog
    from ..core.resolve import available_bindings

    for rb in available_bindings(catalog(), load_config()):
        if rb.provider.name == provider and rb.configured:
            return build_adapter(rb)
    raise MediaError(f"no configured binding for provider {provider!r}", category=ErrorCategory.AUTH)


def _probe_job_query(provider: str, job_id: str) -> MediaError | None:
    """Query a job id that cannot exist. Authentication happens before lookup, so a
    not-found answer proves the key worked — and a GET costs nothing."""
    from ..core.types import JobRef

    try:
        _adapter(provider).query_job(JobRef(id=job_id, provider=provider))
        return None
    except MediaError as exc:
        return exc


def _probe_elevenlabs() -> MediaError | None:
    """``music plan`` is credit-free but fully authenticated."""
    from ..core.types import MusicRequest

    try:
        _adapter("elevenlabs").plan_music(MusicRequest(prompt="probe", duration=3))
        return None
    except MediaError as exc:
        return exc


def _probe_openai() -> MediaError | None:
    """The only paid probe: OpenAI exposes no free authenticated call. Callers must
    confirm before this runs."""
    import tempfile
    from pathlib import Path

    from ..core.types import ImageRequest

    try:
        with tempfile.TemporaryDirectory() as tmp:
            _adapter("openai").generate_image(
                ImageRequest(prompt="a grey square", model="gpt-image-1-mini",
                             output=Path(tmp) / "probe.png", options={"quality": "low"})
            )
        return None
    except MediaError as exc:
        return exc


_PROBES = {
    "gemini": lambda: _probe_job_query("gemini", "models/veo-3.1-generate-preview/operations/probe"),
    "volc-ark": lambda: _probe_job_query("volc-ark", "probe-nonexistent-task"),
    "elevenlabs": _probe_elevenlabs,
    "openai": _probe_openai,
}


def probe(binding: str) -> str:
    """Check a binding's credential. Never raises — the result is a label.

    Keyed by provider because a probe tests the *endpoint's* credential, which every
    binding on that provider shares.
    """
    fn = _PROBES.get(binding.partition("/")[0])
    if fn is None:
        return "unsupported"
    try:
        return classify(fn())
    except Exception:  # noqa: BLE001 - a probe must never take the wizard down
        return "unreachable"
