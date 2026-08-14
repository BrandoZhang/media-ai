"""``media-ai config show|set-default`` — the scene defaults, and what is in effect.

``set-default`` is what makes a bare ``media-ai video generate --prompt …`` work: it
names the binding a scene uses when the caller says nothing. That is the only choice
this CLI makes on anyone's behalf, and it is made here, once, in writing.
"""

from __future__ import annotations

import argparse

from ..brand import cli_name, cmd
from ..core.config import config_path, load_config, save_config
from ..core.errors import ErrorCategory, MediaError
from ..core.registry import catalog
from ..core.resolve import available_bindings
from ..core.result import SCHEMA_VERSION
from ..core.scene import Scene
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
    return ap


def _show(args) -> dict:
    config = load_config()
    out = {
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
    # Absent unless an organisation wrote part of this file, which is the only state
    # most installations are ever in. Reported because "why is this binding here — I
    # never added it?" is otherwise a question the file can answer and no command does.
    if config.managed is not None:
        out["managed"] = config.managed.to_dict()
    return out


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


def _do(args) -> dict:
    return {"show": _show, "set-default": _set_default}[args.op](args)


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
