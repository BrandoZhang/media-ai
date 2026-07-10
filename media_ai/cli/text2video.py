"""text2video: generate a short video clip from a text prompt.

Parameters:
  --prompt   (string, required): what happens in the shot.
  --output   (string, required): path to write the .mp4 (e.g. workspace/shot1.mp4).
  --seconds  (int, optional): clip length. Default 5.
  --resolution (string, optional): 480p|720p|1080p. Default 480p (cheapest).
  --ratio    (string, optional): 16:9|9:16|1:1|4:3|3:4|21:9. Default 16:9.
  --seed     (int, optional): seed for reproducibility.
  --camera_fixed (bool, optional): fix the camera. Default false.
  --watermark (bool, optional): add watermark. Default false.
  --generate_audio (bool, optional): synth audio (Seedance 2.0/1.5). Default unset.
  --wait     (bool, optional): wait for the video (default true). With the
      volc backend, --wait false submits the task and returns a task_id
      immediately (poll it with video_task) so the agent can yield instead
      of blocking for minutes. mock ignores it (always synchronous).
  --backend  (string, optional): mock (default) or volc (real API).

The real (volc) backend submits an async task; by default it blocks until
ready. Pass --wait false to submit-and-return.
"""

import argparse
import pathlib
import sys

from media_ai import mediakit


def _bool(s):
    return str(s).strip().lower() in ("1", "true", "yes", "y", "on")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a short video from a text prompt.")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--seconds", type=int, default=mediakit.DEFAULT_VIDEO_SECONDS)
    ap.add_argument("--resolution", default="480p")
    ap.add_argument("--ratio", default="16:9")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--camera_fixed", type=_bool, default=False)
    ap.add_argument("--watermark", type=_bool, default=False)
    ap.add_argument("--generate_audio", type=_bool, default=None)
    ap.add_argument("--wait", type=_bool, default=True)
    ap.add_argument("--model", default=None, help="Ark video Model ID; default from $ARK_VIDEO_MODEL or built-in.")
    ap.add_argument("--backend", default=None)
    args = ap.parse_args()
    try:
        res = mediakit.get_backend(args.backend).text2video(
            prompt=args.prompt,
            out=pathlib.Path(args.output),
            seconds=args.seconds,
            resolution=args.resolution,
            ratio=args.ratio,
            seed=args.seed,
            camera_fixed=args.camera_fixed,
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
