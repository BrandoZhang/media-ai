"""``media-ai config export`` / ``import`` — provisioning a machine without the wizard.

``init`` is a conversation, and a production instance cannot have one: there is no
terminal, and the answers are the same on every box in the fleet anyway. These two
commands are that conversation's outcome as an artifact — export once, from a machine
that is already right; import everywhere, from a file or a pipe.

The split follows the wizard's own rule, **decide everything, then write**: an import
parses, merges, validates against this build's catalog and renders *both* files before
either lands on disk. A bundle that is refused half-way changes nothing, which is what
makes re-running an import safe — and provisioning re-runs.

What an import is allowed to do to a machine is deliberately narrow:

- **It never invents a binding this build cannot explain.** A config naming a binding
  no manifest declares makes *every* later command fail (``binding_undeclared``, from
  ``available_bindings``) — including ``bindings list``, the one an operator would run
  to find out what happened. Importing a newer machine's bundle onto an older CLI is
  the normal way to reach that state, so it is refused up front, with ``--skip-unknown``
  to drop those entries and say which ones went.
- **It never leaves a default pointing at nothing.** Same reason, one level down: a
  default is what every unflagged call silently gets.
- **A bundle without a ``[credentials]`` section does not touch ``credentials.toml``**,
  including under ``--replace``. "Replace what is here with what I brought" cannot mean
  "delete the keys I did not bring".
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from .. import __version__
from ..core import bundle as fmt
from ..core.config import Config, config_path, load_config, render_config
from ..core.errors import ErrorCategory, MediaError
from ..core.logging import get_logger
from ..core.registry import catalog
from ..core.resolve import available_bindings
from ..core.result import SCHEMA_VERSION
from ..credentials import stores
from ..credentials.tomlwrite import TomlWriteError, atomic_write, dumps, write_if_changed, write_private, write_public

__all__ = ["export_bundle", "import_bundle"]


_SHAREABLE_HEADER = (
    "media-ai configuration bundle — written by `media-ai config export`.\n"
    "NON-SECRET: bindings and scene defaults. Every `credential` is a reference,\n"
    "never a key, so the target machine still supplies its own secrets.\n"
    "Apply it with: media-ai config import --input <this file>"
)
_SECRET_HEADER = (
    "media-ai configuration bundle — written by `media-ai config export --include-credentials`.\n"
    "SECRETS: this file carries credential accounts — usually the keys themselves.\n"
    "It is written chmod 600; move it over a private channel and delete it once\n"
    "imported.\n"
    "Apply it with: media-ai config import --input <this file>"
)
_IMPORTED_CONFIG_HEADER = (
    "media-ai config — written by `media-ai config import`.\n"
    "NON-SECRET: safe to share. `credential` is a reference, never a key."
)
_IMPORTED_CREDENTIALS_HEADER = (
    "media-ai credentials — written by `media-ai config import`.\n"
    "SECRETS: keep this file chmod 600; the CLI refuses to read it otherwise.\n"
    "Each [<name>] is an account a binding reaches by cred://<name>."
)


# --------------------------------------------------------------------------- export


def export_bundle(args) -> dict:
    config = load_config()
    if not config.bindings and not config.defaults:
        raise MediaError(
            "nothing to export: no bindings or scene defaults are configured here",
            category=ErrorCategory.CLI, code="nothing_to_export",
            hint="media-ai init",
        )

    selected = fmt.select(config, args.binding)
    # Read the accounts only when they are wanted. Otherwise a machine whose
    # credentials.toml has the wrong mode could not export the shareable half of its
    # configuration — which is the half that has nothing to do with the problem.
    accounts = stores.load_accounts() if args.include_credentials else {}
    picked = fmt.referenced_accounts(selected, accounts)
    bundle = fmt.Bundle(
        config=selected,
        accounts=picked.kept,
        created_by=f"media-ai {__version__}",
        created_at=fmt.utc_now(),
    )

    out = Path(args.output).expanduser()
    if out.exists() and not args.force:
        raise MediaError(
            f"{out} already exists",
            category=ErrorCategory.CLI, code="output_exists",
            hint=f"media-ai config export --output {out} --force",
        )
    # Header and mode switch on the same fact, so the file cannot describe itself as
    # shareable while being written 0600 — or the other way round.
    secret_bearing = bundle.carries_credentials
    text = fmt.render_bundle(bundle, header=_SECRET_HEADER if secret_bearing else _SHAREABLE_HEADER)
    mode = 0o600 if secret_bearing else 0o644
    # Deliberately not `write_private`: that narrows the *parent directory* to 0700,
    # which is right for ~/.config/media-ai and wrong for whatever directory the
    # operator chose here — exporting to /tmp would make /tmp unusable for everyone
    # else on the box. The file's own mode is the part that matters.
    atomic_write(out, text, mode=mode)

    if picked.missing:
        get_logger().warning(
            "the target must supply these credential accounts itself: %s", ", ".join(picked.missing),
        )
    return {
        "ok": True, "schema_version": SCHEMA_VERSION, "command": "config export",
        "output": str(out),
        "mode": f"{mode:o}",
        "bytes": out.stat().st_size,
        "bundle_schema": bundle.schema,
        "created_by": bundle.created_by,
        "created_at": bundle.created_at,
        "bindings": sorted(selected.bindings),
        "defaults": dict(sorted(selected.defaults.items())),
        # Account *names* only. The values are in the file, and nowhere else.
        "credentials": sorted(picked.kept),
        "omitted_credentials": list(picked.omitted),
        "missing_credentials": list(picked.missing),
        "carries_credentials": bundle.carries_credentials,
    }


# --------------------------------------------------------------------------- import


def import_bundle(args) -> dict:
    text, source = _read(args.input)
    bundle = fmt.parse_bundle(text, source=source)
    cat = catalog()
    existing = load_config()

    incoming, dropped_bindings, dropped = _declared_only(bundle.config, cat, skip_unknown=args.skip_unknown)
    # A binding the bundle carries replaces the local entry **whole**, where
    # `bindings add` merges field by field. The two commands are doing different
    # things: `add` edits one field of a binding that stays yours, while an import says
    # what a binding *is* somewhere else. Field-merging here would leave a local
    # `endpoint_id` — an account-specific `ep-…` — attached to an imported credential
    # from a different account, which is the one combination nothing would report.
    base = Config() if args.replace else existing
    merged = Config(
        bindings={**base.bindings, **incoming.bindings},
        defaults={**base.defaults, **incoming.defaults},
        path=config_path(), exists=True,
    )
    merged, unreachable = _reachable_defaults(merged, cat, skip_unknown=args.skip_unknown)
    dropped_defaults = dict(sorted((dropped | unreachable).items()))

    # A bundle carrying no [credentials] leaves credentials.toml alone, `--replace` or
    # not: "replace what is here with what I brought" cannot mean "delete the keys I
    # did not bring".
    carry = bool(bundle.accounts) and not args.skip_credentials
    before_accounts = stores.load_accounts() if carry else {}
    incoming_accounts = bundle.accounts if carry else {}
    accounts = {**({} if args.replace else before_accounts), **incoming_accounts}

    # Both files are rendered before either is written: `dumps` refuses values it
    # cannot round-trip, and raising after the config had landed would leave the
    # machine half-provisioned — the state this command exists to avoid.
    config_text = render_config(merged, header=_IMPORTED_CONFIG_HEADER)
    credentials_text = _render_accounts(accounts) if carry else None

    summary = {
        "ok": True, "schema_version": SCHEMA_VERSION, "command": "config import",
        "source": source,
        "bundle_schema": bundle.schema,
        "created_by": bundle.created_by,
        "created_at": bundle.created_at,
        "mode": "replace" if args.replace else "merge",
        "bindings": _diff(existing.bindings, incoming.bindings, merged.bindings),
        "defaults": dict(sorted(merged.defaults.items())),
        # Account names only. What the values are is the file's business, not the
        # result's — this object is printed, logged, and read by an agent.
        "credentials": _diff(before_accounts, incoming_accounts, accounts if carry else before_accounts),
        "skipped": {"bindings": dropped_bindings, "defaults": dropped_defaults},
        "wrote": [], "backed_up": [],
        "dry_run": bool(args.dry_run),
    }
    if not args.dry_run:
        _write(config_path(), config_text, write_public, summary)
        if credentials_text is not None:
            _write(stores.credentials_path(), credentials_text, write_private, summary)
    else:
        summary["wrote"] = [str(config_path())] + ([str(stores.credentials_path())] if carry else [])
    return summary


def _read(spec: str) -> tuple[str, str]:
    """The bundle's text and a name for it in errors. ``-`` reads stdin.

    stdin is supported because it is how a bundle reaches a box that has no business
    keeping one on disk::

        curl -fsSL https://internal/media-ai.bundle.toml | media-ai config import --input -

    stdout stays the one JSON object either way — the machine contract says nothing
    about stdin.
    """
    if spec == "-":
        return sys.stdin.read(), "<stdin>"
    path = Path(spec).expanduser()
    try:
        return path.read_text(encoding="utf-8"), str(path)
    except FileNotFoundError:
        raise MediaError(
            f"no bundle at {path}",
            category=ErrorCategory.NOT_FOUND, code="bundle_not_found",
            hint="media-ai config export --output <file>   # on the source machine",
        ) from None
    except OSError as exc:
        raise MediaError(f"could not read {path}: {exc}", category=ErrorCategory.IO) from exc


def _declared_only(incoming: Config, cat, *, skip_unknown: bool) -> tuple[Config, list[str], dict[str, str]]:
    """``incoming`` minus the bindings this build has no declaration for.

    An entry whose id nothing declares — and which does not ``extends`` something that
    is — poisons the whole config: ``available_bindings`` raises on it, so every command
    on the machine fails until it is edited out by hand. Refusing here keeps a bundle
    from a newer fleet member out of an older CLI's config file.

    Returns the scene defaults that went with the dropped bindings as well as the
    bindings themselves. A default is a decision the bundle carried; dropping it while
    reporting only the binding would leave the operator to work out for themselves that
    ``image.text_to_image`` no longer resolves.
    """
    unknown = sorted(bid for bid, ub in incoming.bindings.items() if cat.get(ub.extends or bid) is None)
    if not unknown:
        return incoming, [], {}
    if not skip_unknown:
        raise MediaError(
            f"this build does not declare {len(unknown)} binding(s) in the bundle: {', '.join(unknown)}",
            category=ErrorCategory.CLI, code="binding_undeclared",
            details={"bindings": unknown, "declared": cat.ids()},
            hint="upgrade media-ai, or re-run with --skip-unknown to drop them",
        )
    keep = {bid: ub for bid, ub in incoming.bindings.items() if bid not in unknown}
    defaults = {scene: bid for scene, bid in incoming.defaults.items() if bid not in unknown}
    dropped = {scene: bid for scene, bid in incoming.defaults.items() if bid in unknown}
    return replace(incoming, bindings=keep, defaults=defaults), unknown, dropped


def _reachable_defaults(merged: Config, cat, *, skip_unknown: bool) -> tuple[Config, dict[str, str]]:
    """``merged`` with every scene default naming a binding that is actually callable.

    Checked against ``available_bindings`` rather than ``[bindings]`` alone, because a
    credential-free binding (``local/ffmpeg``) is reachable without appearing there.
    """
    reachable = {rb.id for rb in available_bindings(cat, merged)}
    dropped = {scene: bid for scene, bid in merged.defaults.items() if bid not in reachable}
    if not dropped:
        return merged, {}
    if not skip_unknown:
        raise MediaError(
            f"the bundle defaults to binding(s) this machine cannot reach: {', '.join(sorted(set(dropped.values())))}",
            category=ErrorCategory.CLI, code="default_binding_missing",
            details={"defaults": dropped, "configured": sorted(reachable)},
            hint="re-run with --skip-unknown to drop them, or import the bundle that configures them",
        )
    kept = {scene: bid for scene, bid in merged.defaults.items() if scene not in dropped}
    return replace(merged, defaults=kept), dict(sorted(dropped.items()))


def _diff(before: dict, incoming: dict, final: dict) -> dict:
    """What the bundle did, in names — never values.

    ``added``/``updated``/``unchanged`` are about what the *bundle brought*, so a
    merge does not report every entry it left alone as "unchanged" by the import.
    ``removed`` is about the end state, and is empty unless ``--replace`` dropped
    something the bundle did not carry.
    """
    return {
        "added": sorted(k for k in incoming if k not in before),
        "updated": sorted(k for k in incoming if k in before and before[k] != incoming[k]),
        "unchanged": sorted(k for k in incoming if k in before and before[k] == incoming[k]),
        "removed": sorted(k for k in before if k not in final),
    }


def _render_accounts(accounts: dict[str, dict]) -> str:
    try:
        return dumps(accounts, header=_IMPORTED_CREDENTIALS_HEADER)
    except TomlWriteError as exc:
        raise MediaError(
            f"the credentials in this bundle hold a value the writer cannot round-trip ({exc}); "
            "nothing has been changed",
            category=ErrorCategory.AUTH, code="bundle_unwritable",
        ) from exc


def _write(path: Path, text: str, writer, summary: dict) -> None:
    saved = write_if_changed(path, text, writer)
    if saved:
        summary["backed_up"].append(str(saved))
    summary["wrote"].append(str(path))
