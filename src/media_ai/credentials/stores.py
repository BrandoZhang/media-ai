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

It carries a ``schema`` number for one reason: of everything this tool writes, it is
the only file a user cannot reconstruct. A config can be re-derived by re-running
setup; the keys in here were pasted in from somewhere else, possibly issued once. So
whatever this layout becomes, an existing file has to be *convertible* rather than
replaced — and converting starts with knowing which layout you are holding.

An absent ``schema`` means 1, which is what every file written before the key existed
is. That is the "absent field" rule doing its job: nothing has to be migrated, nothing
has to be rewritten on read, and a file this tool wrote in 2026 keeps working. The key
appears the next time something legitimately writes the file. Reading never writes it
— see :func:`check_schema`.

Writing it
----------

:func:`save_accounts` is the way. It lives here, beside the reader and the schema
rule, rather than inside the setup wizard where it grew up — because ``init`` is not
the only thing that legitimately writes this file. An organisation provisioning a
machine from its own configuration service writes exactly the same accounts, and a
provisioner that reimplements the merge would have to keep tracking three things it
did not choose: that the schema is checked *before* the merge and stamped *after*
(merging into a file this build reads wrongly rewrites it in the older shape and takes
the keys with it), that the mode is 0600 inside a 0700 directory or the resolver
refuses to read it at all, and that a re-run producing identical content must not leave
a second backup — a copy of every key, under a name nobody remembers to delete.

None of that is discoverable from the file format, and every one of them is silent
when got wrong.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path

from ..brand import cli_name, config_dir
from ..core.errors import ErrorCategory, MediaError

__all__ = [
    "SCHEMA",
    "check_schema",
    "credentials_header",
    "credentials_path",
    "named_account",
    "register_secret_backend",
    "registered_schemes",
    "render_accounts",
    "save_accounts",
    "secret_backend",
]

#: The layout of ``credentials.toml``. A monotonic integer, unrelated to the release
#: version: this file either parses the way a build expects or it does not, which is a
#: boolean question, and a release number would only ask every reader to derive one.
SCHEMA = 1

#: ``schema`` is reserved at the top level, so it cannot also be an account name.
_RESERVED = "schema"


def credentials_path() -> Path:
    """Where the secret-bearing file lives (``$MEDIA_CREDENTIALS_FILE``).

    Public because ``init``/``uninstall``/``doctor`` all have to name the same file
    this module reads; a second copy of the default is a bug waiting to happen.
    """
    return Path(os.getenv("MEDIA_CREDENTIALS_FILE") or config_dir() / "credentials.toml").expanduser()


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
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise MediaError(f"could not parse {path}: {exc}", category=ErrorCategory.AUTH) from exc
    check_schema(data, path)
    return data


def check_schema(data: dict, path: Path) -> int:
    """Validate a parsed credentials file's ``schema``, and return it.

    Public because reading is not the only thing that has to understand the layout:
    ``init`` merges new accounts into whatever is already on disk, and merging into a
    file it cannot read correctly would rewrite it in an older shape — losing exactly
    the keys this file exists to keep. One check, both callers.

    A newer file gets its own answer. "Upgrade" and "re-run setup" are opposite
    instructions, and giving the second one to somebody whose file is *ahead* of their
    CLI talks them into overwriting the good copy.
    """
    declared = data.get(_RESERVED, SCHEMA)
    # bool before int: it is an int subclass, so `schema = true` would read as 1.
    if isinstance(declared, bool) or not isinstance(declared, int):
        raise MediaError(
            f"{path}: {_RESERVED} must be an integer — it is reserved at the top level "
            f"and cannot be an account name, got {declared!r}",
            category=ErrorCategory.AUTH, code="credentials_schema_invalid",
        )
    if declared > SCHEMA:
        raise MediaError(
            f"{path} was written by a newer build ({_RESERVED} {declared}; this one reads {SCHEMA}). "
            f"Upgrade {cli_name()} rather than re-running setup, which would rewrite it in the older shape.",
            category=ErrorCategory.AUTH, code="credentials_from_newer_build",
        )
    if declared < SCHEMA:
        raise MediaError(
            f"{path} is written in {_RESERVED} {declared}; this build reads {SCHEMA} and has no "
            f"conversion for it.",
            category=ErrorCategory.AUTH, code="credentials_schema_outdated",
        )
    return declared


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
    without a fork and without this project taking a dependency on it.
    """
    _BACKENDS[scheme] = fn


def secret_backend(scheme: str) -> Callable[[str], str] | None:
    return _BACKENDS.get(scheme)


def registered_schemes() -> tuple[str, ...]:
    return tuple(sorted(_BACKENDS))


# -- writing ----------------------------------------------------------------


def credentials_header() -> str:
    """The comment block written at the top of ``credentials.toml``.

    A function, like every other branded string: a module constant would bake the name
    in at import time, which is invisible until someone renames the build. It names no
    *command* either — ``init`` is not the only thing that legitimately writes this
    file, and a header saying otherwise would be a lie on a provisioned machine.
    """
    cli = cli_name()
    return (
        f"{cli} credentials.\n"
        "SECRETS: keep this file chmod 600; the CLI refuses to read it otherwise.\n"
        "Each [<name>] is an account. Setup names one after the binding that\n"
        "uses it, so `which key did this binding use?` has a one-line answer."
    )


def _existing_for_write(path: Path) -> dict:
    """Parse the current file for a writer, **without** the mode gate :func:`_read` applies.

    The gate is about trusting a key, and a writer is not about to use one: it is about
    to replace the file at 0600, which is the documented remedy for a loose mode. Going
    through the reader here would make ``chmod 644 credentials.toml`` a state that
    setup can no longer repair — the one command a user would reach for stops working
    on exactly the file it would have fixed.

    A file that cannot be *parsed* is still an error, and deliberately not an
    overwrite: it may be hand-written and worth keeping.
    """
    if not path.is_file():
        return {}
    import tomllib

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MediaError(
            f"could not read existing {path}: {exc}; fix or move it, then re-run",
            category=ErrorCategory.AUTH, code="credentials_unreadable",
        ) from exc


def _accounts(raw: Mapping[str, object]) -> dict[str, dict]:
    """Normalize and check the accounts a caller passed.

    ``{"acme-gw": "sk-…"}`` and ``{"acme-gw": {"api_key": "sk-…"}}`` both work — the
    first is what a provisioner has, the second is what the wizard already builds.
    """
    out: dict[str, dict] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name:
            raise MediaError(f"account name must be a non-empty string, got {name!r}",
                             category=ErrorCategory.AUTH, code="credentials_account_invalid")
        if name == _RESERVED:
            raise MediaError(
                f"{_RESERVED!r} is reserved at the top level and cannot be an account name",
                category=ErrorCategory.AUTH, code="credentials_account_invalid",
            )
        entry = {"api_key": value} if isinstance(value, str) else dict(value or {})
        key = entry.get("api_key") or entry.get("key")
        # An empty key is refused rather than written: `named_account` reads a blank
        # value as "no such account", so the file would claim to hold something the
        # resolver then denies, and the error would name the binding rather than this.
        if not isinstance(key, str) or not key.strip():
            raise MediaError(f"account [{name}] has no api_key",
                             category=ErrorCategory.AUTH, code="credentials_account_invalid")
        out[name] = entry
    return out


def render_accounts(accounts: Mapping[str, object], *, replace: bool = False,
                    header: str | None = None) -> str:
    """Serialize ``accounts`` merged into (or replacing) what is on disk. Writes nothing.

    Separate from :func:`save_accounts` because ``init`` writes two files and
    serializes *both* before writing *either*: ``dumps`` refuses a value outside its
    subset, and raising after the keys had been written is exactly the half-applied
    state the wizard's ask-then-do split exists to avoid.

    ``replace=True`` makes the given accounts the whole file. That is the shape an
    organisation provisioning by role wants — an entitlement being withdrawn has to
    *remove* the account it granted, and a merge can only ever add or overwrite. It is
    not the default, because for everything else (the wizard, adding one binding) it
    would silently discard keys nobody asked about.
    """
    from .tomlwrite import TomlWriteError, dumps

    path = credentials_path()
    fresh = _accounts(accounts)
    existing = {} if replace else _existing_for_write(path)
    if not replace:
        # Checked before the merge and stamped after. Before, because merging into a
        # file this build cannot read correctly would rewrite it in the older shape and
        # take the keys with it — the one thing here a user cannot reconstruct. After,
        # because what comes out was written by *this* build, whatever went in claimed.
        check_schema(existing, path)
    data = {**existing, **fresh, _RESERVED: SCHEMA}
    try:
        return dumps(data, header=credentials_header() if header is None else header)
    except TomlWriteError as exc:
        raise MediaError(
            f"{path} holds a value this build cannot write back ({exc}). Remove or quote it, then re-run.",
            category=ErrorCategory.AUTH, code="credentials_unwritable_field",
        ) from exc


def save_accounts(accounts: Mapping[str, object], *, replace: bool = False,
                  header: str | None = None) -> Path | None:
    """Write ``accounts`` into ``credentials.toml``. Returns the backup path, if any.

    The backup is taken **only when the content really changes**. Provisioning runs on
    every login and setup is its own upgrade path, so an unconditional backup would
    accumulate one copy of every key per run, under names nobody will remember to
    delete — the opposite of what a backup is for.

    The *write* happens either way, because it is what sets the mode: the resolver
    refuses a group- or world-readable file, and rewriting is the obvious way to fix
    one. Skipping an identical write would leave that broken with no way back short of
    a manual ``chmod``. The content is unchanged, so the file is not — only its
    permissions can be.
    """
    from .tomlwrite import backup, write_private

    path = credentials_path()
    text = render_accounts(accounts, replace=replace, header=header)
    saved = None
    try:
        unchanged = path.is_file() and path.read_text(encoding="utf-8") == text
    except OSError:
        unchanged = False
    if not unchanged:
        saved = backup(path)
    write_private(path, text)
    return saved
