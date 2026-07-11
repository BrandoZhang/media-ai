# Providers

Run `media-ai capabilities [--provider P] [--model M]` for the authoritative,
machine-readable descriptor. This page is the human summary + setup.

## mock (default, offline)

No credentials. Deterministic given `(prompt, seed)`. Draws Pillow placeholder
images and ffmpeg clips; synthesizes token counts with the same formulas the real
APIs document so the cost path is exercised offline. Supports a stateless fake
async job so the `job query` path is testable without a network.

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

## openai — GPT Image / DALL·E

```bash
export MEDIA_PROVIDER=openai OPENAI_API_KEY=…
export OPENAI_ORG=… OPENAI_PROJECT=…     # optional scoping headers
```

- **Image-only** and **synchronous**; GPT Image returns **base64** (no hosted URL).
  `POST /v1/images/generations` and `POST /v1/images/edits` (multipart, up to 16
  references + an alpha `--mask`). OpenAI no longer exposes a video API, so
  `video generate --provider openai` fails pre-flight with `unsupported` (exit 3).
- Models: `gpt-image-2` (arbitrary sizes — both edges ÷16, max edge 3840px, edge
  ratio ≤3:1, total pixels 655360–8294400; **no transparent background**),
  `gpt-image-1.5` / `gpt-image-1` / `gpt-image-1-mini` (fixed sizes
  1024×1024 / 1536×1024 / 1024×1536, transparency ok), `dall-e-3` (n=1,
  `--option style=…`), `dall-e-2` (edits/variations). `--quality {low,medium,high,auto}`,
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

## gemini — Gemini native image / Imagen / Veo

```bash
export MEDIA_PROVIDER=gemini GEMINI_API_KEY=…      # or GOOGLE_API_KEY
```

- **Native image** (`gemini-2.5-flash-image`, `gemini-3-pro-image`) via
  `generateContent` — conversational generate + edit + multi-image compose;
  base64 inline out; geometry by `--aspect-ratio` (+ `--resolution 512|1K|2K|4K`
  on the pro model). `--count` = candidateCount.
- **Imagen** (`imagen-4.0-generate/-ultra/-fast-001`) via `:predict` — dedicated
  text→image; `--seed`, `--negative-prompt`, `--option person_generation=…`.
- **Video (Veo)** — `veo-3.1-generate-preview`, `veo-3.0-generate-001`,
  `veo-2.0-generate-001` via `:predictLongRunning` → poll operation →
  **download the file URI with the API key**. First-frame (all) + last-frame /
  reference images (3.1). Audio is native on Veo 3.x (`--audio` is unreliable on
  the Developer API — see LIMITATIONS.md); Veo 2 is silent. Jobs **cannot be
  cancelled** on the Developer API (`job cancel` → exit 3).
- **SynthID watermarking is unconditional** on this API (image + video).
- A 200-OK response with **no image** (Gemini's silent safety drop) is surfaced as
  a `safety` error (exit 8), not an empty file.
- Extra env: `GEMINI_BASE_URL`, `GEMINI_IMAGE_MODEL`, `GEMINI_VIDEO_MODEL`,
  `GEMINI_POLL_INTERVAL`, `GEMINI_POLL_TIMEOUT`.

## Capability matrix (summary)

| | volc image | openai gpt-image-2 | gemini native | imagen-4 | volc/gemini video |
|---|---|---|---|---|---|
| edit / references | ✓ | ✓ (+mask) | ✓ | ✗ | i2v/first-frame |
| geometry | px or tier | px (arbitrary) | aspect-ratio | aspect-ratio | ratio+resolution |
| batch | sequential | n≤10 | candidateCount | sampleCount≤4 | 1 |
| seed | ✓ | ✗ | ✗ | ✓ | ✓ |
| negative prompt | ✗ | ✗ | ✗ | ✓ | ✓ (veo) |
| async | ✗ | ✗ | ✗ | ✗ | ✓ |
