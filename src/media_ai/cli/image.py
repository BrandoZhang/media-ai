"""``media-ai image generate|edit`` — provider-agnostic image generation/editing."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..core import registry
from ..core.capabilities import validate_request
from ..core.errors import ErrorCategory, MediaError
from ..core.logging import get_logger
from ..core.types import ImageRequest, MediaRef, Modality, Operation
from . import common


def _add_common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--reference", nargs="*", default=[], help="reference image path(s) or a JSON array")
    ap.add_argument("--count", type=int, default=1, help="request N images (a related group)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--negative-prompt", dest="negative_prompt", default=None)
    ap.add_argument("--background", choices=["transparent", "opaque", "auto"], default=None)
    ap.add_argument("--quality", choices=["low", "medium", "high", "auto"], default=None)
    ap.add_argument("--format", dest="format", default=None, help="png|jpeg|webp")
    ap.add_argument("--option", nargs="*", default=[], help="provider-specific key=value (capability-gated)")
    common.add_geometry_args(ap, resolution_help="named tier, e.g. 1K|2K|4K")
    common.add_global_args(ap)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai image", description="Generate or edit an image.")
    sub = ap.add_subparsers(dest="op", required=True)
    _add_common(sub.add_parser("generate", help="text (+optional references) -> image"))
    edit = sub.add_parser("edit", help="reference image(s) (+optional mask) -> image")
    edit.add_argument("--mask", default=None, help="PNG alpha mask (inpaint region)")
    _add_common(edit)
    return ap


def _do(args) -> object:
    op = Operation.IMAGE_EDIT if args.op == "edit" else Operation.IMAGE_GENERATE
    refs = common.parse_refs(args.reference, "reference_image")
    if op == Operation.IMAGE_EDIT and not refs:
        raise MediaError("image edit requires at least one --reference", category=ErrorCategory.CLI)
    req = ImageRequest(
        prompt=args.prompt, output=Path(args.output), operation=op, references=refs,
        mask=MediaRef(args.mask, role="mask") if getattr(args, "mask", None) else None,
        geometry=common.parse_geometry(args), count=args.count, seed=args.seed,
        negative_prompt=args.negative_prompt, background=args.background, quality=args.quality,
        output_format=args.format, options=common.parse_options(args.option),
    )
    provider, model = registry.build(common.provider_name(args), args.model, Modality.IMAGE,
                                     profile=args.provider_profile)
    req.model = model
    for w in validate_request(req, provider.capabilities(model, Modality.IMAGE), common.policy(args)):
        get_logger().warning("unsupported (proceeding): %s", w)
    return provider.generate_image(req)


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
