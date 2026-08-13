"""``~/.config/<brand>/config.toml`` — which bindings exist here, and which are default.

Two tables, both non-secret::

    schema = 2

    [bindings."volc-ark/seedance-2.0"]
    endpoint_id = "ep-example-endpoint"
    base_url   = "https://ark.cn-beijing.volces.com/api/v3"
    credential = "env://ARK_API_KEY"

    [defaults]
    "video.text_to_video" = "volc-ark/seedance-2.0"

Each entry is self-contained: its own endpoint, its own credential reference. Two
bindings on one account repeat the reference, and rotating that key is two edits.
That is the deliberate trade — the question this file has to answer fastest is
"what is *this* binding doing?", and an indirection that saves a line of typing
costs a hop every time something breaks.

``extends`` is the one indirection, and it is about *capabilities*, not credentials::

    [bindings."volc-ark-sg/seedance-2.0"]     # a second account or region
    extends    = "volc-ark/seedance-2.0"
    base_url   = "https://ark.ap-southeast.volces.com/api/v3"
    credential = "cred://volc-ark-sg"

    [bindings."volc-ark/my-endpoint"]         # an opaque deployment id
    extends    = "volc-ark/seedream-4.5"      # …whose capabilities are the real model's
    endpoint_id = "ep-example-endpoint"

One mechanism covers multi-account, multi-region, and deployment ids, because all
three are "the same declared capabilities, reached differently".

**Defaults are keyed by scene** so a call with neither ``--provider`` nor ``--model``
still resolves. That is the whole of what this file does about picking a binding: it
names one per scene. It never picks a *different* one because the first failed.

Versioning: a one-way door
--------------------------

``schema`` is a monotonic integer, not the release version. A file format has no
"new feature" tier — a reader either understands a document or it does not — so the
question it has to answer is a boolean, and a semver here would only ask every reader
to do a range comparison to arrive at one.

The two directions are deliberately not symmetric:

**A newer build reads an older file.** It migrates (:func:`_migrate`) and carries on.
There is no chain registered yet, so today this only accepts the current schema and
refuses anything older with the same "re-run setup" answer as before — but the hook is
where a real migration lands, rather than being invented in a hurry beside the first
change that needs one.

**An older build reads a newer file.** It refuses, with ``config_from_newer_build``,
and says to upgrade. This is not the same failure as an outdated file and must not
share its message: the user is holding something *ahead* of their CLI — a second
machine, an old virtualenv, a downgrade — and "re-run setup" would talk them into
overwriting the good file with a worse one.

Unknown fields are preserved
----------------------------

Every table and key this build does not recognise is kept on the :class:`Config` and
written back out verbatim. :func:`render_config` rebuilds the whole file from the
parsed object, so anything not modelled here is *deleted* by the next ``bindings add``
— and that is precisely how a field added in a later release would disappear on a
machine that still has an older build, silently, in a command about something else.

This is what makes it safe for a later version to add an optional table (an update
preference, a marker naming where a managed entry came from) without bumping
``schema``: an old build ignores it, but no longer eats it. The bump then buys the
thing it is actually for — refusing a file whose *meaning* has changed — instead of
being the only defence against field loss.

Preservation only reaches as far as the writer does. ``tomlwrite.dumps`` takes
strings, integers, booleans, lists of strings and nested tables; a value outside that
subset (a float, a datetime, an array of tables) makes :func:`save_config` refuse,
naming the field. Refusing beats dropping: dropping is the failure this section
exists to remove, and it is invisible.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..brand import cli_name, cmd, config_dir
from ..credentials.reference import is_reference
from .errors import ErrorCategory, MediaError
from .scene import Scene

__all__ = ["Config", "UserBinding", "config_path", "load_config", "render_config"]

SCHEMA = 2


def config_path() -> Path:
    return Path(os.getenv("MEDIA_CONFIG_FILE") or config_dir() / "config.toml").expanduser()


@dataclass(frozen=True)
class UserBinding:
    """One configured binding: where to reach it and which key to use."""

    id: str
    extends: str | None = None
    model_id: str | None = None
    endpoint_id: str | None = None
    base_url: str | None = None
    credential: str | None = None
    options: dict = field(default_factory=dict)
    #: Keys in this binding's table that this build does not model, kept so writing
    #: the config back does not delete them. See the module docstring.
    extra: dict = field(default_factory=dict)

    def merged_with(self, **changes) -> UserBinding:
        """This entry with only the named fields replaced; ``None`` means "leave alone".

        Every writer that edits one field of an existing binding goes through here.
        Rebuilding the entry from the arguments a command happened to receive is what
        made `bindings add <id> --credential …` delete a hand-configured `endpoint_id`
        (an account-specific `ep-…` endpoint), `base_url` and `options` — and that is
        the command every resolution error hints at, so rotating a key silently
        un-configured the binding whose key was being rotated.

        Clearing a field is therefore deliberate and explicit: pass ``""``.
        """
        kept = {
            f: (getattr(self, f) if changes.get(f) is None else (changes[f] or None))
            for f in ("extends", "model_id", "endpoint_id", "base_url", "credential")
        }
        options = self.options if changes.get("options") is None else changes["options"]
        # `extra` rides along for the same reason every other unnamed field does: this
        # is the one path that edits an existing entry, so dropping it here would undo
        # the preservation at exactly the moment it is needed.
        return UserBinding(id=self.id, options=dict(options or {}), extra=dict(self.extra), **kept)


@dataclass(frozen=True)
class UpdateSettings:
    """``[update]`` — whether this machine looks for a newer release, and where.

    Two fields, and the second one is why this is in the config rather than only in the
    environment: an internal distribution points every install at its own mirror once,
    at setup, and a variable every shell has to export is not that.

    There is no interval knob. Nobody has asked for one, the constant it would replace
    is read in exactly one place, and a setting that exists before its use case does is
    a setting whose default nobody has had a reason to think about.
    """

    check: bool = True
    feed: str | None = None


@dataclass(frozen=True)
class Config:
    bindings: dict[str, UserBinding] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)  # scene value -> binding id
    path: Path | None = None
    exists: bool = False
    #: Top-level keys and tables this build does not model, kept verbatim so writing
    #: the config back does not delete them. See the module docstring.
    update: UpdateSettings = field(default_factory=UpdateSettings)
    extra: dict = field(default_factory=dict)

    def default_for(self, scene: Scene) -> str | None:
        return self.defaults.get(scene.value)

    def merged_with(self, **changes) -> Config:
        """This config with only the named fields replaced.

        The same rule as :meth:`UserBinding.merged_with`, one level up, and it exists
        for the same reason: every writer used to rebuild the object from the fields it
        happened to care about — ``bindings add`` from ``bindings``, ``config
        set-default`` from ``defaults`` — so a field none of them named was dropped by
        all of them. That is how ``extra`` would have been lost on the first write,
        undoing the preservation it was added for. A field added after this one is
        carried by construction rather than by three call sites remembering.
        """
        return replace(self, **changes)


def _fail(msg: str, *, code: str = "config_invalid") -> MediaError:
    return MediaError(msg, category=ErrorCategory.CLI, code=code)


#: The keys this build models inside a ``[bindings."…"]`` table. Anything else is kept
#: on :attr:`UserBinding.extra` rather than dropped — see the module docstring.
_BINDING_KEYS = frozenset({"extends", "model_id", "endpoint_id", "base_url", "credential", "options"})

#: The top-level keys this build models. Same rule, one level up.
_TOP_KEYS = frozenset({"schema", "bindings", "defaults", "update"})


def _parse_binding(bid: str, raw: object, path: Path) -> UserBinding:
    if not isinstance(raw, dict):
        raise _fail(f"{path}: [bindings.\"{bid}\"] must be a table")
    # Every one of these reaches code that assumes a string — `base_url` is `.rstrip`-ed
    # into the HTTP client, `model_id` goes on the wire. A hand-edited `base_url = 8080`
    # used to surface, one command later, as exit 1 `unknown` reading "'int' object has
    # no attribute 'rstrip'", naming neither the file nor the field. This file is
    # hand-editable, so a typo in it is a config error like any other.
    for field_name in ("extends", "model_id", "endpoint_id", "base_url"):
        value = raw.get(field_name)
        if value is not None and (not isinstance(value, str) or not value):
            raise _fail(f'{path}: [bindings."{bid}"].{field_name} must be a non-empty string, got {value!r}')
    credential = raw.get("credential")
    if credential is not None:
        if not isinstance(credential, str) or not credential:
            raise _fail(f'{path}: [bindings."{bid}"].credential must be a string')
        if not is_reference(credential):
            raise _fail(
                f'{path}: [bindings."{bid}"].credential must be a reference such as '
                'env://VAR, cred://<account> or keychain://<name> — never a raw key. '
                "This file is the shareable one; raw keys belong in credentials.toml.",
                code="credential_is_raw_key",
            )
    options = raw.get("options", {})
    if not isinstance(options, dict):
        raise _fail(f'{path}: [bindings."{bid}"].options must be a table')
    model_id = raw.get("model_id")
    endpoint_id = raw.get("endpoint_id")
    if model_id is not None and endpoint_id is not None:
        raise _fail(
            f'{path}: [bindings."{bid}"] must use either model_id or endpoint_id, not both',
            code="wire_id_ambiguous",
        )
    return UserBinding(
        id=bid,
        extends=raw.get("extends"),
        model_id=model_id,
        endpoint_id=endpoint_id,
        base_url=raw.get("base_url"),
        credential=credential,
        options=dict(options),
        extra={k: v for k, v in raw.items() if k not in _BINDING_KEYS},
    )


def _parse_update(raw: object, path: Path) -> UpdateSettings:
    """Validate ``[update]``, or return the defaults when it is absent.

    Type-checked like every other hand-editable field here: ``check = "no"`` is a
    string, which is truthy, so an unchecked read would turn an attempt to *disable*
    checking into leaving it on — the one mistake this setting exists to prevent.
    """
    if raw is None:
        return UpdateSettings()
    if not isinstance(raw, dict):
        raise _fail(f"{path}: [update] must be a table")
    check = raw.get("check", True)
    if not isinstance(check, bool):
        raise _fail(f"{path}: [update].check must be true or false, got {check!r}")
    feed = raw.get("feed")
    if feed is not None and (not isinstance(feed, str) or not feed):
        raise _fail(f"{path}: [update].feed must be a non-empty URL, got {feed!r}")
    return UpdateSettings(check=check, feed=feed)


def _reject_v1(data: dict, path: Path) -> None:
    """A config from before the binding refactor cannot be read, and says so.

    Silently ignoring ``[profiles]``/``[providers.x]`` would leave a user with a
    configured-looking file and a CLI that reports nothing configured. There is no
    migration by design (an install is a fresh start), so the actionable answer is to
    re-run setup.
    """
    stale = [name for name in ("profiles", "providers") if name in data]
    if stale:
        raise _fail(
            f"{path} uses the pre-binding format ([{'], ['.join(stale)}]). Configuration is now "
            f"per binding — run `{cmd('init')}` to write it, or delete the file to start over.",
            code="config_schema_outdated",
        )


def _declared_schema(data: dict, path: Path) -> int:
    """Which schema ``data`` is written in.

    An absent ``schema`` is read as the current one, because a minimal hand-written
    file should work without ceremony — and every write puts the key back, so the
    ambiguity is met once per file at most. It is only safe because the one older
    layout that exists is recognisable by shape (:func:`_reject_v1` reads
    ``[profiles]``/``[providers.x]``), which is the fallback whenever a version field
    is missing: identify the document, do not assume the newest.
    """
    declared = data.get("schema", SCHEMA)
    # bool first: it is an int subclass, and `schema = true` reaching the comparisons
    # below would be read as schema 1.
    if isinstance(declared, bool) or not isinstance(declared, int):
        raise _fail(f"{path}: schema must be an integer, got {declared!r}")
    return declared


def _migrate(data: dict, *, frm: int, path: Path) -> dict:
    """Bring a document written in schema ``frm`` up to :data:`SCHEMA`.

    The hook exists ahead of anything to run in it, and refuses rather than pretending:
    an unmigratable file gets the same actionable answer it did before. When the first
    real migration lands it registers here, with the change that needs it, instead of
    being designed in the abstract now.
    """
    if frm == SCHEMA:
        return data
    raise _fail(
        f"{path} is written in schema {frm}; this build reads schema {SCHEMA} and has no "
        f"migration for it. Run `{cmd('init')}` to write it again, or delete the file to start over.",
        code="config_schema_outdated",
    )


def load_config(path: Path | None = None) -> Config:
    """Read the config, or return an empty one when there is no file.

    An absent file is a legitimate state: a fresh install can still run the bindings
    that need no credential.
    """
    path = path or config_path()
    if not path.is_file():
        return Config(path=path, exists=False)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise _fail(f"could not read {path}: {exc}") from exc

    _reject_v1(data, path)
    schema = _declared_schema(data, path)
    if schema > SCHEMA:
        # The file is ahead of the CLI, so the fix is upgrading — never re-running
        # setup, which would overwrite a good file with one this build can express.
        raise _fail(
            f"{path} was written by a newer build (schema {schema}; this one reads {SCHEMA}). "
            f"Upgrade {cli_name()}, or point $MEDIA_CONFIG_FILE at a different file.",
            code="config_from_newer_build",
        )
    data = _migrate(data, frm=schema, path=path)

    raw_bindings = data.get("bindings", {})
    if not isinstance(raw_bindings, dict):
        raise _fail(f"{path}: [bindings] must be a table")
    bindings = {bid: _parse_binding(bid, raw, path) for bid, raw in raw_bindings.items()}

    raw_defaults = data.get("defaults", {})
    if not isinstance(raw_defaults, dict):
        raise _fail(f"{path}: [defaults] must be a table")
    defaults: dict[str, str] = {}
    for key, value in raw_defaults.items():
        try:
            Scene(key)
        except ValueError:
            raise _fail(f"{path}: [defaults] has no scene named {key!r}") from None
        if not isinstance(value, str):
            raise _fail(f'{path}: [defaults]."{key}" must be a binding id')
        defaults[key] = value

    return Config(
        bindings=bindings,
        defaults=defaults,
        path=path,
        exists=True,
        update=_parse_update(data.get("update"), path),
        extra={k: v for k, v in data.items() if k not in _TOP_KEYS},
    )


def save_config(config: Config, *, header: str | None = None) -> Path | None:
    """Write the config file, backing up whatever was there. Returns the backup path.

    Every command that edits the config goes through here, so "the previous file is
    kept" is a property of the config rather than of whoever remembered. It matters
    because :func:`render_config` cannot round-trip comments: an edit to one field
    rewrites the whole file, and a hand-written note explaining why a binding points at
    a particular endpoint is otherwise gone with no way back.
    """
    from ..credentials.tomlwrite import backup, write_public

    path = config_path()
    # Rendered before the backup, so a file that cannot be written leaves nothing
    # behind — neither a `.bak` for a write that never happened nor a truncated file.
    text = render_config(config, header=header)
    saved = backup(path)
    write_public(path, text)
    return saved


def render_config(config: Config, *, header: str | None = None) -> str:
    """Serialize a :class:`Config` back to TOML.

    Written by ``init`` and ``config set-default``. Comments are not
    preserved — callers back the previous file up before replacing it.

    Fields this build does not model are written back from ``extra``; a value the
    writer's subset cannot express raises rather than being dropped.
    """
    from ..credentials.tomlwrite import TomlWriteError, dumps

    data: dict = {"schema": SCHEMA}
    if config.bindings:
        data["bindings"] = {
            bid: {
                # Unknown keys first so a modelled field always wins the collision: a
                # stale `credential` left in `extra` by some future rename must not be
                # able to overwrite the one this build parsed and validated.
                **b.extra,
                **{
                    k: v
                    for k, v in (
                        ("extends", b.extends),
                        ("model_id", b.model_id),
                        ("endpoint_id", b.endpoint_id),
                        ("base_url", b.base_url),
                        ("credential", b.credential),
                        ("options", b.options or None),
                    )
                    if v is not None
                },
            }
            for bid, b in sorted(config.bindings.items())
        }
    if config.defaults:
        data["defaults"] = dict(sorted(config.defaults.items()))
    # Written only when it says something. A table of defaults in every config file is
    # noise that invites editing settings nobody chose, and an absent one already means
    # exactly what the defaults say.
    if (update := config.update) != UpdateSettings():
        data["update"] = {
            k: v for k, v in (("check", update.check), ("feed", update.feed))
            if v != getattr(UpdateSettings(), k)
        }
    for key, value in config.extra.items():
        data.setdefault(key, value)
    try:
        return dumps(data, header=header)
    except TomlWriteError as exc:
        # Reached by a hand-written value outside the writer's subset — a float
        # timeout in `options`, a datetime, an array of tables. The alternative is
        # dropping it, which is the failure `extra` exists to remove, so this refuses
        # and names the field rather than quietly writing a smaller file.
        raise _fail(
            f"{config.path or config_path()} holds a value this build cannot write back ({exc}). "
            "Remove or quote it, then re-run.",
            code="config_unwritable_field",
        ) from exc
