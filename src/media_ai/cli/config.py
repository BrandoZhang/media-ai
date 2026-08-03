"""``media-ai config show|set-default|export|import`` — the configuration, as a whole.

``set-default`` is what makes a bare ``media-ai video generate --prompt …`` work: it
names the binding a scene uses when the caller says nothing. That is the only choice
this CLI makes on anyone's behalf, and it is made here, once, in writing.

``export``/``import`` move that decision — and every other one ``init`` asks about —
between machines as a file, so a production instance is provisioned rather than
interviewed. They live in this group because the thing they carry is the
configuration; the format and the rules are in :mod:`media_ai.core.bundle` and
:mod:`media_ai.cli._bundle`.
"""

from __future__ import annotations

import argparse

from ..core.config import config_path, load_config, save_config
from ..core.errors import ErrorCategory, MediaError
from ..core.registry import catalog
from ..core.resolve import available_bindings
from ..core.result import SCHEMA_VERSION
from ..core.scene import Scene
from . import common
from .bindings import CONFIG_HEADER


def _add_output_args(ap: argparse.ArgumentParser) -> None:
    """The output flags every command has, minus the addressing ones.

    ``export`` and ``import`` cannot take :func:`common.add_global_args`: it defines
    ``--binding`` as "which binding to call", and here that name means "which binding
    to export". Neither command resolves anything, so the addressing flags would be
    inert as well as ambiguous.
    """
    ap.add_argument("--pretty", action="store_true", help="pretty-print the JSON result")
    ap.add_argument("--log-level", default=None, help="stderr log level: debug, info, warning, or error")
    ap.add_argument("--metadata-out", default=None, help="also write the secret-free result JSON to this path")


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai config", description="Show, change, export or import configuration.")
    sub = ap.add_subparsers(dest="op", required=True)
    common.add_global_args(sub.add_parser("show", help="show configured bindings and scene defaults"))
    sd = sub.add_parser("set-default", help="which binding a scene uses when none is named")
    sd.add_argument("scene", help="e.g. video.text_to_video, or a group like 'video' for all its scenes")
    sd.add_argument("binding", help="the binding id to use")
    common.add_global_args(sd)

    ex = sub.add_parser("export", help="write bindings, defaults and (optionally) keys to one portable bundle")
    ex.add_argument("--output", required=True, help="write the bundle here (chmod 600 when it carries credentials)")
    ex.add_argument("--include-credentials", action="store_true",
                    help="also carry the accounts credentials.toml holds, for a target with no key material")
    ex.add_argument("--binding", action="append", default=None,
                    help="export only this binding (repeatable); default: everything configured")
    ex.add_argument("--force", action="store_true", help="overwrite the output file if it already exists")
    _add_output_args(ex)

    im = sub.add_parser("import", help="apply a bundle written by `config export`, instead of running the wizard")
    im.add_argument("--input", required=True, help="the bundle to read, or - to read it from stdin")
    im.add_argument("--replace", action="store_true",
                    help="replace the configured bindings and defaults instead of merging the bundle into them")
    im.add_argument("--skip-credentials", action="store_true",
                    help="import bindings and defaults only, leaving credentials.toml untouched")
    im.add_argument("--skip-unknown", action="store_true",
                    help="drop bindings and defaults this build does not declare, instead of refusing the bundle")
    im.add_argument("--dry-run", action="store_true", help="report what would change without writing anything")
    _add_output_args(im)
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
            hint=f"media-ai bindings add {args.binding} --credential env://…",
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
    updated = type(config)(bindings=dict(config.bindings), defaults=defaults, path=config.path, exists=True)
    saved = save_config(updated, header=CONFIG_HEADER)
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


def _do(args) -> dict:
    from ._bundle import export_bundle, import_bundle

    return {
        "show": _show,
        "set-default": _set_default,
        "export": export_bundle,
        "import": import_bundle,
    }[args.op](args)


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
