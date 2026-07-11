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
- **Errors are classified by Ark error `code`** (`media_ai/providers/_volc_errors.py`):
  content-safety codes (input *or* output `SensitiveContentDetected`/`RiskDetection`)
  → `safety` (exit 8); `ModelNotOpen`/`InvalidEndpointOrModel` → `not_found` (exit 9,
  with an "enable it in the console" hint); `AccountOverdueError`/overdue → `auth`;
  `QuotaExceeded`/`SetLimitExceeded` → `rate_limit` but **not retryable**; transient
  RPM/TPM/`ServerOverloaded` → retryable `rate_limit`. The Ark `code` + `request_id`
  are preserved in the error `details`. The same classifier maps **async video task
  failures** (whose reason is in the task result, not an HTTP status), so an
  output-safety block on a finished-but-rejected video still exits 8.
- Extra env: `ARK_BASE_URL`, `ARK_IMAGE_SIZE`, `ARK_POLL_INTERVAL`, `ARK_POLL_TIMEOUT`.

## openai — GPT Image / DALL·E / Sora

```bash
export MEDIA_PROVIDER=openai OPENAI_API_KEY=…
export OPENAI_ORG=… OPENAI_PROJECT=…     # optional scoping headers
```

- **Image** is **synchronous**; GPT Image returns **base64** (no hosted URL).
  `POST /v1/images/generations` and `POST /v1/images/edits` (multipart, up to 16
  references + an alpha `--mask`).
- Models: `gpt-image-2` (arbitrary sizes ÷16, ratio 1:3–3:1, ≤3840×2160; **no
  transparent background**), `gpt-image-1` / `gpt-image-1-mini` (fixed sizes,
  transparency ok), `dall-e-3` (n=1, quality standard/hd, `--option style=…`),
  `dall-e-2` (edits/variations). `--quality`, `--background`, `--format`,
  `--option moderation=… output_compression=… input_fidelity=…`.
- **Video (Sora)** — `sora-2` / `sora-2-pro`, async job. **Experimental** (see
  LIMITATIONS.md). `--duration {4,8,12}`, `--option size=…`.
- GPT Image may require org verification in the OpenAI dashboard.
- Extra env: `OPENAI_BASE_URL`, `OPENAI_IMAGE_MODEL`, `OPENAI_VIDEO_MODEL`.

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
- A 200-OK response with **no image** (Gemini's silent safety drop) is surfaced as
  a `safety` error (exit 8), not an empty file.
- Extra env: `GEMINI_BASE_URL`, `GEMINI_IMAGE_MODEL`, `GEMINI_VIDEO_MODEL`,
  `GEMINI_POLL_INTERVAL`, `GEMINI_POLL_TIMEOUT`, `GEMINI_INLINE_MAX_BYTES`.
- Every image/video path (incl. local-file inputs) was exercised against the live API
  — see [GEMINI_LIVE_TEST.md](GEMINI_LIVE_TEST.md) for the coverage matrix and findings.

## elevenlabs — text-to-speech + dialogue

```bash
export MEDIA_PROVIDER=elevenlabs ELEVENLABS_API_KEY=…   # or ELEVEN_API_KEY
```

- **Audio** is **synchronous** (`audio` modality, `media-ai speech`). Auth is the
  `xi-api-key` header.
- **`speech generate`** — single voice via `POST /v1/text-to-speech/{voice_id}`
  (raw audio bytes). `--voice`, `--output-format` (e.g. `mp3_44100_128`; full codec
  enum in `capabilities`), `--language-code`, `--seed`. Voice knobs go through
  `--option stability=… similarity_boost=… style=… speed=… use_speaker_boost=…`
  (plus `previous_text`/`next_text`/`apply_text_normalization`/`enable_logging`/
  `optimize_streaming_latency`).
- **`speech dialogue`** — multi-voice via `POST /v1/text-to-dialogue`; turns come
  from repeated `--turn VOICE_ID TEXT` and/or a `--script file.json`
  (`[{"voice_id":…,"text":…}]`). ≤10 unique voices; keep total text ≲2000 chars.
- **`--timestamps true`** switches either op to the `/with-timestamps` endpoint and
  writes a `<output>.timestamps.json` sidecar (per-character alignment; dialogue
  also gets `voice_segments`) as a second artifact.
- Models: `eleven_multilingual_v2` (TTS default), `eleven_turbo_v2_5`,
  `eleven_flash_v2_5`, `eleven_v3` (dialogue default). `mp3_44100_192` needs Creator
  tier+; PCM/WAV 44.1 kHz needs Pro tier+.
- Base URL is configurable (`ELEVENLABS_BASE_URL` or a profile `base_url`) to target a
  regional residency endpoint (`api.us`/`api.eu.residency`/`api.in.residency`/
  `api.sg.residency`.elevenlabs.io). Extra env: `ELEVENLABS_MODEL`,
  `ELEVENLABS_DIALOGUE_MODEL`, `ELEVENLABS_VOICE_ID`.

## Capability matrix (summary)

| | volc image | openai gpt-image-2 | gemini native | volc/openai/gemini video |
|---|---|---|---|---|
| edit / references | ✓ | ✓ (+mask) | ✓ | i2v/first-frame |
| geometry | px or tier | px (arbitrary) | aspect-ratio | ratio+resolution |
| batch | sequential | n≤10 | candidateCount | 1 |
| seed | ✓ | ✗ | ✗ | ✓ |
| negative prompt | ✗ | ✗ | ✗ | ✓ (veo) |
| async | ✗ | ✗ | ✗ | ✓ |
