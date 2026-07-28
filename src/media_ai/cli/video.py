"""``media-ai video generate|concat`` — the video command group.

``generate`` covers text→video, first/last-frame→video, multimodal-reference→video
and extension via one normalized request. Async backends submit a task; ``--wait
true`` (default) polls to completion, ``--wait false`` returns a JobHandle to poll
with ``media-ai job``.

``concat`` joins finished clips into one film. It sits here, under ``video``, rather
than beside it as a top-level command: it serves ``video.concat``, one of the scenes
the video group is defined by, and the binding that runs it (``local/ffmpeg``) is
resolved and stamped exactly like any other. A top-level ``media-ai concat`` said the
opposite — that joining clips is a different kind of thing from making them — which
is also how it ended up with its own skill to keep in step.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..core.validate import validate_request
from ..core.logging import get_logger
from ..core.scene import Scene
from ..core.types import MediaRef, VideoRequest
from ..media import ffmpeg
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai video", description="Generate a video.")
    sub = ap.add_subparsers(dest="op", required=True)
    gen = sub.add_parser("generate", help="text / frames / references -> video")
    gen.add_argument("--prompt", default="")
    gen.add_argument("--output", required=True)
    gen.add_argument("--first-frame", dest="first_frame", default=None)
    gen.add_argument("--last-frame", dest="last_frame", default=None)
    gen.add_argument("--reference-image", dest="reference_image", nargs="*", default=[])
    gen.add_argument("--reference-video", dest="reference_video", nargs="*", default=[])
    gen.add_argument("--reference-audio", dest="reference_audio", nargs="*", default=[])
    gen.add_argument("--continue-from", dest="continue_from", default=None,
                     help="continue from the end of this clip (video.extend); some backends require a URI")
    gen.add_argument("--duration", "--seconds", dest="duration", type=int, default=None)
    gen.add_argument("--seed", type=int, default=None)
    gen.add_argument("--audio", type=common.bool_arg, default=None)
    gen.add_argument("--watermark", type=common.bool_arg, default=None)
    gen.add_argument("--negative-prompt", dest="negative_prompt", default=None)
    gen.add_argument("--return-last-frame", dest="return_last_frame", type=common.bool_arg, default=False)
    gen.add_argument("--wait", type=common.bool_arg, default=True)
    gen.add_argument("--option", nargs="*", default=[])
    common.add_geometry_args(gen, resolution_help="480p|720p|1080p (provider-dependent)")
    common.add_global_args(gen)

    cat = sub.add_parser("concat", help="join finished clips into one film (local ffmpeg)")
    cat.add_argument("--inputs", required=True, nargs="+", help="ordered clip paths or a JSON array")
    cat.add_argument("--output", required=True)
    cat.add_argument("--width", type=int, default=ffmpeg.DEFAULT_W)
    cat.add_argument("--height", type=int, default=ffmpeg.DEFAULT_H)
    common.add_global_args(cat)
    return ap


def _do(args) -> object:
    return _concat(args) if args.op == "concat" else _generate(args)


def _concat(args) -> object:
    from ..core.config import load_config
    from ..core.registry import build_adapter, catalog
    from ..core.resolve import available_bindings, resolve

    inputs = [Path(p) for p in common._listify(args.inputs)]
    cat, config = catalog(), load_config()
    rb = resolve(binding=args.binding, provider=args.provider, model=args.model,
                 scene=Scene.VIDEO_CONCAT, catalog=cat, config=config)
    rb.check_scene(Scene.VIDEO_CONCAT, available_bindings(cat, config))
    result = build_adapter(rb).concat(inputs, Path(args.output), width=args.width, height=args.height)
    return common.stamp(result, rb, Scene.VIDEO_CONCAT)


def _generate(args) -> object:
    req = VideoRequest(
        prompt=args.prompt, output=Path(args.output),
        first_frame=MediaRef(args.first_frame, role="first_frame") if args.first_frame else None,
        last_frame=MediaRef(args.last_frame, role="last_frame") if args.last_frame else None,
        reference_images=common.parse_refs(args.reference_image, "reference_image"),
        reference_videos=common.parse_refs(args.reference_video, "reference_video"),
        reference_audios=common.parse_refs(args.reference_audio, "reference_audio"),
        continue_from=MediaRef(args.continue_from, role="continue_from") if args.continue_from else None,
        geometry=common.parse_geometry(args), duration=args.duration, seed=args.seed,
        audio=args.audio, watermark=args.watermark, negative_prompt=args.negative_prompt,
        return_last_frame=args.return_last_frame, wait=args.wait, options=common.parse_options(args.option),
    )
    adapter, rb, scene = common.bind(args, req)
    for w in validate_request(req, rb.spec.constraints, common.policy(args), binding=rb.id, scene=scene):
        get_logger().warning("unsupported (proceeding): %s", w)
    return common.stamp(adapter.generate_video(req), rb, scene)


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
