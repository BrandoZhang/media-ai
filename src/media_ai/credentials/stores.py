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
# var instead of being stored verbatim as the key. Kept in sync with resolve_reference().
_REFERENCE_PREFIXES = ("env://", "op://", "vault://", "gcp-sm://", "aws-sm://", "arn:aws:secretsmanager:")


def broker_resolver(provider: str) -> Credential | None:
    """Highest priority: if a broker is configured, hold only a session token."""
    endpoint = os.getenv("MEDIA_CRED_BROKER")
    if not endpoint:
        return None
    token = os.getenv("MEDIA_CRED_BROKER_TOKEN", "")
    return BrokeredHandle(provider=provider, endpoint=endpoint, token=token)


def _config_value(provider: str) -> str | None:
    """Read a provider's entry from ``~/.config/media-ai/credentials.toml``.

    The file must not be world/group readable (``chmod 600``); a looser mode is
    refused rather than silently trusted.
    """
    path = Path(os.getenv("MEDIA_CREDENTIALS_FILE", "~/.config/media-ai/credentials.toml")).expanduser()
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
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = data.get(provider) or {}
    return section.get("api_key") or section.get("key")


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


def resolve_reference(ref: str) -> str:
    """Resolve a ``op://``/``vault://``/… reference to a plaintext value.

    Backends are intentionally pluggable. Only ``env://VARNAME`` is built in (useful
    for tests and CI); other schemes raise a deterministic, actionable error until a
    backend is registered via :func:`register_secret_backend`.
    """
    scheme = ref.split("://", 1)[0] if "://" in ref else ref.split(":", 1)[0]
    backend = _SECRET_BACKENDS.get(scheme)
    if backend is None:
        raise MediaError(
            f"no secret-manager backend registered for reference scheme {scheme!r}; "
            "register one with media_ai.credentials.stores.register_secret_backend",
            category=ErrorCategory.AUTH,
        )
    return backend(ref)


def _env_backend(ref: str) -> str:
    # Accept both env://VAR and env:VAR — resolve_reference() detects the scheme from
    # whichever separator is present, so this backend must too (avoid an IndexError on
    # the bare-colon form).
    var = ref.split("://", 1)[1] if "://" in ref else ref.split(":", 1)[1]
    val = os.getenv(var)
    if not val:
        raise MediaError(f"secret reference {ref} -> env {var} is unset", category=ErrorCategory.AUTH)
    return val


_SECRET_BACKENDS: dict[str, "callable"] = {"env": _env_backend}


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
