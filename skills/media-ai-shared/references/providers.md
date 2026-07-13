# media-ai providers — selection, matrix, tuning

Four real backends plus the offline `mock` default, across three modalities (image,
video, audio). `media-ai capabilities` is the **authoritative, live** source of what
each model supports; this table is a map for picking a starting point.

## At a glance

| provider | images | video | audio (speech/music/sound) | auth env |
|---|---|---|---|---|
| `mock` | ✓ (Pillow) | ✓ (ffmpeg, simulated async) | ✓ (stdlib tones) | — (offline default) |
| `volc` — Volcengine Ark | ✓ Seedream | ✓ Seedance (async, cancellable) | — | `ARK_API_KEY` / `VOLC_API_KEY` |
| `openai` | ✓ GPT-Image | — (Sora retired) | — | `OPENAI_API_KEY` |
| `gemini` | ✓ Nano Banana | ✓ Veo (async, no cancel) | ✓ speech (TTS, ≤2-voice dialogue) | `GEMINI_API_KEY` / `GOOGLE_API_KEY` |
| `elevenlabs` | — | — | ✓ speech + dialogue + music + sound | `ELEVENLABS_API_KEY` / `ELEVEN_API_KEY` |

Video is **async** on every real provider (poll with `media-ai-job`); **audio is
synchronous** everywhere. Only Seedance (`volc`) can cancel a running video job.

## Provider / model routing

- Explicit `--provider` wins.
- Else a profile's provider (`--provider-profile`).
- Else **inferred from `--model`** by hint: `doubao*`/`seedream*`/`seedance*` ⇒ `volc`;
  `gpt-image*` ⇒ `openai`; `gemini-*` (incl. `*-tts`)/`veo-*` ⇒ `gemini`;
  `eleven_*`/`eleven-*` ⇒ `elevenlabs`. (Retired `dall-e*`/`sora*` ids still route to
  `openai` only to return a clear unsupported/removal error.)
- Else `$MEDIA_PROVIDER` / `$MEDIA_BACKEND`, else `mock`.

Default models (override with the env vars below or `--model`):

| provider | default image | default video | default audio |
|---|---|---|---|
| `volc` | `doubao-seedream-4-5-251128` | `doubao-seedance-2-0-260128` | — |
| `openai` | `gpt-image-2` | — (Sora retired) | — |
| `gemini` | `gemini-3.1-flash-image` | `veo-3.1-generate-preview` | `gemini-2.5-flash-preview-tts` (speech) |
| `elevenlabs` | — | — | `eleven_multilingual_v2` (speech) / `eleven_v3` (dialogue) / `music_v1` (music) / `eleven_text_to_sound_v2` (sound) |

`media-ai capabilities` discovery lists these models: **openai** — `gpt-image-2`,
`gpt-image-1.5`, `gpt-image-1`, `gpt-image-1-mini` (image only; Sora & DALL·E retired);
**gemini** — `gemini-3.1-flash-image`, `gemini-3.1-flash-lite-image`,
`gemini-3-pro-image`, `gemini-2.5-flash-image`, `veo-3.1-generate-preview`,
`veo-3.1-fast-generate-preview`, `veo-3.1-lite-generate-preview`, plus TTS
`gemini-2.5-flash-preview-tts`, `gemini-2.5-pro-preview-tts`,
`gemini-3.1-flash-tts-preview`; **elevenlabs** — `eleven_multilingual_v2`,
`eleven_turbo_v2_5`, `eleven_flash_v2_5`, `eleven_v3`; **volc** — the two configured
Ark models (image + video). Some deprecated models still resolve via `--model` but are
omitted from discovery.

## Endpoint / base URL and tuning env vars

Base URL precedence: profile `base_url` → env override → hardcoded default.

| provider | base-url env (default) | model overrides | poll tuning |
|---|---|---|---|
| `volc` | `ARK_BASE_URL` (`https://ark.cn-beijing.volces.com/api/v3`) | `ARK_IMAGE_MODEL`, `ARK_VIDEO_MODEL`, `ARK_IMAGE_SIZE` | `ARK_POLL_INTERVAL` (5s), `ARK_POLL_TIMEOUT` (900s) |
| `openai` | `OPENAI_BASE_URL` (`https://api.openai.com/v1`) | `OPENAI_IMAGE_MODEL` | — (image only) |
| `gemini` | `GEMINI_BASE_URL` (`https://generativelanguage.googleapis.com/v1beta`) | `GEMINI_IMAGE_MODEL`, `GEMINI_VIDEO_MODEL`, `GEMINI_TTS_MODEL` | `GEMINI_POLL_INTERVAL` (10s), `GEMINI_POLL_TIMEOUT` (1200s), `GEMINI_INLINE_MAX_BYTES` (12 MB) |
| `elevenlabs` | `ELEVENLABS_BASE_URL` (`https://api.elevenlabs.io/v1`; regional residency endpoints too) | `ELEVENLABS_MODEL`, `ELEVENLABS_DIALOGUE_MODEL`, `ELEVENLABS_MUSIC_MODEL`, `ELEVENLABS_SOUND_MODEL`, `ELEVENLABS_VOICE_ID` | — (audio is synchronous) |

Per-modality capability detail lives in the `media-ai-image`, `media-ai-video`, and
audio (`media-ai-speech` / `media-ai-music` / `media-ai-sound`) skills; human setup
notes are in `../../../docs/PROVIDERS.md`. Adding a new provider needs no core changes
— see `../../../docs/EXTENDING.md`.
