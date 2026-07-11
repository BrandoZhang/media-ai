"""Backwards-compatible shims for the original eight console-scripts.

Existing Agent Skills invoke ``text2image``/``image2image``/``text2video``/
``image2video``/``ref2video``/``concat_video``/``video_task``/``media_usage``.
Each shim parses the original flags, builds a normalized request, and dispatches
through the same provider-agnostic core as the canonical ``media-ai`` surface.
Provider-specific legacy flags (``--camera_fixed`` …) map to ``options``;
``--resolution``/``--ratio`` map to the normalized geometry.
"""

from __future__ import annotations

import argparse
from argparse import Namespace
from pathlib import Path

from ..core import registry
from ..core.capabilities import validate_request
from ..core.logging import get_logger
from ..core.types import GeometrySpec, ImageRequest, MediaRef, Modality, Operation, VideoRequest
from . import common
from .common import bool_arg


def _out_args(a: argparse.Namespace) -> Namespace:
    """Namespace carrying the output-contract flags common.run expects."""
    return Namespace(pretty=False, metadata_out=None, log_level=None, on_unsupported="error",
                     provider=None, model=getattr(a, "model", None), backend=getattr(a, "backend", None))


def _dispatch_image(req: ImageRequest, a) -> ImageRequest:
    provider, model = registry.build(a.backend, a.model, Modality.IMAGE)
    req.model = model
    for w in validate_request(req, provider.capabilities(model, Modality.IMAGE), common.policy(_out_args(a))):
        get_logger().warning("unsupported (proceeding): %s", w)
    return provider.generate_image(req)


def _dispatch_video(req: VideoRequest, a):
    provider, model = registry.build(a.backend, a.model, Modality.VIDEO)
    req.model = model
    for w in validate_request(req, provider.capabilities(model, Modality.VIDEO), common.policy(_out_args(a))):
        get_logger().warning("unsupported (proceeding): %s", w)
    return provider.generate_video(req)


def text2image() -> int:
    ap = argparse.ArgumentParser(prog="text2image")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=432)
    ap.add_argument("--max_images", type=int, default=1)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--backend", default=None)
    a = ap.parse_args()
    req = ImageRequest(prompt=a.prompt, output=Path(a.output), operation=Operation.IMAGE_GENERATE,
                       geometry=GeometrySpec(width=a.width, height=a.height), count=a.max_images, seed=a.seed)
    return common.run(lambda _a: _dispatch_image(req, a), _out_args(a))


def image2image() -> int:
    ap = argparse.ArgumentParser(prog="image2image")
    ap.add_argument("--images", required=True, nargs="+")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--strength", type=float, default=0.6)  # accepted for compat; not forwarded
    ap.add_argument("--max_images", type=int, default=1)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--backend", default=None)
    a = ap.parse_args()
    req = ImageRequest(prompt=a.prompt, output=Path(a.output), operation=Operation.IMAGE_EDIT,
                       references=common.parse_refs(a.images, "reference_image"), count=a.max_images, seed=a.seed)
    return common.run(lambda _a: _dispatch_image(req, a), _out_args(a))


def _video_geo(resolution: str | None, ratio: str | None) -> GeometrySpec:
    return GeometrySpec(aspect_ratio=ratio, resolution=resolution)


def text2video() -> int:
    ap = argparse.ArgumentParser(prog="text2video")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--seconds", type=int, default=5)
    ap.add_argument("--resolution", default="480p")
    ap.add_argument("--ratio", default="16:9")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--camera_fixed", type=bool_arg, default=False)
    ap.add_argument("--watermark", type=bool_arg, default=False)
    ap.add_argument("--generate_audio", type=bool_arg, default=None)
    ap.add_argument("--wait", type=bool_arg, default=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--backend", default=None)
    a = ap.parse_args()
    req = VideoRequest(prompt=a.prompt, output=Path(a.output), geometry=_video_geo(a.resolution, a.ratio),
                       duration=a.seconds, seed=a.seed, audio=a.generate_audio, watermark=a.watermark,
                       wait=a.wait, options={"camera_fixed": a.camera_fixed} if a.camera_fixed else {})
    return common.run(lambda _a: _dispatch_video(req, a), _out_args(a))


def image2video() -> int:
    ap = argparse.ArgumentParser(prog="image2video")
    ap.add_argument("--first_frame", required=True)
    ap.add_argument("--last_frame", default=None)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--output", required=True)
    ap.add_argument("--seconds", type=int, default=5)
    ap.add_argument("--resolution", default="480p")
    ap.add_argument("--ratio", default="adaptive")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--camera_fixed", type=bool_arg, default=False)
    ap.add_argument("--watermark", type=bool_arg, default=False)
    ap.add_argument("--generate_audio", type=bool_arg, default=None)
    ap.add_argument("--return_last_frame", type=bool_arg, default=False)
    ap.add_argument("--wait", type=bool_arg, default=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--backend", default=None)
    a = ap.parse_args()
    req = VideoRequest(prompt=a.prompt, output=Path(a.output),
                       first_frame=MediaRef(a.first_frame, role="first_frame"),
                       last_frame=MediaRef(a.last_frame, role="last_frame") if a.last_frame else None,
                       geometry=_video_geo(a.resolution, a.ratio), duration=a.seconds, seed=a.seed,
                       audio=a.generate_audio, watermark=a.watermark, return_last_frame=a.return_last_frame,
                       wait=a.wait, options={"camera_fixed": a.camera_fixed} if a.camera_fixed else {})
    return common.run(lambda _a: _dispatch_video(req, a), _out_args(a))


def ref2video() -> int:
    ap = argparse.ArgumentParser(prog="ref2video")
    ap.add_argument("--images", nargs="*", default=[])
    ap.add_argument("--videos", nargs="*", default=[])
    ap.add_argument("--audios", nargs="*", default=[])
    ap.add_argument("--prompt", default="")
    ap.add_argument("--output", required=True)
    ap.add_argument("--seconds", type=int, default=5)
    ap.add_argument("--resolution", default="480p")
    ap.add_argument("--ratio", default="adaptive")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--watermark", type=bool_arg, default=False)
    ap.add_argument("--generate_audio", type=bool_arg, default=None)
    ap.add_argument("--wait", type=bool_arg, default=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--backend", default=None)
    a = ap.parse_args()
    req = VideoRequest(prompt=a.prompt, output=Path(a.output),
                       reference_images=common.parse_refs(a.images, "reference_image"),
                       reference_videos=common.parse_refs(a.videos, "reference_video"),
                       reference_audios=common.parse_refs(a.audios, "reference_audio"),
                       geometry=_video_geo(a.resolution, a.ratio), duration=a.seconds, seed=a.seed,
                       audio=a.generate_audio, watermark=a.watermark, wait=a.wait)
    return common.run(lambda _a: _dispatch_video(req, a), _out_args(a))


def concat_video() -> int:
    from .concat import _do

    ap = argparse.ArgumentParser(prog="concat_video")
    ap.add_argument("--inputs", required=True, nargs="+")
    ap.add_argument("--output", required=True)
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=432)
    ap.add_argument("--backend", default=None)
    a = ap.parse_args()
    a.provider = None
    a.pretty = a.log_level = a.metadata_out = None
    a.pretty = False
    return common.run(_do, a)


def video_task() -> int:
    from .job import _do

    ap = argparse.ArgumentParser(prog="video_task")
    ap.add_argument("--op", required=True, choices=["query", "cancel"])
    ap.add_argument("--id", required=True)
    ap.add_argument("--output", default=None)
    ap.add_argument("--backend", default=None)
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    a.provider = None
    a.pretty = False
    a.log_level = a.metadata_out = None
    return common.run(_do, a)


def media_usage() -> int:
    from .usage import _do

    ap = argparse.ArgumentParser(prog="media_usage")
    ap.add_argument("--log", default=None)
    ap.add_argument("--backend", default=None)  # accepted + ignored
    a = ap.parse_args()
    a.pretty = False
    a.log_level = a.metadata_out = None
    return common.run(_do, a)
