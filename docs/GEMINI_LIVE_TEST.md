# Gemini backend — live API test report

A record of a manual, end-to-end run of the `gemini` provider against the real
**Gemini Developer API** (`generativelanguage.googleapis.com/v1beta`). The offline
suite (`uv run pytest -q`) never touches the network; this report covers what was
exercised against live endpoints, with a real API key, to validate the request
shapes and surface real bugs.

- **Date:** 2026-07-11
- **Key:** a temporary, low-credit key (since revoked); all calls were real and billed.
- **Focus:** every image and video generation path once, with special attention to
  **local files as input**.
- **Outcome:** all paths exercised; **2 real bugs found and fixed** (image
  output mime/format; Veo extension input). Fixes were re-verified live.

## Method

- Model ids were first confirmed with a **free** `GET /v1beta/models` call (no billing).
- Image paths were driven through `media-ai image …`; each output was sniffed with
  `file(1)` to confirm the bytes match the reported mime and extension.
- Video paths were submitted with `--wait false` (which also exercises the async job
  path) and finalized with `media-ai job query --output`; each result was sniffed to
  confirm a real MP4.
- A few raw `curl`/`urllib` probes were used to pin down exact wire shapes (below).

## Models available to the key (from `ListModels`)

| Family | Ids returned | Methods |
|---|---|---|
| Nano Banana (image) | `gemini-3.1-flash-image`(+`-preview`), `gemini-3.1-flash-lite-image`, `gemini-3-pro-image`(+`-preview`), `gemini-2.5-flash-image` | `generateContent` |
| Veo (video) | `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`, `veo-3.1-lite-generate-preview` | `predictLongRunning` |
| Imagen | `imagen-4.0-generate-001`, `-ultra-`, `-fast-` | `predict` (intentionally **not** wired — dropped) |

All model ids used by the adapter's discovery list are real and callable.

## Image generation — coverage

All paths ran on `generateContent`. "Local file" = a real local PNG passed as input.

| Path | Model | Input | Result |
|---|---|---|---|
| text → image | `gemini-3.1-flash-image` | prompt | ✅ |
| geometry (`--aspect-ratio 16:9 --resolution 2K`) | `gemini-3.1-flash-image` | prompt | ✅ (~4.3 MB 2K) |
| **edit** (1 reference) | `gemini-3.1-flash-image` | **local PNG** | ✅ |
| **compose** (2 references) | `gemini-3.1-flash-image` | **2 local PNGs** | ✅ |
| `--option thinking_level=high` | `gemini-3.1-flash-image` | prompt | ✅ accepted |
| `--option grounding=true` | `gemini-3.1-flash-image` | prompt | ✅ accepted |
| text → image | `gemini-3.1-flash-lite-image` | prompt | ✅ |
| text → image | `gemini-3-pro-image` | prompt | ✅ |
| text → image | `gemini-2.5-flash-image` | prompt | ✅ (returns PNG) |
| forced bad request (`--resolution 8K --on-unsupported ignore`) | `gemini-3.1-flash-image` | prompt | ✅ mapped to `validation` / `INVALID_ARGUMENT` |

Output-format verification (same prompt, three extensions):

| `--output` | Reported mime | Actual bytes |
|---|---|---|
| `leaf.png` | `image/png` | PNG |
| `leaf.jpg` | `image/jpeg` | JPEG |
| `leaf.webp` | `image/webp` | WEBP |

## Video generation — coverage

All paths ran on `predictLongRunning`, submitted async and finalized via `job query`.
Every finished job downloaded a real MP4.

| Path | Model | Input | Result |
|---|---|---|---|
| text → video | `veo-3.1-lite-generate-preview` | prompt | ✅ |
| **image → video** (first frame) | `veo-3.1-lite-generate-preview` | **local PNG** | ✅ |
| **reference images** (asset) | `veo-3.1-fast-generate-preview` | **2 local PNGs** | ✅ |
| **first + last frame** | `veo-3.1-fast-generate-preview` | **2 local PNGs** | ✅ |
| extension via local file | `veo-3.1-fast-generate-preview` | local MP4 | ❌ rejected by API → now a clear client error (see bug #2) |
| extension via **URI** | `veo-3.1-fast-generate-preview` | prior clip's `video.uri` | ✅ (after fix) |
| async submit + `job query` poll + download | all of the above | — | ✅ |

Durations/resolutions used: 720p; 4s for first-frame/text, 8s where the API requires
it (reference images, extension).

## Local-file input support (headline)

| Case | Local file supported? | Notes |
|---|---|---|
| Image edit / compose (`--reference`) | ✅ | inlined as base64 |
| Veo first frame (`--first-frame`) | ✅ | inlined as base64 |
| Veo last frame (`--last-frame`) | ✅ | inlined as base64 |
| Veo reference images (`--reference-image`) | ✅ | inlined as base64 |
| **Veo extension (`--reference-video`)** | ❌ **by API design** | needs a **URI** to a prior Veo clip; inline video is refused. A local path now returns a clear `validation` error. |
| Large image ref (> inline cap) for `generateContent` | ✅ | auto-uploaded via the **Files API** and referenced by `fileData.fileUri` (verified live with a 23 MB PNG). |
| Large image input (> ~20 MB) for Veo | ❌ | Veo rejects file URIs for images (`` `uri`/`fileData` isn't supported ``); inline-only, so a clear `validation` error. |

## Wire-format findings (raw probes)

- Image `inlineData.mimeType` from `gemini-3.1-flash-image` is **`image/jpeg`** by
  default (2.5 returns PNG). This drove bug #1.
- Veo image inputs (`image`/`lastFrame`/`referenceImages`) are accepted as
  `{"bytesBase64Encoded": …, "mimeType": …}`.
- Veo **extension** `video` field:
  - `{"inlineData": …}` → `400` "`inlineData` isn't supported by this model."
  - `{"bytesBase64Encoded": …}` → `400` "Video URI not found in the request."
  - `{"uri": …}` → accepted (a malformed URI returns "Could not parse the file name",
    confirming the field shape). **Extension requires a URI.**
- Errors use the standard `{"error": {"code", "message", "status"}}` envelope; the
  adapter's `_error` maps `status` strings correctly (verified with a live
  `INVALID_ARGUMENT`).
- **Files API** resumable upload works (start → `X-Goog-Upload-URL` → upload+finalize
  → file resource `state: ACTIVE`). `generateContent` accepts the result as
  `{"fileData": {"mimeType", "fileUri"}}`. Veo `predictLongRunning` does **not** accept
  `uri` or `fileData` for image inputs — both return `400 "isn't supported by this
  model"`.
- **Usage.** Image `usageMetadata` is `{promptTokenCount, candidatesTokenCount,
  totalTokenCount, promptTokensDetails, candidatesTokensDetails, serviceTier}`; the
  adapter records `candidatesTokenCount`/`totalTokenCount` (correct) and returns the
  full block in the result `usage`. Veo operations carry **no** usage/duration field at
  all (just `generatedSamples[].video.uri`) — see bug #3.
- **Other image-response fields now surfaced.** The response also carries
  `modelVersion` (the model that actually served the request — now reported as
  `result.model` and in the usage record), `responseId` (→ `meta.response_id`, for
  support tickets), interleaved `text` parts (→ `meta.text`; thought parts excluded),
  and, on grounded requests, `candidates[].groundingMetadata` (→ `meta.grounding`,
  which holds the `searchEntryPoint` search-suggestion HTML the grounding terms require
  displaying). The output image part also carries a `thoughtSignature` used only for
  multi-turn continuation (not supported here, so ignored).

## Bugs found and fixed

1. **Image output mime/format mismatch.** Gemini 3.x image models return JPEG, but the
   adapter wrote the raw bytes to the caller's path and hard-coded `image/png` — so
   `--output x.png` held JPEG content and the reported mime lied. Fixed: the response
   `mimeType` is now carried through, and `pillow.save_image_bytes` writes the format
   the output extension asks for (transcoding only when the model's format differs),
   reporting the true mime. Native `output_formats` widened to `png/jpeg/webp`.
   *Re-verified live: `.png`→PNG, `.jpg`→JPEG, `.webp`→WEBP, mime matching.*

2. **Veo extension used inline video.** The API rejects inline video for extension
   (`Video URI not found`; `inlineData` also refused). Fixed: `--reference-video` now
   takes a **URI** (remote ref); a local path is refused with a clear `validation`
   error. *Re-verified live by extending a freshly generated clip.*

3. **Video usage recorded no `seconds`.** Veo operations return no duration, and
   `_finalize_video` recorded none — so the ledger's `video_seconds` counted **0** for
   every Veo run (volc/openai/Sora all record it). Fixed: bill by the **true output
   length**, probed from the downloaded clip with ffmpeg (this captures an extension's
   combined length, which the request would undercount), falling back to the requested
   duration only if the probe can't read the file. *Verified against the real Veo
   outputs: 4 s / 8 s clips probe to 4.00 s / 8.00 s and the extension to 11.01 s; the
   image `usageMetadata` fields the adapter reads (`candidatesTokenCount`,
   `totalTokenCount`) match live responses.*

All fixes are covered by offline tests and reflected in `LIMITATIONS.md` /
`PROVIDERS.md`.

## Not covered / known gaps

- **Cost/latency measurement** — not recorded (functional validation only).
- **4K / 1080p video, `personGeneration`, `--seed` effect** — request shapes are
  built and validated, but not exercised for output quality here (720p only, to
  conserve credit).
- **Files API upload** — implemented and verified for `generateContent` image
  references (large local inputs auto-upload). Not applicable to Veo image inputs
  (API rejects file URIs). Video-to-image (a large local *video* as input to a 3.1
  Flash image request) is not wired up.
- **Batch API** image generation — not covered.
- **Broker/managed-credential path** — the live run used a direct API key; Files-API
  upload is only supported on the direct-key path.

## Reproduce

```bash
export GEMINI_API_KEY=…                 # from aistudio.google.com/apikey
# free: confirm model ids + connectivity
curl -s "https://generativelanguage.googleapis.com/v1beta/models" -H "x-goog-api-key: $GEMINI_API_KEY"
# cheap image checks
uv run media-ai image generate --provider gemini --model gemini-3.1-flash-image \
  --prompt "a green leaf on white" --output /tmp/leaf.png    # then repeat with .jpg / .webp
uv run media-ai image edit --provider gemini --model gemini-3.1-flash-image \
  --prompt "add a hat" --reference /path/to/local.png --output /tmp/edit.png
# video (async submit → poll); billed + slow
uv run media-ai video generate --provider gemini --wait false \
  --model veo-3.1-lite-generate-preview --prompt "a kitten yawns" \
  --first-frame /path/to/local.png --output /tmp/v.mp4 --duration 4 --resolution 720p
uv run media-ai job query --provider gemini --id "<operation-id>" --output /tmp/v.mp4
```

The gated live pytest path (`MEDIA_LIVE_TESTS=1 uv run pytest -q tests/test_live.py`,
plus `MEDIA_LIVE_VIDEO=1` for Veo) covers a smaller smoke subset automatically.
