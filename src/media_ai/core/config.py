"""``~/.config/media-ai/config.toml`` — which bindings exist here, and which are default.

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
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..credentials.reference import is_reference
from .errors import ErrorCategory, MediaError
from .scene import Scene

__all__ = ["Config", "UserBinding", "config_path", "load_config", "render_config"]

SCHEMA = 2


def config_path() -> Path:
    return Path(os.getenv("MEDIA_CONFIG_FILE", "~/.config/media-ai/config.toml")).expanduser()


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

    def merged_with(self, **changes) -> "UserBinding":
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
        return UserBinding(id=self.id, options=dict(options or {}), **kept)


@dataclass(frozen=True)
class Config:
    bindings: dict[str, UserBinding] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)  # scene value -> binding id
    path: Path | None = None
    exists: bool = False

    def default_for(self, scene: Scene) -> str | None:
        return self.defaults.get(scene.value)


def _fail(msg: str, *, code: str = "config_invalid") -> MediaError:
    return MediaError(msg, category=ErrorCategory.CLI, code=code)


def _parse_binding(bid: str, raw: object, path: Path) -> UserBinding:
    if not isinstance(raw, dict):
        raise _fail(f"{path}: [bindings.\"{bid}\"] must be a table")
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
    )


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
            "per binding — run `media-ai init` to write it, or delete the file to start over.",
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
    schema = data.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise _fail(
            f"{path} declares schema = {schema!r}; this build reads schema = {SCHEMA}",
            code="config_schema_outdated",
        )

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

    return Config(bindings=bindings, defaults=defaults, path=path, exists=True)


def save_config(config: "Config", *, header: str | None = None) -> Path | None:
    """Write the config file, backing up whatever was there. Returns the backup path.

    Every command that edits the config goes through here, so "the previous file is
    kept" is a property of the config rather than of whoever remembered. It matters
    because :func:`render_config` cannot round-trip comments: an edit to one field
    rewrites the whole file, and a hand-written note explaining why a binding points at
    a particular endpoint is otherwise gone with no way back.
    """
    from ..credentials.tomlwrite import backup, write_public

    path = config_path()
    saved = backup(path)
    write_public(path, render_config(config, header=header))
    return saved


def render_config(config: Config, *, header: str | None = None) -> str:
    """Serialize a :class:`Config` back to TOML.

    Written by ``media-ai init`` and ``media-ai config set-default``. Comments are not
    preserved — callers back the previous file up before replacing it.
    """
    from ..credentials.tomlwrite import dumps

    data: dict = {"schema": SCHEMA}
    if config.bindings:
        data["bindings"] = {
            bid: {
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
            }
            for bid, b in sorted(config.bindings.items())
        }
    if config.defaults:
        data["defaults"] = dict(sorted(config.defaults.items()))
    return dumps(data, header=header)
