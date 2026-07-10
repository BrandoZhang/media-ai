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
  (<https://www.volcengine.com/docs/82379/1330310>).
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

| | volc image | openai gpt-image-2 | gemini native | imagen-4 | volc/openai/gemini video |
|---|---|---|---|---|---|
| edit / references | ✓ | ✓ (+mask) | ✓ | ✗ | i2v/first-frame |
| geometry | px or tier | px (arbitrary) | aspect-ratio | aspect-ratio | ratio+resolution |
| batch | sequential | n≤10 | candidateCount | sampleCount≤4 | 1 |
| seed | ✓ | ✗ | ✗ | ✓ | ✓ |
| negative prompt | ✗ | ✗ | ✗ | ✓ | ✓ (veo) |
| async | ✗ | ✗ | ✗ | ✗ | ✓ |
