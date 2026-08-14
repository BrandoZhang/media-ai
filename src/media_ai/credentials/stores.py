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

Converting one
--------------

The promise above — *convertible rather than replaced* — had nowhere to land until
there was a registry to land in. :mod:`media_ai.core.migrations` is now that registry,
shared with ``config.toml`` because the question is identical, and the stakes are not:
a config can be re-derived by re-running setup, while a refusal here is a refusal to
read keys that may have been issued once.

Reading applies the **lossless** steps in memory and never writes, exactly as the
config path does. A step that needs a decision refuses and points at ``config
migrate``, which converts both files.

There is deliberately **no ``media-ai credentials`` command group**. The keys are kept
out of the CLI surface on purpose — an agent driving this tool can run any command it
likes, and a group whose whole subject is the secret file is the one thing worth not
handing it. Migration is the only operation this file needs from outside, and it
belongs to the command that already converts the other one.

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
from dataclasses import dataclass
from pathlib import Path

from ..brand import cli_name, cmd, config_dir
from ..core.errors import ErrorCategory, MediaError
from ..core.logging import get_logger

__all__ = [
    "BUILTIN_SCHEMES",
    "ENTRY_POINT_GROUP",
    "SCHEMA",
    "check_schema",
    "credentials_header",
    "credentials_path",
    "migrate_file",
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
    return _migrate(data, path)


def check_schema(data: dict, path: Path) -> int:
    """Validate a parsed credentials file's ``schema``, and return it.

    Public because reading is not the only thing that has to understand the layout:
    ``init`` merges new accounts into whatever is already on disk, and merging into a
    file it cannot read correctly would rewrite it in an older shape — losing exactly
    the keys this file exists to keep. One check, both callers.

    A newer file gets its own answer. "Upgrade" and "re-run setup" are opposite
    instructions, and giving the second one to somebody whose file is *ahead* of their
    CLI talks them into overwriting the good copy.

    An *older* file is not this function's business: it validates the number and hands
    it back, and :func:`_migrate` decides whether it can be converted. Two questions,
    two places — "is this an integer from a build I can read" and "can I get from there
    to here" have different answers and different remedies.
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
    return declared


def _migrate(data: dict, path: Path) -> dict:
    """Bring a parsed credentials file up to :data:`SCHEMA`, in memory only.

    The same door as ``config.toml``'s, with one asymmetry that matters: the answer to
    a refusal there is "re-run setup", and here there is no such answer — the keys came
    from somewhere else. So the message never suggests starting over, and a document
    with no conversion says only what is true, which is that this build cannot read it.
    """
    from ..core.migrations import CREDENTIALS, UNMIGRATABLE, plan

    frm = check_schema(data, path)
    if frm == SCHEMA:
        return data
    steps = plan(CREDENTIALS, frm, SCHEMA)
    if steps and all(s.lossless for s in steps):
        for step in steps:
            data = step.apply(data)
        return data

    head = f"{path} is written in {_RESERVED} {frm}; this build reads {SCHEMA}"
    if steps:
        detail = (f". Converting it needs a decision that cannot be made for you — "
                  f"run `{cmd('config', 'migrate')}` to see it.")
    elif why := UNMIGRATABLE[CREDENTIALS].get(frm):
        detail = f" ({why}). The keys in it have to be re-entered by hand."
    else:
        detail = (f" and has no conversion for it. Upgrade {cli_name()} if a later build has one — "
                  "do not delete the file, its keys may not be reissuable.")
    raise MediaError(head + detail, category=ErrorCategory.AUTH, code="credentials_schema_outdated")


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

#: Schemes resolved in-process by :mod:`media_ai.credentials.reference`. Anything else
#: goes to a backend. Here rather than there because this module owns the namespace a
#: backend registers into, and two copies of the list would eventually disagree.
BUILTIN_SCHEMES = ("env", "cred", "keychain", "broker")

#: The entry-point group a distribution declares a scheme in. The entry point's *name*
#: is the scheme, so ``registered_schemes`` can list what exists without importing any
#: of it — which is what lets the "no backend for scheme" error name a plugin that is
#: installed but was never reached.
ENTRY_POINT_GROUP = "media_ai.credentials"

_BACKENDS: dict[str, Callable[[str], str]] = {}
_LOADED: dict[str, Callable[[str], str] | None] = {}
_SHADOW_WARNED: set[str] = set()


def register_secret_backend(scheme: str, fn: Callable[[str], str]) -> None:
    """Teach the resolver a reference scheme (``op``, ``vault``, ``aws-sm``, …).

    The built-in schemes are handled directly; everything else arrives here, so a
    deployment adds its own vault without this project taking a dependency on it.

    In-process, and therefore for something already running: a test, an embedder, a
    program using this as a library. **An installed CLI has no moment at which to call
    it**, which is why the same thing can be declared instead — see
    :data:`ENTRY_POINT_GROUP`. Registration wins over a declaration for the same scheme,
    whichever happens first: it is the more specific statement, and an embedder that
    made one is not asking to be overruled by whatever happens to be pip-installed.
    """
    _BACKENDS[scheme] = fn


def _declared() -> dict[str, object]:
    """Entry points in :data:`ENTRY_POINT_GROUP`, by scheme name, **not loaded**.

    Enumerating reads distribution metadata; loading imports the plugin. Keeping them
    apart is what makes listing the known schemes cheap enough to do inside an error
    message, and means a plugin nobody's reference names is never imported at all.
    """
    from importlib.metadata import entry_points

    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - very old importlib API
        eps = entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    found = {}
    for ep in eps:
        name = getattr(ep, "name", "")
        if name in BUILTIN_SCHEMES:
            # Declared but unreachable: `_reveal` handles the builtins before any
            # backend is consulted. Said out loud, once, because the alternative is a
            # plugin that is installed, correct, and simply never called.
            if name not in _SHADOW_WARNED:
                _SHADOW_WARNED.add(name)
                get_logger().warning(
                    "credential plugin %r declares the built-in scheme %r, which is resolved "
                    "directly and will never reach it; rename the entry point", name, name,
                )
            continue
        found[name] = ep
    return found


def secret_backend(scheme: str) -> Callable[[str], str] | None:
    """The backend for ``scheme``: registered in-process, else declared, else ``None``."""
    if scheme in _BACKENDS:
        return _BACKENDS[scheme]
    if scheme not in _LOADED:
        ep = _declared().get(scheme)
        try:
            _LOADED[scheme] = ep.load() if ep is not None else None
        except Exception as exc:  # noqa: BLE001 - one broken plugin must not break the CLI
            # Cached as absent, so a plugin that fails to import fails once and the
            # caller gets the ordinary "no backend for this scheme" refusal.
            get_logger().warning("skipping credential plugin %r: %s", scheme, exc)
            _LOADED[scheme] = None
    return _LOADED[scheme]


def registered_schemes() -> tuple[str, ...]:
    """Every non-built-in scheme this installation could resolve, registered or declared."""
    return tuple(sorted({*_BACKENDS, *_declared()}))


def reset_backends() -> None:
    """Forget what was loaded from entry points. For tests."""
    _LOADED.clear()
    _SHADOW_WARNED.clear()


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
        # Converted before the merge and stamped after. Before, because merging into a
        # file this build cannot read correctly would rewrite it in the older shape and
        # take the keys with it — the one thing here a user cannot reconstruct. After,
        # because what comes out was written by *this* build, whatever went in claimed.
        # A file the conversion cannot handle raises here, with the original untouched.
        existing = _migrate(existing, path)
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
        # Ceiling, because this write is also the documented repair for a loose mode:
        # inheriting it would leave a world-readable copy of every key beside a file
        # this call just fixed. See `tomlwrite.backup`.
        saved = backup(path, mode_ceiling=0o600)
    write_private(path, text)
    return saved


@dataclass(frozen=True)
class MigrationReport:
    """What converting this file found, and what it did about it."""

    path: Path
    present: bool
    frm: int
    to: int
    steps: tuple[str, ...]
    applied: bool
    backup: Path | None = None


def migrate_file(*, dry_run: bool = False) -> MigrationReport:
    """Convert ``credentials.toml`` to :data:`SCHEMA`, in place. Driven by ``config migrate``.

    An absent file is reported, not raised: this runs beside the config's own
    conversion, and a machine that keeps every key in the environment legitimately has
    no such file. Failing the whole command over it would make ``config migrate``
    unusable exactly where nothing is wrong.

    The converted document goes back out through :func:`save_accounts` rather than
    straight to disk. That is the one writer, so this inherits its rules by
    construction instead of by remembering them: the accounts are validated before
    anything is written (a bad step fails with the original still there), the file
    lands 0600, and the backup is capped at 0600 rather than inheriting a mode the
    rewrite may be repairing.
    """
    path = credentials_path()
    if not path.is_file():
        return MigrationReport(path=path, present=False, frm=SCHEMA, to=SCHEMA, steps=(), applied=False)

    data = _existing_for_write(path)
    frm = check_schema(data, path)
    if frm == SCHEMA:
        return MigrationReport(path=path, present=True, frm=frm, to=SCHEMA, steps=(), applied=False)

    from ..core.migrations import CREDENTIALS, plan

    steps = plan(CREDENTIALS, frm, SCHEMA)
    if steps is None:
        # Same message the read path gives, from the one function that composes it.
        _migrate(data, path)
        raise AssertionError("unreachable: _migrate raises for a chain it cannot complete")

    for step in steps:
        data = step.apply(data)
    accounts = {name: value for name, value in data.items() if name != _RESERVED}
    summaries = tuple(s.summary for s in steps)
    if dry_run:
        render_accounts(accounts, replace=True)  # validate only; nothing is written
        return MigrationReport(path=path, present=True, frm=frm, to=SCHEMA, steps=summaries, applied=False)
    saved = save_accounts(accounts, replace=True)
    return MigrationReport(path=path, present=True, frm=frm, to=SCHEMA,
                           steps=summaries, applied=True, backup=saved)
