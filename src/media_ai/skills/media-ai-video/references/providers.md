# Video model matrix

`media-ai capabilities --provider <p>` is authoritative and live. All real-provider
video is **async** (`is_async: true`); use `--wait true` to block or `--wait false` +
`media-ai-job` to poll.

## Volcengine — Seedance (`doubao-seedance-2-0-…`)

| aspect | value |
|---|---|
| ops | `video.generate` (async) |
| ratios | `16:9, 9:16, 1:1, 4:3, 3:4, 21:9, adaptive` |
| resolutions | `480p, 720p, 1080p` |
| durations | model-version specific (unconstrained set; defaults to 5s) |
| frames | first ✓, last ✓ |
| references | images ✓, videos ✓, audios ✓ |
| seed / audio / watermark | ✓ / ✓ (`generate_audio`) / ✓ |
| return_last_frame | ✓ |
| **cancel** | ✓ |
| options | `camera_fixed` (only sent when you pass it — some models reject it otherwise) |

`--wait true` installs signal handlers so a killed wait cancels the **billed** task
rather than orphaning it. Custom endpoint ids (`ep-…`): geometry is validated by the
Ark API, modality is taken from the command.

## Google — Veo (async long-running op; **cannot cancel**)

| model (tier) | ratios | resolutions | durations | audio | first/last frame | ref images | ref videos |
|---|---|---|---|---|---|---|---|
| `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview` | `16:9, 9:16` | `720p, 1080p, 4k` | `4, 6, 8` | ✓ | ✓ / ✓ | up to 3 (asset) | extension (URI only) |
| `veo-3.1-lite-generate-preview` | `16:9, 9:16` | `720p, 1080p` | `4, 6, 8` | ✓ | ✓ / ✓ | ✗ | ✗ |
| `veo-2.0` (deprecated) | `16:9, 9:16` | `720p` | `5, 6, 7, 8` | ✗ | ✓ / ✗ | — | — |

`supports_seed` ✓, `supports_negative_prompt` ✓, `supports_cancel` **✗** (`job cancel`
→ exit 3). Options: `person_generation`. SynthID watermark is unconditional. A
200-OK with no video = a silent safety drop → **exit 8** (`safety`), not an empty file.
Downloaded file URIs may expire — finalize promptly with `media-ai job query --output`.

## OpenAI — no video model

Sora was retired (OpenAI removed the Videos API), so `--provider openai` has **no
video capability**. `sora-*` ids still route to openai but return a deterministic
`unsupported` error (exit 3) — use `volc` or `gemini` for video.

> Durations, resolutions, audio, and frame/reference support vary by tier — confirm
> with `media-ai capabilities --model <m>` before submitting a long (billed) job.
