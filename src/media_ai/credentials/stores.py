"""The credential *file* and the pluggable secret-manager backends.

``credentials.toml`` is a flat namespace of **accounts**::

    ["volc-ark/seedance-2.0"]        # named after the binding that uses it
    api_key = "..."

    [shared-ark]                     # or named whatever you like, when several
    api_key = "op://vault/volc/key"  # bindings should share one key deliberately

Nothing here decides *which* account a call uses — a binding names one explicitly
with ``cred://<name>`` (see :mod:`media_ai.credentials.reference`). The wizard writes
one account per binding by default, so "which key did this binding use?" has a
one-line answer; sharing is available by pointing two bindings at the same name,
which is something you do on purpose rather than a precedence rule you inherit.

The file must not be group- or world-readable. A looser mode is refused rather than
silently trusted.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

from ..core.errors import ErrorCategory, MediaError

__all__ = [
    "credentials_path",
    "named_account",
    "register_secret_backend",
    "registered_schemes",
    "secret_backend",
]


def credentials_path() -> Path:
    """Where the secret-bearing file lives (``$MEDIA_CREDENTIALS_FILE``).

    Public because ``init``/``uninstall``/``doctor`` all have to name the same file
    this module reads; a second copy of the default is a bug waiting to happen.
    """
    return Path(os.getenv("MEDIA_CREDENTIALS_FILE", "~/.config/media-ai/credentials.toml")).expanduser()


def _read() -> dict:
    path = credentials_path()
    if not path.is_file():
        return {}
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise MediaError(
            f"credentials file {path} is group/world accessible; run: chmod 600 {path}",
            category=ErrorCategory.AUTH, code="credentials_file_permissions",
        )
    import tomllib  # py311+

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise MediaError(f"could not parse {path}: {exc}", category=ErrorCategory.AUTH) from exc


def named_account(name: str, *, _seen: frozenset[str] = frozenset()) -> str | None:
    """The plaintext value of account ``[<name>]``, or ``None`` when there is no such block.

    An account's ``api_key`` may itself be a reference (``op://…``, ``env://…``, even
    another ``cred://…``), resolved recursively with a cycle guard — so a machine can
    keep every key in a vault and still name accounts locally.
    """
    if name in _seen:
        raise MediaError(f"circular credential reference at cred://{name}", category=ErrorCategory.AUTH)
    section = _read().get(name)
    if not isinstance(section, dict):
        return None
    raw = section.get("api_key") or section.get("key")
    if not isinstance(raw, str) or not raw:
        return None

    from .reference import _split, is_reference, resolve_reference  # deferred: reference imports this module

    if not is_reference(raw):
        return raw
    scheme, rest = _split(raw)
    if scheme == "cred":
        return named_account(rest, _seen=_seen | {name})
    return resolve_reference(raw).reveal()


# -- pluggable secret-manager backends -------------------------------------

_BACKENDS: dict[str, Callable[[str], str]] = {}


def register_secret_backend(scheme: str, fn: Callable[[str], str]) -> None:
    """Teach the resolver a reference scheme (``op``, ``vault``, ``aws-sm``, …).

    The built-in schemes (``env``, ``cred``, ``keychain``, ``broker``) are handled
    directly; everything else arrives here, so a deployment adds its own vault
    without a fork and without media-ai taking a dependency on it.
    """
    _BACKENDS[scheme] = fn


def secret_backend(scheme: str) -> Callable[[str], str] | None:
    return _BACKENDS.get(scheme)


def registered_schemes() -> tuple[str, ...]:
    return tuple(sorted(_BACKENDS))
