---
name: media-ai-video
description: >-
  Generate video from a text prompt, a first/last frame image, or multimodal
  references (images/videos/audio) via the media-ai CLI, across Volcengine Seedance,
  Google Veo, and OpenAI Sora. Handles the async job flow (blocking or --wait false
  + poll). Use when asked to create, generate, make, or animate a video / clip /
  movie / animation from text or an image on the command line.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai capabilities --model veo-3.1-generate-preview"
---

# media-ai-video — generate video

> **Read `../media-ai-shared/SKILL.md` first** for the machine contract, provider
> selection, and credentials. **Video is async on every real provider** — see the
> `--wait` behavior below and the `media-ai-job` skill for polling/cancelling.

One command, `media-ai video generate`, covers three input modes via one normalized request:

1. **text → video** — just `--prompt`.
2. **image → video** — `--first-frame` (and optional `--last-frame`) to animate stills.
3. **references → video** — `--reference-image` / `--reference-video` / `--reference-audio`.

## Discover first

Durations, resolutions, audio, and frame/reference support differ sharply per model
(and per tier). Check before you submit:

```bash
media-ai capabilities --provider gemini --model veo-3.1-generate-preview --pretty
media-ai capabilities --provider volc --pretty
```

Read `video.is_async`, `resolutions`, `durations`, `aspect_ratios`,
`supports_first_frame` / `supports_last_frame`, `supports_reference_*`,
`supports_audio`, `supports_watermark_control`, `supports_cancel`, and `options[]`.

## Async: `--wait`

- `--wait true` (**default**) — the CLI blocks, polls the task to completion, and
  downloads the video. Returns a `GenerationResult`.
- `--wait false` — submit and return a **`JobHandle`** immediately with a ready-to-run
  `poll` command. Finalize with the `media-ai-job` skill (`job query --output`).

```json
{"status":"queued","job":{"provider":"gemini","id":"<op>"},
 "poll":"media-ai job query --provider gemini --id <op> --output clip.mp4"}
```

> **Cancel support:** `volc` (Seedance) and `openai` (Sora) support `job cancel`;
> **Gemini/Veo cannot be cancelled** (exit 3). With `--wait true`, `volc` also
> cancels the *billed* task if the process is killed (SIGTERM/SIGINT/timeout).

## Core flags

| flag | meaning |
|---|---|
| `--output` (required) | output video path |
| `--prompt` | text prompt (default empty; required in practice for text→video) |
| `--first-frame PATH` / `--last-frame PATH` | animate from/to a still |
| `--reference-image` / `--reference-video` / `--reference-audio PATH...` | multimodal references (JSON array ok) |
| `--duration N` / `--seconds N` | clip length in seconds (model-dependent set) |
| `--resolution {480p,720p,1080p,...}` + `--aspect-ratio R` | geometry (or `--size WxH`) |
| `--seed N` | reproducibility (where supported) |
| `--audio {true,false}` | request generated audio (where supported) |
| `--watermark {true,false}` | watermark control (default false = no watermark, where supported) |
| `--negative-prompt TEXT` | what to avoid |
| `--return-last-frame {true,false}` | also return the final frame as an artifact |
| `--wait {true,false}` | block+poll (default) vs async submit |
| `--option key=value` | provider-specific, capability-gated (e.g. volc `camera_fixed`) |

## Quick starts

```bash
# text → video, blocking (Seedance)
media-ai video generate --provider volc \
    --prompt "a paper boat sailing down a rain gutter, cinematic" \
    --resolution 720p --aspect-ratio 16:9 --duration 5 --output boat.mp4

# image → video with audio (Veo), async submit
media-ai video generate --provider gemini --model veo-3.1-generate-preview \
    --first-frame hero.png --prompt "she turns and smiles" \
    --resolution 1080p --duration 6 --audio true --wait false --output hero.mp4

# references → video (Sora, experimental)
media-ai video generate --provider openai --model sora-2 \
    --prompt "montage in this style" --reference-image '["a.png","b.png"]' \
    --resolution 720p --duration 8 --output montage.mp4
```

## References

- `references/generate.md` — full flag semantics + the three input modes with examples.
- `references/providers.md` — video model matrix (Seedance / Veo tiers / Sora): durations, resolutions, audio, frames, references, cancel, options.
