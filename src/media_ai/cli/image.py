"""``media-ai image generate|edit`` — provider-agnostic image generation/editing."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..brand import cli_name
from ..core.errors import ErrorCategory, MediaError
from ..core.types import ImageRequest
from . import common


def _add_common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--prompt", required=True, help="text instruction for the image to generate or edit")
    ap.add_argument("--output", required=True, help="path for the generated image file")
    ap.add_argument("--reference", nargs="*", default=[], help="reference image path(s) or a JSON array")
    ap.add_argument("--count", type=int, default=1, help="request N images (a related group)")
    ap.add_argument("--seed", type=int, default=None, help="deterministic generation seed, when the binding supports it")
    ap.add_argument("--negative-prompt", dest="negative_prompt", default=None,
                    help="content to avoid, when the binding supports it")
    ap.add_argument("--background", choices=["transparent", "opaque", "auto"], default=None,
                    help="requested background treatment")
    ap.add_argument("--quality", choices=["low", "medium", "high", "auto"], default=None,
                    help="requested quality tier, when the binding supports it")
    ap.add_argument("--format", dest="format", default=None, help="output image format: png, jpeg, or webp")
    ap.add_argument("--option", nargs="*", default=[], help="provider-specific key=value (capability-gated)")
    common.add_geometry_args(ap, resolution_help="named tier, e.g. 1K|2K|4K")
    common.add_global_args(ap)
    common.add_call_headers(ap)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{cli_name()} image", description="Generate or edit an image.")
    sub = ap.add_subparsers(dest="op", required=True)
    _add_common(sub.add_parser("generate", help="text (+optional references) -> image"))
    edit = sub.add_parser("edit", help="reference image(s) -> image; --reference is required")
    _add_common(edit)
    return ap


def _do(args) -> object:
    refs = common.parse_refs(args.reference, "reference_image")
    # `edit` is `generate` with the reference made mandatory. It runs the same scene
    # (`image.image_to_image`, derived from the references like every other scene) and
    # exists only to catch the intent it declares: a caller that meant to transform an
    # image and forgot the input gets an error instead of a brand-new one from text.
    if args.op == "edit" and not refs:
        raise MediaError("image edit requires at least one --reference", category=ErrorCategory.CLI,
                         code="missing_reference", hint="add --reference <path>, or use `image generate` to make a new image")
    req = ImageRequest(
        prompt=args.prompt, output=Path(args.output), references=refs,
        geometry=common.parse_geometry(args), count=args.count, seed=args.seed,
        negative_prompt=args.negative_prompt, background=args.background, quality=args.quality,
        output_format=args.format, options=common.parse_options(args.option),
    )
    adapter, rb, scene = common.bind(args, req)
    common.check(req, args, rb, scene)
    return common.produce(adapter.generate_image, req, rb, scene)


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
