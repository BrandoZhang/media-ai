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

from ..brand import cli_name
from ..core.errors import ErrorCategory, MediaError
from ..core.types import DialogueRequest, DialogueTurn, SpeechRequest
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{cli_name()} speech", description="Generate speech audio.")
    sub = ap.add_subparsers(dest="op", required=True)

    gen = sub.add_parser("generate", help="text -> speech (single voice)")
    gen.add_argument("--text", required=True, help="text to synthesize")
    gen.add_argument("--output", required=True, help="path for the generated audio file")
    gen.add_argument("--voice", default=None, help="provider voice id (falls back to a provider default)")
    gen.add_argument("--output-format", dest="output_format", default=None, help="e.g. mp3_44100_128")
    gen.add_argument("--language-code", dest="language_code", default=None, help="ISO 639-1")
    gen.add_argument("--seed", type=int, default=None, help="deterministic generation seed, when supported")
    gen.add_argument("--timestamps", type=common.bool_arg, default=False, help="also emit a character-alignment sidecar")
    gen.add_argument("--option", nargs="*", default=[], help="provider-specific key=value options (capability-gated)")
    common.add_global_args(gen)
    common.add_call_headers(gen)

    dlg = sub.add_parser("dialogue", help="multi-voice dialogue -> speech")
    dlg.add_argument("--speaker", action="append", metavar="NAME=VOICE", default=[],
                     help="cast entry mapping a speaker name to a voice id; repeatable")
    dlg.add_argument("--turn", nargs=2, action="append", metavar=("NAME", "TEXT"), default=[],
                     help="a dialogue line by a cast speaker; repeatable (NAME TEXT)")
    dlg.add_argument("--instruction", default=None, help="global director note (provider-dependent)")
    dlg.add_argument("--script", default=None,
                     help="JSON: {\"cast\":{NAME:VOICE}, \"turns\":[{\"speaker\":..,\"text\":..}], \"instruction\"?} "
                          "or a flat [{\"speaker\":..,\"voice\":..,\"text\":..}] list")
    dlg.add_argument("--output", required=True, help="path for the generated dialogue audio file")
    dlg.add_argument("--output-format", dest="output_format", default=None, help="e.g. mp3_44100_128")
    dlg.add_argument("--language-code", dest="language_code", default=None, help="ISO 639-1")
    dlg.add_argument("--seed", type=int, default=None, help="deterministic generation seed, when supported")
    dlg.add_argument("--timestamps", type=common.bool_arg, default=False,
                     help="also emit a character-alignment sidecar")
    dlg.add_argument("--option", nargs="*", default=[], help="provider-specific key=value options (capability-gated)")
    common.add_global_args(dlg)
    common.add_call_headers(dlg)
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
    adapter, rb, scene = common.bind(args, req)
    common.check(req, args, rb, scene)
    return common.produce(adapter.generate_speech, req, rb, scene)


def _do_dialogue(args) -> object:
    cast, turns, instruction = _parse_dialogue(args)
    if not turns:
        raise MediaError("dialogue needs at least one --turn (or turns in --script)", category=ErrorCategory.CLI)
    if not cast:
        raise MediaError("dialogue needs a cast: --speaker NAME=VOICE (or a cast in --script)", category=ErrorCategory.CLI)
    unknown = sorted({t.speaker for t in turns} - set(cast))
    if unknown:
        raise MediaError(f"turn speaker(s) not in cast: {', '.join(unknown)}; add --speaker NAME=VOICE",
                         category=ErrorCategory.CLI)
    req = DialogueRequest(
        turns=turns, cast=cast, instruction=instruction, output=Path(args.output),
        output_format=args.output_format, language_code=args.language_code, seed=args.seed,
        timestamps=args.timestamps, options=common.parse_options(args.option), model=args.model,
    )
    # No "resolve the provider but keep the model" dance any more: dialogue is its own
    # scene, so its default binding is a different entry from plain speech.
    adapter, rb, scene = common.bind(args, req)
    common.check(req, args, rb, scene)
    return common.produce(adapter.generate_dialogue, req, rb, scene)


def _parse_dialogue(args) -> tuple[dict, list[DialogueTurn], str | None]:
    """Assemble the cast (speaker->voice), turns, and instruction from --script (if any)
    then the --speaker / --turn / --instruction flags (flags win)."""
    cast: dict[str, str] = {}
    turns: list[DialogueTurn] = []
    instruction: str | None = None
    if args.script:
        cast, turns, instruction = _parse_script(args.script)
    for entry in args.speaker or []:
        if "=" not in entry:
            raise MediaError(f"--speaker must be NAME=VOICE, got {entry!r}", category=ErrorCategory.CLI)
        name, voice = entry.split("=", 1)
        cast[name.strip()] = voice.strip()
    for speaker, text in args.turn or []:
        turns.append(DialogueTurn(speaker=speaker, text=text))
    if args.instruction is not None:
        instruction = args.instruction
    return cast, turns, instruction


def _parse_script(path: str) -> tuple[dict, list[DialogueTurn], str | None]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MediaError(f"could not read --script {path!r}: {exc}", category=ErrorCategory.CLI) from exc
    cast: dict[str, str] = {}
    turns: list[DialogueTurn] = []
    instruction: str | None = None
    if isinstance(raw, dict):  # {cast, turns, instruction?}
        cast = {str(k): str(v) for k, v in (raw.get("cast") or {}).items()}
        instruction = raw.get("instruction")
        for i, item in enumerate(raw.get("turns") or []):
            if not isinstance(item, dict) or "speaker" not in item or "text" not in item:
                raise MediaError(f"--script turns[{i}] must have 'speaker' and 'text'", category=ErrorCategory.CLI)
            turns.append(DialogueTurn(speaker=str(item["speaker"]), text=str(item["text"])))
    elif isinstance(raw, list):  # flat [{speaker, voice, text}] -> derive cast (last wins)
        for i, item in enumerate(raw):
            if not isinstance(item, dict) or not {"speaker", "voice", "text"} <= item.keys():
                raise MediaError(f"--script[{i}] must have 'speaker', 'voice', and 'text'", category=ErrorCategory.CLI)
            cast[str(item["speaker"])] = str(item["voice"])
            turns.append(DialogueTurn(speaker=str(item["speaker"]), text=str(item["text"])))
    else:
        raise MediaError("--script must be a JSON object {cast,turns} or a list of {speaker,voice,text}",
                         category=ErrorCategory.CLI)
    return cast, turns, instruction


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
