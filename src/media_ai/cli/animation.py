"""``media-ai animation export`` — a clip or a set of stills to an animated image.

The last mile of a render: a GIF, animated WebP or APNG that autoplays in a README, a
chat message or a docs page, where a ``<video>`` tag is unwelcome or silently blocked.
Local, offline, free.

It is its own group rather than a scene under ``video`` because the group carries the
**output** modality, and an animated image is served as an image — ``image/gif``, an
``<img>`` tag — whichever way it was made. Under ``video`` the result would have come
back as ``modality: video``, which is the field a consumer branches on.

Two scenes, split by input role: one source clip (``animation.from_video``) or a frame
sequence (``animation.from_frames``). Transparency is *not* a third — it takes exactly
the inputs the opaque case takes and changes only the output, which by the rule in
:mod:`media_ai.core.scene` makes it a request field. Keying is a chroma distance, so the
subject has to be on a flat colour; arbitrary footage is matted frame by frame elsewhere
and comes back in through ``--frames``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..brand import cli_name
from ..core.errors import ErrorCategory, MediaError
from ..core.logging import get_logger
from ..core.types import AnimationRequest, MediaRef
from ..core.validate import validate_request
from ..media.animation import CONTAINER_NAMES, KEY_MODES, SCALE_FILTERS
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{cli_name()} animation",
                                 description="Export an animated image (GIF, animated WebP, APNG).")
    sub = ap.add_subparsers(dest="op", required=True)
    ex = sub.add_parser("export", help="video or frame sequence -> animated image")

    ex.add_argument("--input", default=None, help="source video to animate")
    ex.add_argument("--frames", nargs="*", default=[],
                    help="frame sequence instead of --input: a directory, a glob, "
                         "ordered image paths, or one JSON array")
    ex.add_argument("--output", required=True, help="path for the animated image")
    # No argparse `choices`: the binding's manifest declares which formats it writes, and
    # letting argparse refuse first would answer with "invalid arguments" (exit 2) instead
    # of the machine-readable `request_not_supported` naming what this binding accepts —
    # the same reason `image generate --format` is ungated here.
    ex.add_argument("--format", dest="format", default=None,
                    help=f"output container ({', '.join(CONTAINER_NAMES)}); "
                         "inferred from --output's extension when omitted")

    # which part of the source
    ex.add_argument("--start", dest="start", type=float, default=None, help="skip to this offset, in seconds")
    ex.add_argument("--end", dest="end", type=float, default=None, help="stop at this offset, in seconds")
    ex.add_argument("--duration", dest="duration", type=float, default=None,
                    help="how many seconds to take from --start (alternative to --end)")

    # timing
    ex.add_argument("--fps", type=float, default=None,
                    help="frame rate of the animation; a lower rate is the biggest size saving")
    ex.add_argument("--speed", type=float, default=None, help="playback multiplier, e.g. 2 = twice as fast")
    common.add_toggle(ex, "--reverse", default=False, help="play backwards")
    common.add_toggle(ex, "--bounce", default=False,
                      help="play forwards then backwards, so a short clip loops without a jump")
    ex.add_argument("--loop", type=int, default=0, help="0 = forever (default), 1 = play once, N = N plays")

    # geometry. No --aspect-ratio or --resolution here: nothing is choosing a shape, the
    # source already has one — the only question is what to scale it to.
    ex.add_argument("--size", default=None, help="exact pixel size WIDTHxHEIGHT")
    ex.add_argument("--max-width", dest="max_width", type=int, default=None,
                    help="fit inside this width, keeping the aspect ratio; never enlarges")
    ex.add_argument("--max-height", dest="max_height", type=int, default=None,
                    help="fit inside this height, keeping the aspect ratio; never enlarges")
    ex.add_argument("--scale-filter", dest="scale_filter", default=None, choices=list(SCALE_FILTERS),
                    help="rescaling algorithm (default: lanczos)")

    # transparency
    common.add_toggle(ex, "--transparent", default=False,
                      help="key the background colour out to alpha (needs a flat-coloured background)")
    ex.add_argument("--key-color", dest="key_color", default=None,
                    help="the colour to key out, e.g. 0x00FF00 or green (default: green)")
    ex.add_argument("--key-mode", dest="key_mode", default=None, choices=list(KEY_MODES),
                    help="chromakey keys on chroma alone (tolerates lighting); colorkey on RGB")
    ex.add_argument("--similarity", type=float, default=None,
                    help="how far from --key-color still counts as background, 0-1 (default: 0.30)")
    ex.add_argument("--blend", type=float, default=None,
                    help="soften the alpha edge, 0-1; 0 is a hard cut (default: 0.05)")
    common.add_toggle(ex, "--despill", default=True,
                      help="remove the colour cast the key leaves on edges (default: true)")

    ex.add_argument("--option", nargs="*", default=[],
                    help="encoder key=value, e.g. dither=none quality=90 (capability-gated)")
    common.add_global_args(ex)
    return ap


def _do(args) -> object:
    frames = common.parse_refs(args.frames, "frame")
    if args.input and frames:
        raise MediaError(
            "give either --input or --frames, not both", category=ErrorCategory.CLI,
            code="animation_input_conflict",
            hint="one source clip is `--input clip.mp4`; a matted sequence is `--frames frames/`",
        )
    if not args.input and not frames:
        raise MediaError(
            "animation export needs a source", category=ErrorCategory.CLI, code="missing_input",
            hint="add --input clip.mp4, or --frames frames/ for a still sequence",
        )
    req = AnimationRequest(
        output=Path(args.output),
        source=MediaRef(args.input, role="source_video") if args.input else None,
        frames=frames,
        output_format=args.format,
        start_seconds=args.start, end_seconds=args.end, duration_seconds=args.duration,
        fps=args.fps, speed=args.speed, reverse=args.reverse, bounce=args.bounce, loop=args.loop,
        geometry=common.parse_geometry(args), max_width=args.max_width, max_height=args.max_height,
        scale_filter=args.scale_filter,
        transparent=args.transparent, key_color=args.key_color, key_mode=args.key_mode,
        similarity=args.similarity, blend=args.blend, despill=args.despill,
        options=common.parse_options(args.option),
    )
    adapter, rb, scene = common.bind(args, req)
    for w in validate_request(req, rb.spec.constraints, common.policy(args), binding=rb.id, scene=scene):
        get_logger().warning("unsupported (proceeding): %s", w)
    return common.stamp(adapter.generate_animation(req), rb, scene)


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
