"""The **configuration bundle** — one file that provisions a machine without the wizard.

``media-ai init`` is a conversation, and a production instance cannot have one. A
bundle is the same outcome as a written artifact: the bindings, the scene defaults and
(when asked for) the credential accounts, in one self-describing document that
``media-ai config import`` applies::

    schema     = 1                        # the envelope
    created_by = "media-ai 0.5.2"
    created_at = "2026-08-03T09:14:22Z"

    [config]                              # verbatim config.toml, schema and all
    schema = 2
    [config.bindings."volc-ark/seedance-2.0"]
    credential = "cred://volc-ark/seedance-2.0"
    [config.defaults]
    "video.text_to_video" = "volc-ark/seedance-2.0"

    [credentials."volc-ark/seedance-2.0"] # only with --include-credentials
    api_key = "…"

Four rules hold it together:

- **Export never resolves a credential.** It copies what is at rest — a reference stays
  a reference, an account's raw key stays a raw key. Materialising ``env://ARK_API_KEY``
  into a literal would silently answer a question nobody asked ("where does this key
  come from?") with a different answer on the target than on the source, and a binding
  whose source changed without being told is exactly what one-binding-one-source exists
  to prevent. A bundle is a *move*, not a transformation.
- **It carries the accounts its bindings name, and no others** — following ``cred://``
  chains, since an account whose ``api_key`` is itself a reference is useless on the
  target without the account it points at. An account no exported binding uses is
  reported as omitted rather than shipped: a key that travels for no reason is a key
  in one more place.
- **Payloads are the real documents.** ``[config]`` is what ``config.toml`` holds,
  written by the same serializer and read back by the same parser (:func:`parse_config`),
  so a bundle cannot accept a config the CLI would refuse — a raw key in ``[config]`` is
  rejected here exactly as it is there.
- **The envelope is versioned, and so is each payload.** A bundle exists to cross
  between installs, so the versions it carries will differ from the ones reading it
  sooner or later; :mod:`media_ai.core.migrate` decides what that means. This is the one
  document in the project that migrates rather than refusing, because it is the only one
  whose whole purpose is to be read somewhere else.

Adding a section later (installed skills, a usage-ledger destination) is a new key plus
a migration step — which is why the envelope holds the payloads in named tables rather
than being a config file with extras bolted on.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..credentials.redaction import register_secret
from ..credentials.reference import is_reference, split_reference
from ..credentials.stores import account_key
from .config import SCHEMA as CONFIG_SCHEMA
from .config import Config, config_payload, parse_config
from .errors import ErrorCategory, MediaError
from .migrate import Migrations

__all__ = [
    "BUNDLE_MIGRATIONS",
    "BUNDLE_SCHEMA",
    "CONFIG_MIGRATIONS",
    "AccountSelection",
    "Bundle",
    "parse_bundle",
    "referenced_accounts",
    "render_bundle",
    "select",
    "utc_now",
]

#: The envelope's version. Bump it when the *shape* changes incompatibly, and register
#: the step that upgrades the old shape — see :mod:`media_ai.core.migrate`.
BUNDLE_SCHEMA = 1

BUNDLE_MIGRATIONS = Migrations("bundle", target=BUNDLE_SCHEMA)
#: The ``[config]`` payload's own chain. A bundle written by an older release carries an
#: older ``config.schema``; upgrading it here is what lets one bundle provision a fleet
#: that is mid-rollout. ``config.toml`` on disk deliberately does *not* migrate — a file
#: belongs to the install that wrote it, and an install is a fresh start.
CONFIG_MIGRATIONS = Migrations("config", target=CONFIG_SCHEMA)


def utc_now() -> str:
    """A second-resolution UTC stamp for ``created_at``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Bundle:
    """A parsed or freshly collected bundle: the config, and optionally the accounts."""

    config: Config
    accounts: dict[str, dict] = field(default_factory=dict)
    created_by: str = ""
    created_at: str = ""
    schema: int = BUNDLE_SCHEMA
    source: str = ""

    def __post_init__(self) -> None:
        # Any plaintext key that passes through this process is registered with the
        # redactor, in both directions. A bundle is the one place raw keys are handled
        # in bulk, and the cost of missing one is that it lands in a log or an error
        # message; registering here means every sink masks it whatever route it takes.
        for section in self.accounts.values():
            value = account_key(section)
            if value and not is_reference(value):
                register_secret(value)

    @property
    def carries_credentials(self) -> bool:
        """Whether the file needs 0600 — true as soon as there is an account in it.

        Deliberately not "does any account hold a *literal* key": an all-``op://``
        bundle would then be written world-readable, and the next account added to it
        would silently change the mode of a file somebody had already decided where to
        put. The section's presence is the fact worth switching on.
        """
        return bool(self.accounts)


@dataclass(frozen=True)
class AccountSelection:
    """Which accounts a bundle takes, and the two ways that answer can surprise you."""

    kept: dict[str, dict] = field(default_factory=dict)
    #: In ``credentials.toml``, named by nothing the bundle exports. Left behind.
    omitted: tuple[str, ...] = ()
    #: Named by an exported binding, not carried. The target must supply these itself,
    #: or every call through that binding fails with ``credential_unresolved``.
    missing: tuple[str, ...] = ()


def select(config: Config, ids: list[str] | None) -> Config:
    """``config`` narrowed to ``ids`` — all of it when none are named.

    A scene default survives if the binding it names survives, **or** if that binding
    was never in ``[bindings]`` to begin with: ``video.concat`` points at
    ``local/ffmpeg``, which needs no configuration and is therefore not something a
    ``--binding`` filter can be said to have excluded.
    """
    if not ids:
        return config
    unknown = sorted(set(ids) - set(config.bindings))
    if unknown:
        raise MediaError(
            f"not configured here, so there is nothing to export: {', '.join(unknown)}",
            category=ErrorCategory.CLI, code="binding_not_configured",
            details={"configured": sorted(config.bindings)},
            hint="media-ai config show",
        )
    keep = set(ids)
    return Config(
        bindings={bid: b for bid, b in config.bindings.items() if bid in keep},
        defaults={
            scene: bid for scene, bid in config.defaults.items()
            if bid in keep or bid not in config.bindings
        },
        path=config.path, exists=config.exists,
    )


def _cred_name(reference: str | None) -> str | None:
    """The account a ``cred://`` reference names, or ``None`` for any other source."""
    if not reference or not is_reference(reference):
        return None
    scheme, rest = split_reference(reference)
    return rest if scheme == "cred" and rest else None


def referenced_accounts(config: Config, accounts: dict[str, dict]) -> AccountSelection:
    """The accounts ``config``'s bindings actually name, chains followed.

    Least privilege, and it is the export's only decision about secrets: a bundle
    carries the keys its bindings use, reports the ones it left behind, and reports the
    ones it could not find. Called with ``accounts={}`` — a credential-free export — the
    third list becomes "what this bundle expects the target to already have", which is
    the same sentence and just as worth printing.
    """
    kept: dict[str, dict] = {}
    missing: list[str] = []
    queue = [name for name in (_cred_name(b.credential) for b in config.bindings.values()) if name]
    while queue:
        name = queue.pop()
        if name in kept or name in missing:
            continue
        section = accounts.get(name)
        if section is None:
            missing.append(name)
            continue
        kept[name] = dict(section)
        if nxt := _cred_name(account_key(section)):
            queue.append(nxt)
    return AccountSelection(
        kept={name: kept[name] for name in sorted(kept)},
        omitted=tuple(sorted(set(accounts) - set(kept))),
        missing=tuple(sorted(missing)),
    )


def render_bundle(bundle: Bundle, *, header: str | None = None) -> str:
    """Serialize a bundle to TOML."""
    from ..credentials.tomlwrite import TomlWriteError, dumps

    data: dict = {
        "schema": bundle.schema,
        "created_by": bundle.created_by,
        "created_at": bundle.created_at,
        "config": config_payload(bundle.config),
    }
    if bundle.accounts:
        data["credentials"] = {name: dict(section) for name, section in sorted(bundle.accounts.items())}
    try:
        return dumps(data, header=header)
    except TomlWriteError as exc:
        # The writer supports a narrow subset and refuses the rest rather than mangling
        # it. Refusing here — before a file exists — beats writing a bundle that imports
        # as something subtly different from what was exported.
        raise MediaError(
            f"this configuration holds a value the bundle writer cannot round-trip ({exc}); "
            "simplify it in config.toml, then export again",
            category=ErrorCategory.CLI, code="bundle_unwritable",
        ) from exc


def parse_bundle(text: str, *, source: str) -> Bundle:
    """Read a bundle, upgrading it to the shapes this build understands, or refuse."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise MediaError(
            f"{source} is not valid TOML: {exc}",
            category=ErrorCategory.CLI, code="bundle_invalid",
        ) from exc

    data = BUNDLE_MIGRATIONS.upgrade(data, source=source)
    raw_config = data.get("config")
    if not isinstance(raw_config, dict):
        raise MediaError(
            f"{source}: a bundle needs a [config] table",
            category=ErrorCategory.CLI, code="bundle_invalid",
            hint="media-ai config export --output <file>   # on the source machine",
        )
    where = f"{source} [config]"
    config = parse_config(CONFIG_MIGRATIONS.upgrade(raw_config, source=where), source=where)
    return Bundle(
        config=config,
        accounts=_parse_accounts(data.get("credentials", {}), source=source),
        created_by=str(data.get("created_by") or ""),
        created_at=str(data.get("created_at") or ""),
        schema=BUNDLE_SCHEMA,
        source=source,
    )


def _parse_accounts(raw: object, *, source: str) -> dict[str, dict]:
    if not isinstance(raw, dict):
        raise MediaError(
            f"{source}: [credentials] must be a table of accounts",
            category=ErrorCategory.AUTH, code="bundle_invalid",
        )
    out: dict[str, dict] = {}
    for name, section in raw.items():
        if not isinstance(section, dict):
            raise MediaError(
                f'{source}: [credentials."{name}"] must be a table',
                category=ErrorCategory.AUTH, code="bundle_invalid",
            )
        if not account_key(section):
            # An account with no key would import cleanly and fail at call time as
            # `credential_unresolved`, one command and one machine away from the file
            # that is actually wrong.
            raise MediaError(
                f'{source}: [credentials."{name}"] has no api_key',
                category=ErrorCategory.AUTH, code="bundle_invalid",
            )
        out[name] = dict(section)
    return out
