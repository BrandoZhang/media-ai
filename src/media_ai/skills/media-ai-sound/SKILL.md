---
name: media-ai-sound
description: >-
  Generate a sound effect from a text description via the media-ai CLI. Covers
  one-shot or looping SFX with an optional target duration. Use when asked to create,
  generate, or make a sound effect / SFX / foley / whoosh / impact / ambience / UI
  sound from text on the command line.
version: 2.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai capabilities --scene sound.text_to_sound"
  install:
    tier: optional
    summary: >-
      Generate sound effects from a description — whooshes, impacts, foley, UI
      clicks, ambience — one-shot or seamlessly looping.
---

# media-ai-sound — text → sound effect

> **Read `../media-ai-shared/SKILL.md` first** for the machine contract, how a binding
> is named, and credentials. Sound effects are part of the **`audio`** modality and are
> **synchronous** — no `job` polling.

One scene: **`media-ai sound generate`** (`sound.text_to_sound`) — `--text` describing
the effect → an audio file.

## Discover first

```bash
media-ai capabilities --scene sound.text_to_sound --configured
media-ai capabilities --binding <id> --pretty
```

Read `constraints.audio.formats`, the declared duration bounds, and
`constraints.options[]`.

## Flags

| flag | meaning |
|---|---|
| `--text` (required) | describe the sound effect |
| `--output` (required) | output audio path |
| `--duration-seconds N` | within the binding's declared bounds; omit to let the model choose |
| `--output-format FMT` | codec, e.g. `mp3_44100_128` |
| `--option key=value` | per-binding extras, gated on `constraints.options[]` — commonly a seamless-`loop` toggle and a prompt-adherence dial |

```bash
# One-shot effect, model picks the length
media-ai sound generate \
    --text "a heavy wooden door creaking open, then slamming shut" \
    --output door.mp3

# A seamless 4-second loop, tight to the prompt (option keys per `capabilities`)
media-ai sound generate \
    --text "gentle rain on a tin roof" --duration-seconds 4 \
    --option loop=true prompt_influence=0.7 --output rain_loop.mp3
```

## Gotchas

- `--duration-seconds` outside the binding's declared bounds is exit 3 before the
  call; omit it to let the model decide.
- Where a `loop` option exists it yields a seamless loop; a prompt-adherence dial
  raised toward `1` sticks closer to the text, lowered leaves more variation.
- Cost is metered like other audio ops (`speech_characters` ledger field) — see
  `media-ai-usage`.
