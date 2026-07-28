"""``media-ai concat`` — join per-shot clips into one film (local ffmpeg)."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..media import ffmpeg
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai concat", description="Concatenate clips into a final film.")
    ap.add_argument("--inputs", required=True, nargs="+", help="ordered clip paths or a JSON array")
    ap.add_argument("--output", required=True)
    ap.add_argument("--width", type=int, default=ffmpeg.DEFAULT_W)
    ap.add_argument("--height", type=int, default=ffmpeg.DEFAULT_H)
    common.add_global_args(ap)
    return ap


def _do(args) -> object:
    from ..core.config import load_config
    from ..core.registry import build_adapter, catalog
    from ..core.resolve import available_bindings, resolve
    from ..core.scene import Scene

    inputs = [Path(p) for p in common._listify(args.inputs)]
    cat, config = catalog(), load_config()
    rb = resolve(binding=args.binding, provider=args.provider, model=args.model,
                 scene=Scene.VIDEO_CONCAT, catalog=cat, config=config)
    rb.check_scene(Scene.VIDEO_CONCAT, available_bindings(cat, config))
    result = build_adapter(rb).concat(inputs, Path(args.output), width=args.width, height=args.height)
    return common.stamp(result, rb, Scene.VIDEO_CONCAT)


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
