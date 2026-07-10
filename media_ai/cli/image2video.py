"""image2video: generate a clip from a first frame (+ optional last frame).

The key tool for cross-shot consistency: pass a reference image as the
first frame so the shot starts from a locked character/style. Provide
--last_frame too for first+last-frame interpolation.

Parameters:
  --first_frame (string, required): path to the first-frame image.
  --last_frame  (string, optional): path to the last-frame image (first+last).
  --prompt   (string, optional): the motion / action for the shot.
  --output   (string, required): path to write the .mp4.
  --seconds  (int, optional): clip length. Default 5.
  --resolution (string, optional): 480p|720p|1080p. Default 480p.
  --ratio    (string, optional): default adaptive (match the first frame).
  --seed     (int, optional): seed for reproducibility.
  --camera_fixed (bool, optional): default false.
  --watermark (bool, optional): default false.
  --generate_audio (bool, optional): default unset.
  --return_last_frame (bool, optional): also return the clip's last frame
      (useful to chain the next shot). Default false.
  --backend  (string, optional): mock (default) or volc (real API).
"""

import argparse
import pathlib
import sys

from media_ai import mediakit


def _bool(s):
    return str(s).strip().lower() in ("1", "true", "yes", "y", "on")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a clip from a first-frame (+ optional last-frame) image.")
    ap.add_argument("--first_frame", required=True)
    ap.add_argument("--last_frame", default=None)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--output", required=True)
    ap.add_argument("--seconds", type=int, default=mediakit.DEFAULT_VIDEO_SECONDS)
    ap.add_argument("--resolution", default="480p")
    ap.add_argument("--ratio", default="adaptive")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--camera_fixed", type=_bool, default=False)
    ap.add_argument("--watermark", type=_bool, default=False)
    ap.add_argument("--generate_audio", type=_bool, default=None)
    ap.add_argument("--return_last_frame", type=_bool, default=False)
    ap.add_argument("--wait", type=_bool, default=True)
    ap.add_argument("--model", default=None, help="Ark video Model ID; default from $ARK_VIDEO_MODEL or built-in.")
    ap.add_argument("--backend", default=None)
    args = ap.parse_args()
    try:
        res = mediakit.get_backend(args.backend).image2video(
            prompt=args.prompt,
            first_frame=pathlib.Path(args.first_frame),
            last_frame=(pathlib.Path(args.last_frame) if args.last_frame else None),
            out=pathlib.Path(args.output),
            seconds=args.seconds,
            resolution=args.resolution,
            ratio=args.ratio,
            seed=args.seed,
            camera_fixed=args.camera_fixed,
            watermark=args.watermark,
            generate_audio=args.generate_audio,
            return_last_frame=args.return_last_frame,
            wait=args.wait,
            model=args.model,
        )
    except mediakit.MediaError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(mediakit.dumps_result(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
