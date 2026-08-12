"""``media-ai capabilities`` — what the bindings on this machine can do.

Prints the binding manifests directly. Discovery and pre-flight validation read the
same declaration, so "what does this support?" cannot drift from what actually gets
enforced — which is the whole reason the declaration is data.

Each entry also says whether it is **usable right now**: a binding needing a
credential appears as available only once one is configured. An agent choosing where
to send work needs both halves of that — what exists, and what it can reach.
"""

from __future__ import annotations

import argparse

from ..brand import cli_name
from ..core.config import load_config
from ..core.errors import ErrorCategory, MediaError
from ..core.registry import catalog
from ..core.resolve import available_bindings
from ..core.result import SCHEMA_VERSION
from ..core.scene import Scene
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{cli_name()} capabilities", description="Describe the available bindings.")
    ap.add_argument("--scene", default=None, help="only bindings serving this scene, e.g. video.image_to_video")
    ap.add_argument("--configured", action="store_true", help="only bindings usable right now")
    common.add_global_args(ap)
    return ap


def _parse_scene(raw: str | None) -> Scene | None:
    if not raw:
        return None
    try:
        return Scene(raw)
    except ValueError:
        raise MediaError(
            f"unknown scene {raw!r}",
            category=ErrorCategory.CLI, code="unknown_scene",
            details={"scenes": [s.value for s in Scene]},
        ) from None


def _matches(spec, args, scene: Scene | None, binding_id: str | None = None) -> bool:
    if args.binding and (binding_id or spec.id) != args.binding:
        return False
    if args.provider and spec.provider != args.provider:
        return False
    if args.model and spec.model != args.model:
        return False
    return scene is None or scene in spec.scenes


def _do(args) -> dict:
    scene = _parse_scene(args.scene)
    cat, config = catalog(), load_config()
    reachable = {b.id: b for b in available_bindings(cat, config)}

    entries: list[dict] = []
    for spec in cat.all():
        if not _matches(spec, args, scene):
            continue
        provider = cat.providers[spec.provider]
        rb = reachable.get(spec.id)
        entry = spec.to_dict()
        entry.update({
            "transport": provider.transport.value,
            "needs_credential": provider.auth.needs_credential,
            "available": rb is not None,
            "configured": bool(rb and rb.configured),
        })
        if rb is None and provider.setup_hint:
            entry["setup_hint"] = provider.setup_hint
        entries.append(entry)

    # Bindings that exist only in the config — a second account, a deployment id — are
    # real and callable, so omitting them would describe the package rather than this
    # machine.
    known = {e["binding"] for e in entries}
    for bid, rb in sorted(reachable.items()):
        if bid in known or not _matches(rb.spec, args, scene, binding_id=bid):
            continue
        entry = rb.spec.to_dict()
        entry.update({
            "binding": bid, "model_id": rb.model_id, "extends": rb.spec.id,
            "transport": rb.provider.transport.value,
            "needs_credential": rb.provider.auth.needs_credential,
            "available": True, "configured": rb.configured,
        })
        entries.append(entry)

    if args.configured:
        entries = [e for e in entries if e["available"]]

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "bindings": sorted(entries, key=lambda e: e["binding"]),
        "defaults": dict(sorted(config.defaults.items())),
    }


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
