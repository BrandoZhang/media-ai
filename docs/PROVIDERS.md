# Providers

Run `media-ai capabilities [--provider P] [--model M]` for the authoritative,
machine-readable descriptor. This page is the human summary + setup.

## mock (default, offline)

No credentials. Deterministic given `(prompt, seed)`. Draws Pillow placeholder
images and ffmpeg clips, and synthesizes placeholder speech/dialogue as stdlib
`wave` WAV tones (no ffmpeg needed, incl. a fake `--timestamps` sidecar);
synthesizes token/character counts with the same formulas the real APIs document
so the cost path is exercised offline. Supports a stateless fake async job so the
`job query` path is testable without a network.

## volc — Volcengine Ark (Doubao Seedream / Seedance)

```bash
export MEDIA_PROVIDER=volc ARK_API_KEY=…
export ARK_IMAGE_MODEL=doubao-seedream-4-5-251128    # optional defaults
export ARK_VIDEO_MODEL=doubao-seedance-2-0-260128
```

- **Image** (`/images/generations`, sync): text→image, multi-image reference,
  group output (`--count`). Geometry: pixel `WxH` (below 2560×1440 falls back to
  the `2K` preset) or a named tier. `--option watermark=…`.
- **Video** (async task create→poll→cancel): text / first-frame / first+last-frame
  / multimodal-reference (images+videos+audio). `--audio`, `--seed`,
  `--return-last-frame`, `--option camera_fixed=…`. A blocking `--wait true` cancels
  the billed task on SIGTERM/SIGINT/timeout so a killed call doesn't orphan a task.
- Model IDs are **account-specific** — enable them in the console
  (<https://www.volcengine.com/docs/82379/1330310>). Both plain model names
  (`doubao-seedance-2-0-260128`) and **custom endpoint IDs** (`ep-2026…`) work.
- **Endpoint IDs encode no modality**, so the modality comes from the *command*
  (`media-ai video generate …` ⇒ video), not the id — you don't need to set
  `ARK_VIDEO_MODEL` to match. For endpoint IDs, geometry is left to the Ark API to
  validate rather than pre-checked (pre-flight validation fails open, not closed),
  so a valid endpoint request is never blocked by a name-based guess. Pass
  `--provider volc` explicitly with an endpoint id (the id alone can't imply the
  provider). Discovery (`media-ai capabilities`) still classifies known model names
  by name; for a bare endpoint id it is best-effort.
- **Different endpoints on different accounts?** Bind each to its own key (and
  optional base URL / default model) with a **profile** and select it via
  `--provider-profile` — see [CREDENTIALS.md](CREDENTIALS.md#profiles-per-endpoint--per-tenant-credentials).
- **Errors are classified by Ark error `code`** (`src/media_ai/providers/_volc_errors.py`):
  content-safety codes (input *or* output `SensitiveContentDetected`/`RiskDetection`)
  → `safety` (exit 8); `ModelNotOpen`/`InvalidEndpointOrModel` → `not_found` (exit 9,
  with an "enable it in the console" hint); `AccountOverdueError`/overdue → `auth`;
  `QuotaExceeded`/`SetLimitExceeded` → `rate_limit` but **not retryable**; transient
  RPM/TPM/`ServerOverloaded` → retryable `rate_limit`. The Ark `code` + `request_id`
  are preserved in the error `details`. The same classifier maps **async video task
  failures** (whose reason is in the task result, not an HTTP status), so an
  output-safety block on a finished-but-rejected video still exits 8.
- Extra env: `ARK_BASE_URL`, `ARK_IMAGE_SIZE`, `ARK_POLL_INTERVAL`, `ARK_POLL_TIMEOUT`.

## openai — GPT Image

```bash
export MEDIA_PROVIDER=openai OPENAI_API_KEY=…
export OPENAI_ORG=… OPENAI_PROJECT=…     # optional scoping headers
```

- **Image-only** and **synchronous**; GPT Image returns **base64** (no hosted URL).
  `POST /v1/images/generations` and `POST /v1/images/edits` (multipart, up to 16
  references + an alpha `--mask`). OpenAI no longer exposes a video API, so
  `video generate --provider openai` fails pre-flight with `unsupported` (exit 3).
- **GPT-Image-only.** DALL·E is intentionally unsupported — the current Images API
  rejects its `response_format` parameter, and GPT Image supersedes it. A `dall-e-*`
  id still routes here so it returns a clear `unsupported` error (exit 3, "use a GPT
  Image model") rather than silently falling back to mock; `sora-*` likewise gets the
  provider's no-video `unsupported` error.
- Models: `gpt-image-2` (arbitrary sizes — both edges ÷16, max edge 3840px, edge
  ratio ≤3:1, total pixels 655360–8294400; **no transparent background**),
  `gpt-image-1.5` / `gpt-image-1` / `gpt-image-1-mini` (fixed sizes
  1024×1024 / 1536×1024 / 1024×1536, transparency ok). `--quality {low,medium,high,auto}`,
  `--background`, `--format {png,jpeg,webp}`, `--option moderation=… output_compression=…`.
- **`input_fidelity`** is a knob only on `gpt-image-1` / `gpt-image-1.5`
  (`--option input_fidelity=high`); `gpt-image-2` always processes inputs at high
  fidelity (the param is rejected and never sent) and the mini tier doesn't expose it.
- For `gpt-image-2`, a `--resolution 2K|4K` alongside `--aspect-ratio` selects a
  documented larger size (e.g. `16:9` + `4K` → `3840×2160`).
- **Moderation blocks** map to a `safety` error (exit 8) carrying the stable
  `error.code` and coarse `moderation_details` (stage + categories) in the error
  `details` for developer logs.
- GPT Image may require org verification in the OpenAI dashboard.
- Extra env: `OPENAI_BASE_URL`, `OPENAI_IMAGE_MODEL`.

## gemini — Gemini native image (Nano Banana) / Veo

```bash
export MEDIA_PROVIDER=gemini GEMINI_API_KEY=…      # or GOOGLE_API_KEY
```

- **Native image — Nano Banana** (`gemini-3.1-flash-image` (go-to),
  `gemini-3.1-flash-lite-image`, `gemini-3-pro-image`, legacy
  `gemini-2.5-flash-image`) via `generateContent` — conversational generate + edit
  + compose from up to 14 reference images; base64 inline out; geometry by
  `--aspect-ratio` (+ `--resolution 512|1K|2K|4K`, per-model). `--count` =
  candidateCount. Extras via `--option`: `grounding=true` (Google Search, Flash +
  Pro) and `thinking_level=high` (3.1 Flash). Sizes/ratios/refs differ per model —
  see `capabilities`. Large local references (past `GEMINI_INLINE_MAX_BYTES`, default
  12 MB) auto-upload via the **Files API** and are referenced by URI, so inputs above
  the ~20 MB inline cap work. Output is written in the format your `--output`
  extension asks for (models return JPEG by default). (`imagen-*` ids return a clear
  `unsupported` error — use a Nano Banana model.)
- **Video (Veo)** — `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`,
  `veo-3.1-lite-generate-preview` via `:predictLongRunning` → poll operation →
  **download the file URI with the API key**. First-frame (all) + last-frame,
  up to 3 `--reference-image`s (asset) — all accept **local files** — and video
  extension via `--reference-video`, which must be the **URI of a prior Veo clip**
  (3.1 non-Lite; a local file is refused since the API rejects inline video). `--seed`
  and `--resolution 720p|1080p|4k` supported. Audio is native on Veo 3.x (`--audio` is
  unreliable on the Developer API — see LIMITATIONS.md). Jobs **cannot be
  cancelled** on the Developer API (`job cancel` → exit 3). Deprecated `veo-2.0` /
  `veo-3.0` snapshots still resolve via `--model`.
- **SynthID watermarking is unconditional** on this API (image + video).
- **TTS** (`gemini-2.5-flash-preview-tts`, `gemini-2.5-pro-preview-tts`,
  `gemini-3.1-flash-tts-preview`) via `:generateContent` with
  `responseModalities:["AUDIO"]` — **synchronous**, returns headerless 24 kHz PCM which
  the adapter wraps into a WAV. Style/tone/accent/pace and inline `[whispers]`/`[laughs]`
  tags are directed **in the prompt text**; language is auto-detected; there are 30 named
  voices (see `capabilities`). `speech generate --voice Kore`; `speech dialogue`
  (≤2 speakers) takes a cast + turns and an optional global `--instruction`. Output is
  WAV only (no format/seed/timestamps knobs). A 200-OK with no audio (safety) → `safety`
  error (exit 8). Env: `GEMINI_TTS_MODEL`.
- A 200-OK response with **no image** (Gemini's silent safety drop) is surfaced as
  a `safety` error (exit 8), not an empty file.
- Extra env: `GEMINI_BASE_URL`, `GEMINI_IMAGE_MODEL`, `GEMINI_VIDEO_MODEL`,
  `GEMINI_TTS_MODEL`, `GEMINI_POLL_INTERVAL`, `GEMINI_POLL_TIMEOUT`, `GEMINI_INLINE_MAX_BYTES`.
- Every image/video path (incl. local-file inputs) and the TTS paths (single +
  multi-speaker) were exercised against the live API — see
  [LIVE_TESTS.md](LIVE_TESTS.md) for the cross-provider coverage matrix and findings.

## elevenlabs — text-to-speech + dialogue + music + sound effects

```bash
export MEDIA_PROVIDER=elevenlabs ELEVENLABS_API_KEY=…   # or ELEVEN_API_KEY
```

- **Audio** is **synchronous** (`audio` modality; `media-ai speech` / `music` / `sound`).
  Auth is the `xi-api-key` header.
- **`speech generate`** — single voice via `POST /v1/text-to-speech/{voice_id}`
  (raw audio bytes). `--voice`, `--output-format` (e.g. `mp3_44100_128`; full codec
  enum in `capabilities`), `--language-code`, `--seed`. Voice knobs go through
  `--option stability=… similarity_boost=… style=… speed=… use_speaker_boost=…`
  (plus `previous_text`/`next_text`/`apply_text_normalization`/`enable_logging`/
  `optimize_streaming_latency`).
- **`speech dialogue`** — multi-voice via `POST /v1/text-to-dialogue`. Define a cast
  with `--speaker NAME=VOICE` (repeatable), then the script with `--turn NAME TEXT`
  (repeatable) and/or a `--script file.json`. ≤10 unique voices; keep total text ≲2000
  chars. (No global `--instruction` — that's Gemini-only.)
- **`--timestamps true`** switches either op to the `/with-timestamps` endpoint and
  writes a `<output>.timestamps.json` sidecar (per-character alignment; dialogue
  also gets `voice_segments`) as a second artifact.
- **`music generate`** — compose a song via `POST /v1/music` from a `--prompt` **or** a
  `--plan` composition-plan JSON (exactly one). `--duration-ms` (prompt mode, 3s–600s),
  `--output-format` (incl. `auto`), `--seed` (plan mode only). `--option
  force_instrumental=… respect_sections_durations=… store_for_inpainting=… sign_with_c2pa=…`.
  `--detailed true` uses `POST /v1/music/detailed` (multipart) and writes a
  `<output>.metadata.json` sidecar with the model's composition plan + song metadata.
- **`music plan`** — `POST /v1/music/plan`: a **credit-free** helper that returns a
  composition plan (JSON) from a prompt; edit it and feed it back via `music generate --plan`.
- **`sound generate`** — text→sound effect via `POST /v1/sound-generation`. `--text`,
  `--duration-seconds` (0.5–30, optional), `--output-format`, `--option loop=… prompt_influence=…`.
- **`--timestamps true`** switches either **speech** op to the `/with-timestamps` endpoint and
  writes a `<output>.timestamps.json` sidecar (per-character alignment; dialogue
  also gets `voice_segments`) as a second artifact.
- Models: `eleven_multilingual_v2` (TTS default), `eleven_turbo_v2_5`,
  `eleven_flash_v2_5`, `eleven_v3` (dialogue default); `music_v1`/`music_v2` (music);
  `eleven_text_to_sound_v2` (sound). `mp3_44100_192` needs Creator tier+; PCM/WAV 44.1 kHz
  needs Pro tier+.
- Base URL is configurable (`ELEVENLABS_BASE_URL` or a profile `base_url`) to target a
  regional residency endpoint (`api.us`/`api.eu.residency`/`api.in.residency`/
  `api.sg.residency`.elevenlabs.io). Extra env: `ELEVENLABS_MODEL`,
  `ELEVENLABS_DIALOGUE_MODEL`, `ELEVENLABS_MUSIC_MODEL`, `ELEVENLABS_SOUND_MODEL`,
  `ELEVENLABS_VOICE_ID`.

## Capability matrix (summary)

| | volc image | openai gpt-image-2 | gemini native | volc/gemini video |
|---|---|---|---|---|
| edit / references | ✓ | ✓ (+mask) | ✓ | i2v/first-frame |
| geometry | px or tier | px (arbitrary) | aspect-ratio | ratio+resolution |
| batch | sequential | n≤10 | candidateCount | 1 |
| seed | ✓ | ✗ | ✗ | ✓ |
| negative prompt | ✗ | ✗ | ✗ | ✓ (veo) |
| async | ✗ | ✗ | ✗ | ✓ |
