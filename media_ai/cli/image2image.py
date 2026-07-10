"""image2image: generate image(s) from one or more reference images + a prompt.

Covers single-image and multi-image reference generation (and group output).
Use it to keep a character/style locked, or fuse several references.

Parameters:
  --images   (string, required): reference image path(s). Accepts a JSON
      array ('["a.png","b.png"]') or one/more plain paths.
  --prompt   (string, required): how to transform / what to render.
  --output   (string, required): path to write the new image.
  --strength (float, optional): follow prompt vs reference (0-1). Default 0.6.
  --max_images (int, optional): >1 requests a related group. Default 1.
  --seed     (int, optional): seed for reproducibility.
  --backend  (string, optional): mock (default) or volc (real API).
"""

import argparse
import json
import pathlib
import sys

from media_ai import mediakit


def _listify(raw: list[str]) -> list[str]:
    if len(raw) == 1 and raw[0].lstrip().startswith("["):
        try:
            v = json.loads(raw[0])
            if isinstance(v, list):
                return [str(x) for x in v]
        except json.JSONDecodeError:
            pass
    return raw


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate an image from reference image(s) + prompt.")
    ap.add_argument("--images", required=True, nargs="+")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--strength", type=float, default=0.6)
    ap.add_argument("--max_images", type=int, default=1)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model", default=None, help="Ark image Model ID; default from $ARK_IMAGE_MODEL or built-in.")
    ap.add_argument("--backend", default=None)
    args = ap.parse_args()
    try:
        res = mediakit.get_backend(args.backend).image2image(
            prompt=args.prompt,
            images=_listify(args.images),
            out=pathlib.Path(args.output),
            strength=args.strength,
            seed=args.seed,
            max_images=args.max_images,
            model=args.model,
        )
    except mediakit.MediaError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(res.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
