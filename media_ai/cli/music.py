"""``media-ai music`` — compose songs (ElevenLabs Music).

``music generate`` composes a song from a ``--prompt`` **or** a ``--plan`` composition
plan (JSON); ``--detailed`` also writes the model's plan + song metadata as a sidecar.
``music plan`` generates a composition plan (JSON) from a prompt — a credit-free helper
whose output can be edited and fed back into ``music generate --plan``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core import registry
from ..core.capabilities import validate_request
from ..core.errors import ErrorCategory, MediaError
from ..core.logging import get_logger
from ..core.types import Modality, MusicPlanRequest, MusicRequest
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai music", description="Compose music.")
    sub = ap.add_subparsers(dest="op", required=True)

    gen = sub.add_parser("generate", help="prompt or composition plan -> song")
    gen.add_argument("--prompt", default=None, help="text prompt (mutually exclusive with --plan)")
    gen.add_argument("--plan", default=None, help="composition-plan JSON file (mutually exclusive with --prompt)")
    gen.add_argument("--output", required=True)
    gen.add_argument("--duration-ms", dest="duration_ms", type=int, default=None, help="song length (prompt mode)")
    gen.add_argument("--output-format", dest="output_format", default=None, help="e.g. mp3_44100_128 or auto")
    gen.add_argument("--seed", type=int, default=None, help="composition-plan mode only")
    gen.add_argument("--detailed", type=common.bool_arg, default=False, help="also write a <output>.metadata.json sidecar")
    gen.add_argument("--option", nargs="*", default=[])
    common.add_global_args(gen)

    plan = sub.add_parser("plan", help="prompt -> composition plan (JSON; credit-free)")
    plan.add_argument("--prompt", required=True)
    plan.add_argument("--output", required=True, help="where to write the composition-plan JSON")
    plan.add_argument("--duration-ms", dest="duration_ms", type=int, default=None)
    plan.add_argument("--source-plan", dest="source_plan", default=None, help="optional source composition-plan JSON to refine")
    plan.add_argument("--option", nargs="*", default=[])
    common.add_global_args(plan)
    return ap


def _load_json(path: str, flag: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MediaError(f"could not read {flag} {path!r}: {exc}", category=ErrorCategory.CLI) from exc
    if not isinstance(data, dict):
        raise MediaError(f"{flag} must be a JSON object", category=ErrorCategory.CLI)
    return data


def _do(args) -> object:
    return _do_plan(args) if args.op == "plan" else _do_generate(args)


def _do_generate(args) -> object:
    if bool(args.prompt) == bool(args.plan):
        raise MediaError("provide exactly one of --prompt or --plan", category=ErrorCategory.CLI)
    req = MusicRequest(
        output=Path(args.output), prompt=args.prompt,
        composition_plan=_load_json(args.plan, "--plan") if args.plan else None,
        duration_ms=args.duration_ms, output_format=args.output_format, seed=args.seed,
        detailed=args.detailed, model=args.model, options=common.parse_options(args.option),
    )
    provider = _provider(args)
    for w in validate_request(req, provider.capabilities(args.model, Modality.AUDIO), common.policy(args)):
        get_logger().warning("unsupported (proceeding): %s", w)
    return provider.generate_music(req)


def _do_plan(args) -> object:
    req = MusicPlanRequest(
        prompt=args.prompt, output=Path(args.output), duration_ms=args.duration_ms,
        source_plan=_load_json(args.source_plan, "--source-plan") if args.source_plan else None,
        model=args.model, options=common.parse_options(args.option),
    )
    provider = _provider(args)
    for w in validate_request(req, provider.capabilities(args.model, Modality.AUDIO), common.policy(args)):
        get_logger().warning("unsupported (proceeding): %s", w)
    return provider.generate_music_plan(req)


def _provider(args):
    # Resolve only the provider; the request keeps its own model (adapter picks the
    # music default when --model is omitted, not the speech default).
    provider, _ = registry.build(common.provider_name(args), args.model, Modality.AUDIO,
                                 profile=args.provider_profile)
    return provider


def main() -> int:
    args = _build_parser().parse_args()
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
