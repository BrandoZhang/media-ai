"""text2image: generate an image (or a group of images) from a text prompt.

Parameters:
  --prompt (string, required): what to draw.
  --output (string, required): path to write the image (e.g. workspace/ref.png).
  --width  (int, optional): image width. Default 768.
  --height (int, optional): image height. Default 432.
  --max_images (int, optional): >1 requests a related *group* of images
      (saved as <output>, <output>_2, ...). Default 1.
  --seed   (int, optional): seed for reproducibility.
  --backend (string, optional): mock (default, offline) or volc (real API).
"""

import argparse
import pathlib
import sys

from media_ai import mediakit


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate an image from a text prompt.")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--width", type=int, default=mediakit.DEFAULT_W)
    ap.add_argument("--height", type=int, default=mediakit.DEFAULT_H)
    ap.add_argument("--max_images", type=int, default=1)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model", default=None, help="Ark image Model ID; default from $ARK_IMAGE_MODEL or built-in.")
    ap.add_argument("--backend", default=None)
    args = ap.parse_args()
    try:
        res = mediakit.get_backend(args.backend).text2image(
            prompt=args.prompt,
            out=pathlib.Path(args.output),
            width=args.width,
            height=args.height,
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
