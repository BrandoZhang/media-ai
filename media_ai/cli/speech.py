"""``media-ai speech`` — provider-agnostic text-to-speech.

``speech generate`` renders text with a single voice; ``speech dialogue`` renders an
ordered list of (voice, text) turns into one multi-voice track. ``--timestamps true``
also emits a character-alignment JSON sidecar next to the audio. Provider-specific
voice knobs (stability, similarity_boost, style, speed, …) are passed via ``--option``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core import registry
from ..core.capabilities import validate_request
from ..core.errors import ErrorCategory, MediaError
from ..core.logging import get_logger
from ..core.types import DialogueRequest, DialogueTurn, Modality, SpeechRequest
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai speech", description="Generate speech audio.")
    sub = ap.add_subparsers(dest="op", required=True)

    gen = sub.add_parser("generate", help="text -> speech (single voice)")
    gen.add_argument("--text", required=True)
    gen.add_argument("--output", required=True)
    gen.add_argument("--voice", default=None, help="provider voice id (falls back to a provider default)")
    gen.add_argument("--output-format", dest="output_format", default=None, help="e.g. mp3_44100_128")
    gen.add_argument("--language-code", dest="language_code", default=None, help="ISO 639-1")
    gen.add_argument("--seed", type=int, default=None)
    gen.add_argument("--timestamps", type=common.bool_arg, default=False, help="also emit a character-alignment sidecar")
    gen.add_argument("--option", nargs="*", default=[])
    common.add_global_args(gen)

    dlg = sub.add_parser("dialogue", help="multi-voice dialogue -> speech")
    dlg.add_argument("--turn", nargs=2, action="append", metavar=("VOICE_ID", "TEXT"), default=[],
                     help="a dialogue turn; repeatable (VOICE_ID TEXT)")
    dlg.add_argument("--script", default=None, help="JSON file: [{\"voice_id\": ..., \"text\": ...}, ...]")
    dlg.add_argument("--output", required=True)
    dlg.add_argument("--output-format", dest="output_format", default=None, help="e.g. mp3_44100_128")
    dlg.add_argument("--language-code", dest="language_code", default=None, help="ISO 639-1")
    dlg.add_argument("--seed", type=int, default=None)
    dlg.add_argument("--timestamps", type=common.bool_arg, default=False)
    dlg.add_argument("--option", nargs="*", default=[])
    common.add_global_args(dlg)
    return ap


def _do(args) -> object:
    if args.op == "dialogue":
        return _do_dialogue(args)
    return _do_generate(args)


def _do_generate(args) -> object:
    req = SpeechRequest(
        text=args.text, output=Path(args.output), voice=args.voice,
        output_format=args.output_format, language_code=args.language_code, seed=args.seed,
        timestamps=args.timestamps, options=common.parse_options(args.option),
    )
    provider, model = registry.build(common.provider_name(args), args.model, Modality.AUDIO,
                                     profile=args.provider_profile)
    req.model = model
    for w in validate_request(req, provider.capabilities(model, Modality.AUDIO), common.policy(args)):
        get_logger().warning("unsupported (proceeding): %s", w)
    return provider.generate_speech(req)


def _do_dialogue(args) -> object:
    turns = _parse_turns(args)
    if not turns:
        raise MediaError("dialogue needs at least one --turn or a --script file", category=ErrorCategory.CLI)
    req = DialogueRequest(
        turns=turns, output=Path(args.output), output_format=args.output_format,
        language_code=args.language_code, seed=args.seed, timestamps=args.timestamps,
        options=common.parse_options(args.option),
    )
    provider, model = registry.build(common.provider_name(args), args.model, Modality.AUDIO,
                                     profile=args.provider_profile)
    req.model = model
    for w in validate_request(req, provider.capabilities(model, Modality.AUDIO), common.policy(args)):
        get_logger().warning("unsupported (proceeding): %s", w)
    return provider.generate_dialogue(req)


def _parse_turns(args) -> list[DialogueTurn]:
    """Turns come from a --script JSON file (each {text, voice_id}) then any --turn flags."""
    turns: list[DialogueTurn] = []
    if args.script:
        try:
            raw = json.loads(Path(args.script).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MediaError(f"could not read --script {args.script!r}: {exc}", category=ErrorCategory.CLI) from exc
        if not isinstance(raw, list):
            raise MediaError("--script must be a JSON array of {text, voice_id}", category=ErrorCategory.CLI)
        for i, item in enumerate(raw):
            if not isinstance(item, dict) or "text" not in item or "voice_id" not in item:
                raise MediaError(f"--script[{i}] must have 'text' and 'voice_id'", category=ErrorCategory.CLI)
            turns.append(DialogueTurn(text=str(item["text"]), voice=str(item["voice_id"])))
    for voice_id, text in args.turn or []:
        turns.append(DialogueTurn(text=text, voice=voice_id))
    return turns


def main() -> int:
    args = _build_parser().parse_args()
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
