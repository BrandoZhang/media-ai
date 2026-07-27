---
name: media-ai-music
description: >-
  Compose music from a text prompt or a structured composition plan via the media-ai
  CLI, backed by ElevenLabs Music. Covers prompt→song, plan→song, and a credit-free
  prompt→plan helper you can edit and feed back. Use when asked to compose, generate,
  or make a song / track / background music / soundtrack / jingle from a description
  on the command line.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai capabilities --provider elevenlabs --pretty"
  install:
    tier: optional
    summary: >-
      Compose a song, a soundtrack, or a jingle from a description — or from an
      editable composition plan you can revise before spending credits. ElevenLabs
      Music.
---

# media-ai-music — compose music

> **Read `../media-ai-shared/SKILL.md` first** for the machine contract, provider
> selection, and credentials. Music is part of the **`audio`** modality, is
> **synchronous** (no `job` polling), and is served by **`elevenlabs`** (the offline
> `mock` default renders a placeholder tone).

Two operations under `media-ai music`:

1. **`music generate`** — a `--prompt` **or** a `--plan` composition-plan JSON → a song.
2. **`music plan`** — a `--prompt` → a **composition plan** (JSON). **Credit-free** —
   iterate on structure/sections before spending on audio.

## Discover first

```bash
media-ai capabilities --provider elevenlabs --pretty
```

Read `audio.supports_music`, `supports_composition_plan`, `music_models`,
`music_output_formats`, `music_min_ms` / `music_max_ms`, and `music_options[]`.

## `music generate`

`--prompt` and `--plan` are **mutually exclusive** — supply exactly one.

| flag | meaning |
|---|---|
| `--prompt TEXT` | describe the song (prompt mode) |
| `--plan FILE.json` | a composition plan from `music plan` (plan mode) |
| `--output` (required) | output audio path |
| `--duration-ms N` | song length in ms, **prompt mode only** (≈ 3000–600000) |
| `--output-format FMT` | codec, or `auto` (e.g. `mp3_44100_128`) |
| `--seed N` | **plan mode only** |
| `--detailed true` | use `/v1/music/detailed`; also writes a `<output>.metadata.json` sidecar (composition plan + song metadata) |
| `--option key=value` | `force_instrumental`, `respect_sections_durations`, `store_for_inpainting`, `sign_with_c2pa`, `with_timestamps` |

```bash
# Prompt → song
media-ai music generate --provider elevenlabs \
    --prompt "warm lofi hip-hop beat, vinyl crackle, mellow rhodes" \
    --duration-ms 30000 --output-format mp3_44100_128 \
    --option force_instrumental=true --output beat.mp3

# Plan → song (edit the plan first; seed for reproducibility)
media-ai music generate --provider elevenlabs \
    --plan song_plan.json --seed 42 --detailed true --output song.mp3
```

## `music plan` — credit-free structure pass

```bash
media-ai music plan --provider elevenlabs \
    --prompt "upbeat indie-pop, verse/chorus/bridge, ~2 min" \
    --duration-ms 120000 --output song_plan.json
# → edit song_plan.json, then: music generate --plan song_plan.json
```

`--source-plan FILE.json` refines an existing plan instead of starting fresh.

## Gotchas

- Exactly one of `--prompt` / `--plan` on `generate` (else exit 2).
- `--duration-ms` applies to **prompt** mode; `--seed` to **plan** mode.
- `music plan` spends **no credits** — use it to iterate before generating audio.
- Cost is metered like other audio ops (`speech_characters` ledger field) — see
  `media-ai-usage`.
