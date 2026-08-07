---
name: media-ai-music
description: >-
  Compose music from a text prompt or a structured composition plan via the media-ai
  CLI. Covers prompt→song, plan→song, and a credit-free prompt→plan helper you can
  edit and feed back. Use when asked to compose, generate, or make a song / track /
  background music / soundtrack / jingle from a description on the command line.
version: 2.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai capabilities --scene music.text_to_music"
  install:
    tier: optional
    summary: >-
      Compose a song, a soundtrack, or a jingle from a description — or from an
      editable composition plan you can revise before spending credits.
---

# media-ai-music — compose music

> **Read `../media-ai-shared/SKILL.md` first** for the machine contract, how a binding
> is named, and credentials. Music is part of the **`audio`** modality and is
> **synchronous** — no `job` polling.

Three scenes under `media-ai music`:

| command | scene | meaning |
|---|---|---|
| `music generate --prompt` | `music.text_to_music` | describe a song, get audio |
| `music generate --plan` | `music.plan_to_music` | render an edited composition plan |
| `music plan` | `music.plan` | prompt → a plan JSON, **credit-free** |

## Discover first

```bash
media-ai capabilities --scene music.text_to_music --configured   # who can serve this?
media-ai capabilities --binding <id> --pretty                    # what does it accept?
```

Read `constraints.audio.formats`, the declared duration bounds, and
`constraints.options[]`. A binding that serves `music.text_to_music` need not serve
`music.plan` — the plan pass is a separate scene and is checked before the call.

## `music generate`

`--prompt` and `--plan` are **mutually exclusive** — supply exactly one.

| flag | meaning |
|---|---|
| `--prompt TEXT` | describe the song (prompt mode) |
| `--plan FILE.json` | a composition plan from `music plan` (plan mode) |
| `--output` (required) | output audio path |
| `--duration-ms N` | song length in ms, **prompt mode only**; within the binding's declared bounds |
| `--output-format FMT` | codec, or `auto` (e.g. `mp3_44100_128`) |
| `--seed N` | **plan mode only** |
| `--detailed true` | use `/v1/music/detailed`; also writes a `<output>.metadata.json` sidecar (composition plan + song metadata) |
| `--option key=value` | per-binding extras, gated on `constraints.options[]` (read them from `capabilities`) |

```bash
# Prompt → song
media-ai music generate \
    --prompt "warm lofi hip-hop beat, vinyl crackle, mellow rhodes" \
    --duration-ms 30000 --output-format mp3_44100_128 --output beat.mp3

# Plan → song (edit the plan first; seed for reproducibility)
media-ai music generate --plan song_plan.json --seed 42 --detailed true --output song.mp3
```

## `music plan` — credit-free structure pass

```bash
media-ai music plan \
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

## References

- `references/bindings/<provider>.<model>.md` — what one binding is good at, how to
  prompt it, and its traps. Only bindings with something non-obvious to say have one.
- `../media-ai-shared/references/bindings.md` — how to read `bindings list` /
  `capabilities` output and pick between candidates.
