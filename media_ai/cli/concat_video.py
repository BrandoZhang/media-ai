"""concat_video: join per-shot clips into one final film.

Clips are normalised (size/fps) and re-encoded so differing inputs join
cleanly.

Parameters:
  --inputs (string, required): the ordered clip paths. Accepts a JSON array
      (e.g. '["workspace/shot1.mp4","workspace/shot2.mp4"]') -- this is how
      the agent tool layer passes list arguments -- or one or more plain
      paths.
  --output (string, required): path to write the final .mp4.
  --width  (int, optional): output width. Default 768.
  --height (int, optional): output height. Default 432.
"""

import argparse
import json
import pathlib
import sys

from media_ai import mediakit


def _parse_inputs(raw: list[str]) -> list[str]:
    """Accept either a single JSON-array string or several plain paths."""
    if len(raw) == 1 and raw[0].lstrip().startswith("["):
        try:
            parsed = json.loads(raw[0])
            if isinstance(parsed, list):
                return [str(p) for p in parsed]
        except json.JSONDecodeError:
            pass
    return raw


def main() -> int:
    ap = argparse.ArgumentParser(description="Concatenate shot clips into a final film.")
    ap.add_argument("--inputs", required=True, nargs="+")
    ap.add_argument("--output", required=True)
    ap.add_argument("--width", type=int, default=mediakit.DEFAULT_W)
    ap.add_argument("--height", type=int, default=mediakit.DEFAULT_H)
    ap.add_argument("--backend", default=None)  # accepted + ignored for a uniform tool surface
    args = ap.parse_args()
    inputs = [pathlib.Path(p) for p in _parse_inputs(args.inputs)]
    try:
        out = mediakit.concat_clips(inputs, pathlib.Path(args.output), w=args.width, h=args.height)
    except mediakit.MediaError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    size = out.stat().st_size if out.is_file() else 0
    print(
        json.dumps(
            {"ok": True, "kind": "video", "path": str(out), "clips": len(inputs), "bytes": size}, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
