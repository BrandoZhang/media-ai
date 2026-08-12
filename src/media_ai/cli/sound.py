"""``media-ai sound`` — text-to-sound-effects (ElevenLabs).

``sound generate`` turns a text description into a sound effect. ``--duration-seconds``
is optional (the model guesses when omitted). Provider knobs (loop, prompt_influence)
are passed via ``--option``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..brand import cli_name
from ..core.validate import validate_request
from ..core.logging import get_logger
from ..core.types import SoundEffectRequest
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{cli_name()} sound", description="Generate sound effects.")
    sub = ap.add_subparsers(dest="op", required=True)

    gen = sub.add_parser("generate", help="text -> sound effect")
    gen.add_argument("--text", required=True, help="text description of the sound effect")
    gen.add_argument("--output", required=True, help="path for the generated audio file")
    gen.add_argument("--duration-seconds", dest="duration_seconds", type=float, default=None,
                     help="0.5-30s; omit to let the model choose")
    gen.add_argument("--output-format", dest="output_format", default=None, help="e.g. mp3_44100_128")
    gen.add_argument("--option", nargs="*", default=[], help="loop=true prompt_influence=0.5")
    common.add_global_args(gen)
    return ap


def _do(args) -> object:
    req = SoundEffectRequest(
        text=args.text, output=Path(args.output), duration_seconds=args.duration_seconds,
        output_format=args.output_format, model=args.model, options=common.parse_options(args.option),
    )
    adapter, rb, scene = common.bind(args, req)
    for w in validate_request(req, rb.spec.constraints, common.policy(args), binding=rb.id, scene=scene):
        get_logger().warning("unsupported (proceeding): %s", w)
    return common.stamp(adapter.generate_sound(req), rb, scene)


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
