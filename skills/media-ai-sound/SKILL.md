---
name: media-ai-sound
description: >-
  Generate a sound effect from a text description via the media-ai CLI, backed by
  ElevenLabs sound generation. Covers one-shot or looping SFX with an optional target
  duration. Use when asked to create, generate, or make a sound effect / SFX / foley
  / whoosh / impact / ambience / UI sound from text on the command line.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai capabilities --provider elevenlabs --pretty"
---

# media-ai-sound — text → sound effect

> **Read `../media-ai-shared/SKILL.md` first** for the machine contract, provider
> selection, and credentials. Sound effects are part of the **`audio`** modality, are
> **synchronous** (no `job` polling), and are served by **`elevenlabs`** (the offline
> `mock` default renders a placeholder tone).

One operation: **`media-ai sound generate`** — `--text` describing the effect → an
audio file.

## Discover first

```bash
media-ai capabilities --provider elevenlabs --pretty
```

Read `audio.supports_sound`, `sound_output_formats`, `sound_min_seconds` /
`sound_max_seconds`, and `sound_options[]`.

## Flags

| flag | meaning |
|---|---|
| `--text` (required) | describe the sound effect |
| `--output` (required) | output audio path |
| `--duration-seconds N` | **0.5–30s**; omit to let the model choose the length |
| `--output-format FMT` | codec, e.g. `mp3_44100_128` |
| `--option key=value` | `loop=true` (seamless loop), `prompt_influence=0.5` (0–1: adherence vs. creativity) |

```bash
# One-shot effect, model picks the length
media-ai sound generate --provider elevenlabs \
    --text "a heavy wooden door creaking open, then slamming shut" \
    --output door.mp3

# A seamless 4-second loop, tight to the prompt
media-ai sound generate --provider elevenlabs \
    --text "gentle rain on a tin roof" --duration-seconds 4 \
    --option loop=true prompt_influence=0.7 --output rain_loop.mp3
```

## Gotchas

- `--duration-seconds` must be within **0.5–30**; omit it to let the model decide.
- `loop=true` yields a seamless loop; raise `prompt_influence` toward `1` for stricter
  adherence, lower it for more variation.
- Cost is metered like other audio ops (`speech_characters` ledger field) — see
  `media-ai-usage`.
