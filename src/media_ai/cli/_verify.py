"""Optional credential probes for ``media-ai init --verify``.

**A probe belongs to a binding, not to a provider.** Each binding names its own
credential, so two bindings on one endpoint can hold different keys — checking one and
reporting the answer for both is how ``--verify`` would confidently clear a key it
never touched.

That is also why every probe here is a **model-independent authenticated GET**. A
probe that generates something has to name a model, which makes it a test of *that*
binding's model rather than of the key: sending a speech model id to a music endpoint
earns a 4xx that says nothing about the credential, and reading it as "invalid" sends
someone to rotate a working key. A listing or a lookup authenticates first and answers
second, which is exactly the shape a credential check wants — and it is free, so there
is nothing to ask permission for before running one.

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
#: Stable error codes meaning "there is nothing to check" — the reference did not
#: resolve to a value at all. Matched **before** any prose, because these are the cases
#: where calling a key invalid does real damage: it sends someone to rotate a key that
#: was never the problem. A live run against a typo'd `cred://` account is what showed
#: this: the message is "no [openai] account in credentials.toml", which matched none of
#: the substrings below and so classified as `invalid`.
#:
#: Codes, not prose, because the code is a contract and the sentence is not — every one
#: of these messages can be reworded without anybody thinking to update a marker list.
_MISSING_CODES = frozenset({
    "credential_unresolved",      # env var unset, no such cred:// account, empty value
    "credential_missing",         # the binding names no credential at all
    "credential_scheme_unknown",  # e.g. op:// with nothing registered to serve it
    "credential_backend_missing", # keychain:// without the optional extra installed
})
# Kept as a fallback for messages that arrive without one of those codes.
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

    if code in _MISSING_CODES:
        return "missing"
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


def _adapter(binding: str):
    """The adapter for exactly this binding — the one holding the credential to check."""
    from ..core.config import load_config
    from ..core.registry import build_adapter, catalog
    from ..core.resolve import available_bindings

    for rb in available_bindings(catalog(), load_config()):
        if rb.id == binding and rb.configured:
            return build_adapter(rb)
    raise MediaError(f"binding {binding!r} is not configured", category=ErrorCategory.AUTH)


def _authenticated_get(path: str):
    """A probe that GETs one model-independent path through the binding's own client.

    Goes through ``_prepare`` so the credential is resolved, revealed and redacted by
    the same code a real call uses — a probe that authenticated some other way would
    be testing itself.
    """

    def run(adapter) -> MediaError | None:
        try:
            client, headers = adapter._prepare()
            client.request_json("GET", path, headers=headers)
            return None
        except MediaError as exc:
            return exc

    return run


#: Per provider, the cheapest request that authenticates without naming a model.
#: A 404 counts as success — see :func:`classify`: the lookup happened, which means
#: the key was accepted first.
_PROBES = {
    # Listing what the key can see: free, and unrelated to any one binding's model.
    "openai": _authenticated_get("/models"),
    "gemini": _authenticated_get("/models"),
    "elevenlabs": _authenticated_get("/user"),
    # Ark has no listing endpoint; a task id that cannot exist answers 404 *after*
    # authenticating, which is the same signal.
    "volc-ark": _authenticated_get("/contents/generations/tasks/probe-nonexistent-task"),
}


def probe(binding: str) -> str:
    """Check one binding's credential. Never raises — the result is a label.

    The *strategy* is chosen per provider, because which request is cheap is a fact
    about the API surface. It is then run through the binding the caller named, so the
    key that gets tested is the key that binding would actually use.
    """
    strategy = _PROBES.get(binding.partition("/")[0])
    if strategy is None:
        return "unsupported"
    try:
        return classify(strategy(_adapter(binding)))
    except Exception:  # noqa: BLE001 - a probe must never take the wizard down
        return "unreachable"
