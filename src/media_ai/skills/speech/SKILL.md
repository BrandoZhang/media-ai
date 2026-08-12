---
name: {{skill}}speech
description: >-
  Turn text into spoken audio via the {{cli}} CLI — single-voice text-to-speech and
  multi-voice dialogue. Handles voice selection, output codec/format, per-character
  timestamp sidecars, and the speaker/cast dialogue model. Use when asked to narrate,
  voice, read aloud, or synthesize speech / a voiceover / a conversation / a
  podcast-style dialogue from text on the command line.
version: 2.0.0
metadata:
  requires:
    bins: ["{{cli}}"]
  cliHelp: "{{cli}} capabilities --scene speech.text_to_speech"
  install:
    tier: optional
    summary: >-
      Read text aloud — a single narrator or a multi-voice conversation — plus
      per-character timestamp sidecars for subtitles.
---

# {{skill}}speech — text → speech

> **Read `../{{skill}}shared/SKILL.md` first** for the machine contract (one JSON
> object on stdout, exit-code categories), how a binding is named, and credentials.
> Speech is part of the **`audio`** modality and is **synchronous** on every real
> backend — no `job` polling.

## Two scenes

| command | scene | meaning |
|---|---|---|
| `{{cli}} speech generate` | `speech.text_to_speech` | one voice reads `--text` |
| `{{cli}} speech dialogue` | `speech.dialogue` | several voices act out a script |

**A binding that serves one need not serve the other.** Ask which do:

```bash
{{cli}} capabilities --scene speech.dialogue --configured   # who can act out a script?
{{cli}} capabilities --binding <id> --pretty                # what does that one accept?
```

Read `constraints.audio.formats`, `.default_voice`, `.max_dialogue_voices`,
`.max_characters`, `constraints.supports.{seed,language_code,timestamps,instruction}`
and `constraints.options[]`. An unsupported field fails with **exit 3** *before any
network call*. This skill lists no voice ids or model names on purpose — voices are
account-specific and lineups change; `capabilities` is the only current answer.

## `speech generate` — flags

| flag | meaning |
|---|---|
| `--text` (required) | the text to speak |
| `--output` (required) | output audio path (extension is a hint; the codec follows `--output-format`) |
| `--voice ID` | voice id / name for this binding; falls back to its declared default |
| `--output-format FMT` | e.g. `codec_samplerate_bitrate`; must be in `constraints.audio.formats` |
| `--language-code ISO` | ISO 639-1 hint, where `supports.language_code` is true |
| `--seed N` | reproducibility, where `supports.seed` is true |
| `--timestamps true` | also write a `<output>.timestamps.json` per-character alignment sidecar (an extra artifact, `role: "alignment"`) |
| `--option key=value` | per-binding voice knobs, gated on `constraints.options[]` |

```bash
# Use the configured default for this scene
{{cli}} speech generate --text "Welcome aboard. Please fasten your seatbelt." \
    --output welcome.mp3

# A specific binding, a specific voice, tuned with its own option keys
{{cli}} speech generate --binding <provider>/<model> \
    --text "Welcome aboard." --voice <voice-id> --output-format mp3_44100_128 \
    --option stability=0.4 --output welcome.mp3
```

## `speech dialogue` — the cast model

One representation serves every backend: a **cast** (speaker name → voice) plus
ordered **turns** (which speaker says what).

| flag | meaning |
|---|---|
| `--speaker NAME=VOICE` | cast entry; repeatable |
| `--turn NAME TEXT` | one line by a cast speaker; repeatable, order preserved |
| `--instruction TEXT` | global director note; only where `supports.instruction` is true, else exit 3 |
| `--script FILE.json` | `{"cast":{NAME:VOICE},"turns":[{"speaker":..,"text":..}],"instruction"?}` **or** a flat `[{"speaker":..,"voice":..,"text":..}]` list |
| `--output` (required), `--output-format`, `--language-code`, `--seed`, `--timestamps`, `--option` | as for `generate` |

```bash
{{cli}} speech dialogue \
    --speaker Ana=<voice-id> --speaker Ben=<voice-id> \
    --turn Ana "Did you see the numbers?" \
    --turn Ben "I did — they're up thirty percent." \
    --timestamps true --output chat.mp3
```

The cast size is capped by `constraints.audio.max_dialogue_voices` — some bindings
allow a handful of speakers, others two. Check before writing a long script.

## Gotchas that hold across bindings

- **Delivery is often prompt-level, not a flag.** Where a binding has no
  `--instruction` and few option knobs, accent, pace, emotion and effects like
  `[laughs]` are written **into the text itself**. That is a per-binding technique —
  see its fragment under `references/bindings/`.
- **In-text direction is not portable, and failing to port it is silent.** Which markup
  a model understands — bracketed tags, break tags, a plain-language stage note — varies
  per binding, and one that does not know a marker **speaks it** rather than refusing
  it. Nothing errors and you are charged in full. Read the target binding's fragment
  before moving a script between bindings.
- **Keep total text within `constraints.audio.max_characters`.** Exceeding it is exit 3
  before the call, but only where the binding declares the cap.
- **A silent safety drop is an error, not an empty file.** A 200-OK carrying no audio
  surfaces as a `safety` failure (**exit 8**).
- `--timestamps true` adds a **second artifact**; dialogue sidecars also carry
  `voice_segments`. Cost is metered in `speech_characters` — see `{{skill}}usage`.

## References

- `references/dialogue.md` — the cast/turns/`--script` model in full, with JSON shapes.
- `references/bindings/<provider>.<model>.md` — what one binding is good at, how to
  direct it, and its traps.
- `../{{skill}}shared/references/bindings.md` — how to read `bindings list` /
  `capabilities` output and pick between candidates.
