"""``media-ai concat`` — join per-shot clips into one film (local ffmpeg)."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..core.result import SCHEMA_VERSION
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


def _do(args) -> dict:
    inputs = [Path(p) for p in common._listify(args.inputs)]
    out = ffmpeg.concat_clips(inputs, Path(args.output), w=args.width, h=args.height)
    size = out.stat().st_size if out.is_file() else 0
    return {
        "ok": True, "schema_version": SCHEMA_VERSION, "modality": "video", "operation": "video.concat",
        "provider": "local", "model": None, "kind": "video", "path": str(out),
        "bytes": size, "clips": len(inputs),
        "artifacts": [{"path": str(out), "kind": "video", "mime": "video/mp4", "bytes": size, "role": None}],
        "usage": {}, "meta": {"clips": len(inputs)},
    }


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
