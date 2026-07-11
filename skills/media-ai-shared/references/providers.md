# media-ai providers — selection, matrix, tuning

Three real backends plus the offline `mock` default. `media-ai capabilities` is the
**authoritative, live** source of what each model supports; this table is a map for
picking a starting point.

## At a glance

| provider | images | video | video async? | cancel job? | auth env |
|---|---|---|---|---|---|
| `mock` | ✓ (Pillow) | ✓ (ffmpeg) | simulated | ✓ | — (offline default) |
| `volc` — Volcengine Ark | ✓ Seedream | ✓ Seedance | yes | ✓ | `ARK_API_KEY` / `VOLC_API_KEY` |
| `openai` | ✓ GPT-Image / DALL·E | ✗ (Sora retired) | — | — | `OPENAI_API_KEY` |
| `gemini` | ✓ Nano Banana | ✓ Veo | yes | ✗ (Veo can't cancel) | `GEMINI_API_KEY` / `GOOGLE_API_KEY` |

## Provider / model routing

- Explicit `--provider` wins.
- Else a profile's provider (`--provider-profile`).
- Else **inferred from `--model`** by hint: `doubao*`/`seedream*`/`seedance*` ⇒ `volc`;
  `gpt-image*`/`dall-e*` ⇒ `openai`; `gemini-*`/`veo-*` ⇒ `gemini`.
- Else `$MEDIA_PROVIDER` / `$MEDIA_BACKEND`, else `mock`.

Default models (override with the env vars below or `--model`):

| provider | default image model | default video model |
|---|---|---|
| `volc` | `doubao-seedream-4-5-251128` | `doubao-seedance-2-0-260128` |
| `openai` | `gpt-image-2` | — (no video; Sora retired) |
| `gemini` | `gemini-3.1-flash-image` | `veo-3.1-generate-preview` |

`media-ai capabilities` discovery lists these models: **openai** — `gpt-image-2`,
`gpt-image-1`, `gpt-image-1-mini`, `dall-e-3`, `dall-e-2` (image only; Sora retired);
**gemini** — `gemini-3.1-flash-image`, `gemini-3.1-flash-lite-image`,
`gemini-3-pro-image`, `gemini-2.5-flash-image`, `veo-3.1-generate-preview`,
`veo-3.1-fast-generate-preview`, `veo-3.1-lite-generate-preview`; **volc** — the two
configured Ark models (image + video). Some deprecated models still resolve via
`--model` but are omitted from discovery.

## Endpoint / base URL and tuning env vars

Base URL precedence: profile `base_url` → env override → hardcoded default.

| provider | base-url env (default) | model overrides | poll tuning |
|---|---|---|---|
| `volc` | `ARK_BASE_URL` (`https://ark.cn-beijing.volces.com/api/v3`) | `ARK_IMAGE_MODEL`, `ARK_VIDEO_MODEL`, `ARK_IMAGE_SIZE` | `ARK_POLL_INTERVAL` (5s), `ARK_POLL_TIMEOUT` (900s) |
| `openai` | `OPENAI_BASE_URL` (`https://api.openai.com/v1`) | `OPENAI_IMAGE_MODEL` | — (image only) |
| `gemini` | `GEMINI_BASE_URL` (`https://generativelanguage.googleapis.com/v1beta`) | `GEMINI_IMAGE_MODEL`, `GEMINI_VIDEO_MODEL` | `GEMINI_POLL_INTERVAL` (10s), `GEMINI_POLL_TIMEOUT` (1200s), `GEMINI_INLINE_MAX_BYTES` (12 MB) |

Per-modality capability detail lives in the `media-ai-image` and `media-ai-video`
skills; human setup notes are in `../../../docs/PROVIDERS.md`. Adding a new provider
needs no core changes — see `../../../docs/EXTENDING.md`.
