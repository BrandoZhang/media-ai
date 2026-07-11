"""``media-ai video generate`` — provider-agnostic video generation.

Covers text→video, first/last-frame→video, and multimodal-reference→video via one
normalized request. Async providers submit a task; ``--wait true`` (default) polls
to completion, ``--wait false`` returns a JobHandle to poll with ``media-ai job``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..core import registry
from ..core.capabilities import validate_request
from ..core.logging import get_logger
from ..core.types import MediaRef, Modality, VideoRequest
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
    return ap


def _do(args) -> object:
    req = VideoRequest(
        prompt=args.prompt, output=Path(args.output),
        first_frame=MediaRef(args.first_frame, role="first_frame") if args.first_frame else None,
        last_frame=MediaRef(args.last_frame, role="last_frame") if args.last_frame else None,
        reference_images=common.parse_refs(args.reference_image, "reference_image"),
        reference_videos=common.parse_refs(args.reference_video, "reference_video"),
        reference_audios=common.parse_refs(args.reference_audio, "reference_audio"),
        geometry=common.parse_geometry(args), duration=args.duration, seed=args.seed,
        audio=args.audio, watermark=args.watermark, negative_prompt=args.negative_prompt,
        return_last_frame=args.return_last_frame, wait=args.wait, options=common.parse_options(args.option),
    )
    provider, model = registry.build(common.provider_name(args), args.model, Modality.VIDEO)
    req.model = model
    for w in validate_request(req, provider.capabilities(model, Modality.VIDEO), common.policy(args)):
        get_logger().warning("unsupported (proceeding): %s", w)
    return provider.generate_video(req)


def main() -> int:
    args = _build_parser().parse_args()
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
