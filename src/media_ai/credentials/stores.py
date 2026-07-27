"""Individual credential resolvers, ordered most-secure → simplest.

Each resolver is ``(provider: str) -> Credential | None`` and returns ``None`` when
it holds nothing for that provider (so the chain falls through to the next one).
The value is only ever wrapped in a :class:`Secret`/:class:`BrokeredHandle`; it is
never logged or returned as a bare string.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ..core.errors import ErrorCategory, MediaError
from .secret import BrokeredHandle, Credential, Secret

# Environment variables checked per provider (first non-empty wins).
ENV_VARS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "volc": ("ARK_API_KEY", "VOLC_API_KEY"),
    "elevenlabs": ("ELEVENLABS_API_KEY", "ELEVEN_API_KEY"),
}

# Value prefixes that mark a credential as a *reference* to resolve (vs. a raw key).
# Includes ``env://`` so an ``env://VAR`` written in credentials.toml resolves the env
# var instead of being stored verbatim as the key, and ``cred://`` so a profile can
# reference an *account* ([<name>]) held in credentials.toml.
# Kept in sync with resolve_reference() / _SECRET_BACKENDS.
_REFERENCE_PREFIXES = ("env://", "cred://", "op://", "vault://", "gcp-sm://", "aws-sm://", "arn:aws:secretsmanager:")


class _CredentialMiss(Exception):
    """A reference's *source is simply absent* (an unset env var, a missing
    ``[<name>]`` account block). Distinct from a genuine misconfiguration
    (unknown scheme, refused file permissions, reference cycle): a miss is
    silently skipped inside a fallback list, while a misconfiguration is always
    surfaced. See :func:`try_resolve_reference`."""


def broker_resolver(provider: str) -> Credential | None:
    """Highest priority: if a broker is configured, hold only a session token."""
    endpoint = os.getenv("MEDIA_CRED_BROKER")
    if not endpoint:
        return None
    token = os.getenv("MEDIA_CRED_BROKER_TOKEN", "")
    return BrokeredHandle(provider=provider, endpoint=endpoint, token=token)


def credentials_path() -> Path:
    """Where the secret-bearing credentials file lives (``$MEDIA_CREDENTIALS_FILE``).

    Public because ``media-ai init``/``uninstall``/``doctor`` all have to name the
    same file this module reads; a second copy of the default would be a bug waiting
    for someone to change one of them.
    """
    return Path(os.getenv("MEDIA_CREDENTIALS_FILE", "~/.config/media-ai/credentials.toml")).expanduser()


def _read_credentials_toml() -> dict | None:
    """Parse ``~/.config/media-ai/credentials.toml`` (override with
    ``MEDIA_CREDENTIALS_FILE``), or return ``None`` when the file is absent.

    The file must not be world/group readable (``chmod 600``); a looser mode is
    refused rather than silently trusted.
    """
    path = credentials_path()
    if not path.is_file():
        return None
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise MediaError(
            f"credentials file {path} is group/world accessible; run: chmod 600 {path}",
            category=ErrorCategory.AUTH,
        )
    try:
        import tomllib  # py311+
    except ModuleNotFoundError:  # pragma: no cover
        return None
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _section_key(section: object) -> str | None:
    """Pull the ``api_key`` (or legacy ``key``) out of a credential table."""
    if not isinstance(section, dict):
        return None
    return section.get("api_key") or section.get("key")


def _config_value(provider: str) -> str | None:
    """A provider's **default** credential from credentials.toml — the account whose
    name matches the provider (``[<provider>]``). credentials.toml is one flat
    namespace of accounts, so a provider default is just an account named after the
    provider; extra accounts are referenced from a profile as ``cred://<name>``.
    """
    data = _read_credentials_toml()
    if data is None:
        return None
    return _section_key(data.get(provider))


def _named_credential(name: str, *, _seen: frozenset[str] = frozenset()) -> str | None:
    """Resolve an **account** ``[<name>]`` from credentials.toml to a plaintext value,
    or ``None`` when no such block exists.

    The stored ``api_key`` may itself be a reference (``op://…``, ``env://…``, even
    another ``cred://…``), resolved recursively with a cycle guard. A *nested* absent
    source raises :class:`_CredentialMiss` so a fallback list still skips it.
    """
    if name in _seen:
        raise MediaError(f"circular credential reference at cred://{name}", category=ErrorCategory.AUTH)
    data = _read_credentials_toml()
    if data is None:
        return None
    raw = _section_key(data.get(name))
    if not raw:
        return None
    if raw.startswith(_REFERENCE_PREFIXES):
        scheme = raw.split("://", 1)[0] if "://" in raw else raw.split(":", 1)[0]
        if scheme == "cred":
            inner = raw.split("://", 1)[1] if "://" in raw else raw.split(":", 1)[1]
            return _named_credential(inner, _seen=_seen | {name})
        return _resolve_ref(raw)
    return raw


def secret_manager_resolver(provider: str) -> Credential | None:
    """Resolve a secret-manager *reference* (``op://…``, a Vault path, an ARN, …).

    The reference may come from env (``MEDIA_SECRET_REF_<PROVIDER>``) or the config
    file. Actual manager backends are pluggable; this ships the reference-detection
    and a clear "install the backend" error so the wiring is real but dependency-free.
    """
    ref = os.getenv(f"MEDIA_SECRET_REF_{provider.upper()}")
    if ref is None:
        cfg = _config_value(provider)
        ref = cfg if (cfg and cfg.startswith(_REFERENCE_PREFIXES)) else None
    if not ref:
        return None
    value = resolve_reference(ref)
    return Secret(value, provider=provider, source="secret-manager")


def _resolve_ref(ref: str) -> str:
    """Dispatch a reference to its backend. Raises :class:`_CredentialMiss` when the
    source is merely absent and :class:`MediaError` for a genuine misconfiguration
    (unknown scheme). The public wrappers below decide how to treat a miss."""
    scheme = ref.split("://", 1)[0] if "://" in ref else ref.split(":", 1)[0]
    backend = _SECRET_BACKENDS.get(scheme)
    if backend is None:
        raise MediaError(
            f"no secret-manager backend registered for reference scheme {scheme!r}; "
            "register one with media_ai.credentials.stores.register_secret_backend",
            category=ErrorCategory.AUTH,
        )
    return backend(ref)


def resolve_reference(ref: str) -> str:
    """Resolve a ``cred://``/``env://``/``op://``/… reference to a plaintext value.

    Strict: an absent source (unset env var, missing ``[<name>]`` account) is an
    actionable error. ``cred://<name>`` resolves an *account* from credentials.toml;
    ``env://VARNAME`` reads the environment; other schemes are
    pluggable and raise until a backend is registered via
    :func:`register_secret_backend`. Use :func:`try_resolve_reference` for
    fall-through-on-miss behavior inside a credential fallback list.
    """
    try:
        return _resolve_ref(ref)
    except _CredentialMiss as miss:
        raise MediaError(str(miss), category=ErrorCategory.AUTH) from None


def try_resolve_reference(ref: str) -> str | None:
    """Soft resolution for fallback lists: return the value, or ``None`` when the
    source is simply **absent** (so the caller can try the next option). A genuine
    misconfiguration — unknown scheme, a ``chmod``-refused credentials file, a
    reference cycle — still raises, so a typo or security problem is never silently
    swallowed."""
    try:
        return _resolve_ref(ref)
    except _CredentialMiss:
        return None


def _env_backend(ref: str) -> str:
    # Accept both env://VAR and env:VAR — _resolve_ref() detects the scheme from
    # whichever separator is present, so this backend must too (avoid an IndexError on
    # the bare-colon form).
    var = ref.split("://", 1)[1] if "://" in ref else ref.split(":", 1)[1]
    val = os.getenv(var)
    if not val:
        raise _CredentialMiss(f"secret reference {ref} -> env {var} is unset")
    return val


def _cred_backend(ref: str) -> str:
    # cred://<name> -> the account [<name>] in credentials.toml.
    name = ref.split("://", 1)[1] if "://" in ref else ref.split(":", 1)[1]
    val = _named_credential(name)
    if not val:
        raise _CredentialMiss(f"credential reference {ref} -> no [{name}] account in credentials.toml")
    return val


_SECRET_BACKENDS: dict[str, "callable"] = {"env": _env_backend, "cred": _cred_backend}


def register_secret_backend(scheme: str, fn) -> None:
    """Register a resolver for a secret-manager reference scheme (e.g. ``op``)."""
    _SECRET_BACKENDS[scheme] = fn


def keychain_resolver(provider: str) -> Credential | None:
    """Look up the provider key in the OS keychain via the optional ``keyring`` extra."""
    if os.getenv("MEDIA_DISABLE_KEYCHAIN"):
        return None
    try:
        import keyring  # type: ignore
    except ModuleNotFoundError:
        return None
    try:
        value = keyring.get_password("media-ai", provider)
    except Exception:  # noqa: BLE001 - a locked/absent keychain is just a miss
        return None
    return Secret(value, provider=provider, source="keychain") if value else None


def file_resolver(provider: str) -> Credential | None:
    value = _config_value(provider)
    if value and not value.startswith(_REFERENCE_PREFIXES):
        return Secret(value, provider=provider, source="config-file")
    return None


def env_resolver(provider: str) -> Credential | None:
    for var in ENV_VARS.get(provider, (f"{provider.upper()}_API_KEY",)):
        val = os.getenv(var)
        if val:
            return Secret(val, provider=provider, source=f"env:{var}")
    return None
