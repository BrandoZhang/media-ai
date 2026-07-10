"""ref2video: multimodal-reference video generation (Seedance 2.0).

Generate one video from any mix of reference images (0-9), reference
videos (0-3), reference audio (0-3), and an optional text prompt. At least
one reference image or video is required. Use it for character/scene
consistency driven by real assets, video editing/extension, or audio-synced
generation.

Parameters:
  --images (string, optional): reference image path(s) (role reference_image).
  --videos (string, optional): reference video path(s) (role reference_video).
  --audios (string, optional): reference audio path(s) (role reference_audio).
      Each accepts a JSON array or one/more plain paths/URLs.
  --prompt (string, optional): text guidance.
  --output (string, required): path to write the .mp4.
  --seconds (int, optional): clip length. Default 5.
  --resolution (string, optional): 480p|720p|1080p. Default 480p.
  --ratio  (string, optional): default adaptive.
  --seed   (int, optional): seed.
  --watermark (bool, optional): default false.
  --generate_audio (bool, optional): default unset.
  --backend (string, optional): mock (default) or volc (real API).

Note: reference videos/audio should generally be public URLs or asset://
IDs; large local files are inlined as base64 which the API may reject.
"""

import argparse
import json
import pathlib
import sys

from media_ai import mediakit


def _bool(s):
    return str(s).strip().lower() in ("1", "true", "yes", "y", "on")


def _listify(raw):
    if not raw:
        return []
    if len(raw) == 1 and raw[0].lstrip().startswith("["):
        try:
            v = json.loads(raw[0])
            if isinstance(v, list):
                return [str(x) for x in v]
        except json.JSONDecodeError:
            pass
    return list(raw)


def main() -> int:
    ap = argparse.ArgumentParser(description="Multimodal-reference video generation.")
    ap.add_argument("--images", nargs="*", default=[])
    ap.add_argument("--videos", nargs="*", default=[])
    ap.add_argument("--audios", nargs="*", default=[])
    ap.add_argument("--prompt", default="")
    ap.add_argument("--output", required=True)
    ap.add_argument("--seconds", type=int, default=mediakit.DEFAULT_VIDEO_SECONDS)
    ap.add_argument("--resolution", default="480p")
    ap.add_argument("--ratio", default="adaptive")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--watermark", type=_bool, default=False)
    ap.add_argument("--generate_audio", type=_bool, default=None)
    ap.add_argument("--wait", type=_bool, default=True)
    ap.add_argument("--model", default=None, help="Ark video Model ID; default from $ARK_VIDEO_MODEL or built-in.")
    ap.add_argument("--backend", default=None)
    args = ap.parse_args()
    try:
        res = mediakit.get_backend(args.backend).ref2video(
            prompt=args.prompt,
            images=_listify(args.images),
            videos=_listify(args.videos),
            audios=_listify(args.audios),
            out=pathlib.Path(args.output),
            seconds=args.seconds,
            resolution=args.resolution,
            ratio=args.ratio,
            seed=args.seed,
            watermark=args.watermark,
            generate_audio=args.generate_audio,
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
