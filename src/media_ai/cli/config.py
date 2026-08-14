"""``media-ai config show|set-default|migrate`` — the scene defaults, and what is in effect.

``set-default`` is what makes a bare ``media-ai video generate --prompt …`` work: it
names the binding a scene uses when the caller says nothing. That is the only choice
this CLI makes on anyone's behalf, and it is made here, once, in writing.

``migrate`` is the other half of the schema door: an ordinary read converts what it can
without touching the file, and anything it cannot is sent here, where the rewrite has a
name and a ``--dry-run``. It covers ``credentials.toml`` as well — "my configuration"
is both files from outside, and the secret one deliberately has no command group of its
own to put the other half in.
"""

from __future__ import annotations

import argparse

from ..brand import cli_name, cmd
from ..core.config import SCHEMA, config_path, load_config, migrate_file, save_config
from ..core.errors import ErrorCategory, MediaError
from ..core.registry import catalog
from ..core.resolve import available_bindings
from ..core.result import SCHEMA_VERSION
from ..core.scene import Scene
from ..credentials.stores import SCHEMA as CREDENTIALS_SCHEMA
from . import common
from .bindings import config_header


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{cli_name()} config", description="Show or change configuration.")
    sub = ap.add_subparsers(dest="op", required=True)
    common.add_global_args(sub.add_parser("show", help="show configured bindings and scene defaults"))
    sd = sub.add_parser("set-default", help="which binding a scene uses when none is named")
    sd.add_argument("scene", help="e.g. video.text_to_video, or a group like 'video' for all its scenes")
    sd.add_argument("binding", help="the binding id to use")
    common.add_global_args(sd)
    mig = sub.add_parser(
        "migrate",
        help=f"bring config.toml (schema {SCHEMA}) and credentials.toml "
             f"(schema {CREDENTIALS_SCHEMA}) up to the shapes this build reads",
    )
    mig.add_argument("--dry-run", action="store_true", help="report what would change, and write nothing")
    common.add_global_args(mig)
    return ap


def _show(args) -> dict:
    config = load_config()
    return {
        "ok": True, "schema_version": SCHEMA_VERSION,
        "config": str(config_path()),
        "exists": config.exists,
        "bindings": {
            bid: {k: v for k, v in (("extends", b.extends), ("model_id", b.model_id),
                                    ("endpoint_id", b.endpoint_id),
                                    ("base_url", b.base_url), ("credential", b.credential)) if v}
            for bid, b in sorted(config.bindings.items())
        },
        "defaults": dict(sorted(config.defaults.items())),
    }


def _scenes_named(raw: str) -> list[Scene]:
    """A scene, or every scene in a group.

    A group is accepted because "images go to this binding" is the decision people
    actually make; expanding it to each scene keeps the stored form precise, so
    refining one scene later needs no schema change.
    """
    try:
        return [Scene(raw)]
    except ValueError:
        pass
    scenes = [s for s in Scene if s.group == raw]
    if scenes:
        return scenes
    raise MediaError(
        f"unknown scene or group {raw!r}",
        category=ErrorCategory.CLI, code="unknown_scene",
        details={"scenes": [s.value for s in Scene], "groups": sorted({s.group for s in Scene})},
    )


def _set_default(args) -> dict:
    cat, config = catalog(), load_config()
    scenes = _scenes_named(args.scene)
    available = {b.id: b for b in available_bindings(cat, config)}
    rb = available.get(args.binding)
    if rb is None:
        raise MediaError(
            f"binding {args.binding!r} is not configured, so it cannot be a default",
            category=ErrorCategory.CLI, code="binding_not_configured",
            details={"configured": sorted(available)},
            hint=f"{cmd('bindings', 'add', args.binding)} --credential env://…",
        )

    unsupported = [s.value for s in scenes if s not in rb.spec.scenes]
    scenes = [s for s in scenes if s in rb.spec.scenes]
    if not scenes:
        raise MediaError(
            f"binding {args.binding!r} serves none of {args.scene!r}",
            category=ErrorCategory.UNSUPPORTED, code="scene_not_supported",
            details={"supported_scenes": sorted(s.value for s in rb.spec.scenes)},
        )

    defaults = dict(config.defaults)
    for scene in scenes:
        defaults[scene.value] = args.binding
    updated = config.merged_with(defaults=defaults, exists=True)
    saved = save_config(updated, header=config_header())
    return {
        "ok": True, "schema_version": SCHEMA_VERSION,
        "binding": args.binding,
        "scenes": [s.value for s in scenes],
        # Naming a group whose binding covers only part of it is worth saying out
        # loud: the rest keeps whatever default it had, which is not what "set the
        # default for video" sounds like it did.
        "skipped": unsupported,
        "config": str(config_path()),
        "backup": str(saved) if saved else None,
    }


def _migrate(args) -> dict:
    """Convert both files this tool owns, or say there is nothing to convert.

    Both, because "my configuration" is `config.toml` *and* `credentials.toml` from
    outside, and two commands for two files is an implementation detail leaking into
    the surface. There is deliberately no `credentials` command group to put the other
    half in — the secret file is kept off the CLI on purpose, and migration is the only
    thing it needs from out here.

    Nothing is written unless *both* can be, which is the rule `init` follows for the
    same two files: a dry pass first, so a document that cannot be converted fails with
    neither file touched rather than with one of them half-way.

    Exits 0 when they are already current. "Nothing to do" is the success case of a
    command whose whole job is to make a thing true, and a script that runs this before
    every start would otherwise have to special-case the ordinary answer.
    """
    from ..credentials import stores

    runs = ((migrate_file, "config.toml"), (stores.migrate_file, "credentials.toml"))
    planned = [run(dry_run=True) for run, _ in runs]
    if not any(report.present for report in planned):
        raise MediaError(
            f"neither {config_path()} nor {stores.credentials_path()} exists; nothing to migrate",
            category=ErrorCategory.CLI, code="config_absent",
        )
    reports = planned if args.dry_run else [run(dry_run=False) for run, _ in runs]
    return {
        "ok": True, "schema_version": SCHEMA_VERSION,
        "migrated": any(report.applied for report in reports),
        "files": [
            {
                "document": name,
                "path": str(report.path),
                "present": report.present,
                "from_schema": report.frm,
                "to_schema": report.to,
                "migrated": report.applied,
                "steps": list(report.steps),
                "backup": str(report.backup) if report.backup else None,
            }
            for (_, name), report in zip(runs, reports, strict=True)
        ],
    }


def _do(args) -> dict:
    return {"show": _show, "set-default": _set_default, "migrate": _migrate}[args.op](args)


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
