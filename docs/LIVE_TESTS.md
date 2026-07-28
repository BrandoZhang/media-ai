# Live API test log

One record of every **real-API** validation run, across all providers. The offline
suite (`uv run pytest -q`) never touches the network; this doc captures what was
exercised against live endpoints with a real key, to prove the request/response
shapes match the wire format and to surface real bugs.

> **Keys.** Every run below used temporary, low-credit keys that were revoked
> afterwards. All calls were real and billed. Never commit a real key — see
> [CREDENTIALS.md](CREDENTIALS.md).

## Summary matrix

| Provider | Paths exercised live | Last run | Result |
|---|---|---|---|
| **mock** | n/a — offline by design (no network) | — | — |
| **gemini** | image (text/edit/compose/geometry/grounding/thinking), Veo video (t2v, first/last-frame, reference-images, extension, async job), TTS (single + multi-speaker) | **2026-07-28** | ✅ all paths; 4 bugs found+fixed |
| **openai** | image `generate` (gpt-image-2), base64→PNG, usage/meta echo | 2026-07-12 | ✅ (not re-run post-refactor) |
| **elevenlabs** | `speech generate` (eleven_multilingual_v2) | 2026-07-12 | ✅ (not re-run post-refactor) |
| **volc** (Ark) | — not yet live-tested (no key available) | — | ⏳ |

Only **gemini** has been re-run since the binding refactor. The openai and elevenlabs
rows predate it: their adapters are covered offline and by `tests/test_contract.py`,
but nobody has watched them answer a real request through the new addressing, so treat
those two `verified` dates as what they say — a real run, on the *old* call path.

Pre-flight validation was also confirmed live: an unsupported request (e.g.
`gemini-3.1-flash-lite-image --resolution 4K`) fails with **exit 3** and a
machine-readable reason **before any network call**.

---

## gemini — Gemini native image (Nano Banana) / Veo / TTS

A manual, end-to-end run against `generativelanguage.googleapis.com/v1beta`,
first on 2026-07-11 (image + video + TTS) and re-confirmed 2026-07-12
(image + TTS smoke). Focus: every generation path once, with special attention to
**local files as input**. Model ids were first confirmed with a **free**
`GET /v1beta/models` call.

### Image generation

All paths ran on `generateContent`. "Local file" = a real local PNG passed as input.

| Path | Model | Input | Result |
|---|---|---|---|
| text → image | `gemini-3.1-flash-image` | prompt | ✅ (1024×1024 PNG re-confirmed 2026-07-12) |
| geometry (`--aspect-ratio 16:9 --resolution 2K`) | `gemini-3.1-flash-image` | prompt | ✅ (~4.3 MB 2K) |
| **edit** (1 reference) | `gemini-3.1-flash-image` | **local PNG** | ✅ |
| **compose** (2 references) | `gemini-3.1-flash-image` | **2 local PNGs** | ✅ |
| `--option thinking_level=high` | `gemini-3.1-flash-image` | prompt | ✅ accepted |
| `--option grounding=true` | `gemini-3.1-flash-image` | prompt | ✅ accepted |
| text → image | `gemini-3.1-flash-lite-image` / `gemini-3-pro-image` / `gemini-2.5-flash-image` | prompt | ✅ (2.5 returns PNG) |
| forced bad request (`--resolution 8K --on-unsupported ignore`) | `gemini-3.1-flash-image` | prompt | ✅ mapped to `validation` / `INVALID_ARGUMENT` |

Output-format verification (same prompt, three extensions): `.png`→PNG, `.jpg`→JPEG,
`.webp`→WEBP, each with a matching reported mime.

### Video generation (Veo)

All paths ran on `predictLongRunning`, submitted async (`--wait false`) and finalized
via `media-ai job query --output`. Every finished job downloaded a real MP4.

| Path | Model | Input | Result |
|---|---|---|---|
| text → video | `veo-3.1-lite-generate-preview` | prompt | ✅ |
| **image → video** (first frame) | `veo-3.1-lite-generate-preview` | **local PNG** | ✅ |
| **reference images** (asset) | `veo-3.1-fast-generate-preview` | **2 local PNGs** | ✅ |
| **first + last frame** | `veo-3.1-fast-generate-preview` | **2 local PNGs** | ✅ |
| extension via local file | `veo-3.1-fast-generate-preview` | local MP4 | ❌ rejected by API → now a clear client error (bug #2) |
| extension via **URI** | `veo-3.1-fast-generate-preview` | prior clip's `video.uri` | ✅ (after fix) |

Durations/resolutions used: 720p; 4s for first-frame/text, 8s where the API requires
it (reference images, extension).

### TTS (speech)

Both paths worked on the first attempt; **no adapter changes were needed** — the
offline `FakeClient` request/response shapes matched the live wire format exactly.

| Path | Model | Voice(s) | Result |
|---|---|---|---|
| text → speech (single) | `gemini-2.5-flash-preview-tts` | `Kore` | ✅ 24 kHz mono WAV (re-confirmed 2026-07-12) |
| multi-speaker dialogue (cast + `--instruction`) | `gemini-2.5-flash-preview-tts` | `Kore` + `Puck` | ✅ 24 kHz mono WAV |

`responseModalities` **must be `["AUDIO"]` only** — adding `"TEXT"` (as the image path
does) makes TTS models reject the request. The response carries base64 **headerless
PCM** (`audio/L16;codec=pcm;rate=24000`), which the adapter wraps into a WAV (rate
parsed from the mimeType).

### Local-file input support (headline)

| Case | Local file supported? | Notes |
|---|---|---|
| Image edit / compose (`--reference`) | ✅ | inlined as base64 |
| Veo first/last frame, reference images | ✅ | inlined as base64 |
| **Veo extension (`--reference-video`)** | ❌ **by API design** | needs a **URI** to a prior Veo clip; a local path returns a clear `validation` error |
| Large image ref (> inline cap) for `generateContent` | ✅ | auto-uploaded via the **Files API**, referenced by `fileData.fileUri` (verified with a 23 MB PNG) |
| Large image input (> ~20 MB) for Veo | ❌ | Veo rejects file URIs for images; inline-only → clear `validation` error |
| TTS input | text-only | no file field; style/tone/pace go in the prompt text / `--instruction` |

### Wire-format findings (raw probes)

- Image `inlineData.mimeType` from `gemini-3.1-flash-image` is **`image/jpeg`** by
  default (2.5 returns PNG). This drove bug #1.
- Veo image inputs are `{"bytesBase64Encoded": …, "mimeType": …}`. Veo **extension**
  `video` requires `{"uri": …}` — `inlineData` and `bytesBase64Encoded` are both
  rejected (`400`).
- Errors use `{"error": {"code","message","status"}}`; the adapter's `_error` maps
  the canonical `status` correctly (verified with a live `INVALID_ARGUMENT`).
- Image `usageMetadata` = `{promptTokenCount, candidatesTokenCount, totalTokenCount,
  promptTokensDetails, candidatesTokensDetails, serviceTier}`; the adapter records
  `candidatesTokenCount`/`totalTokenCount` and returns the full block. Veo operations
  carry **no** usage/duration (just `generatedSamples[].video.uri`) — see bug #3.
- The response also carries `modelVersion` (→ `result.model`), `responseId`
  (→ `meta.response_id`), interleaved `text` parts (→ `meta.text`), and
  `groundingMetadata` on grounded requests (→ `meta.grounding`).

### Re-verified 2026-07-28 (post-refactor)

The whole binding path was re-run against the live API after the binding refactor,
through the new addressing (`--binding gemini/…`) and a `cred://` account:

| Path | Scene | Result |
|---|---|---|
| text → image | `image.text_to_image` | ✅ 1024×1024 PNG for `--aspect-ratio 1:1 --resolution 1K` |
| image → image | `image.image_to_image` | ✅ via `image edit --reference`; same geometry preserved |
| text → speech | `speech.text_to_speech` | ✅ 24 kHz mono WAV |
| dialogue | `speech.dialogue` | ✅ two voices + `--instruction` |
| text → video | `video.text_to_video` | ✅ async submit → `poll` string run **verbatim** → 1280×720, 4.00 s, h264 + AAC |
| credential probe | — | ✅ `--verify` reports `ok` (`GET /models`) |

Confirmed by that run, and worth keeping:

- **`meta.scene` is absent on `job query`, `meta.binding` is present.** Exactly as
  designed: the process that finalizes a job never saw the inputs that implied a scene.
  The ledger line lands under `by_scene["?"]` with the cost still attributed to the
  right binding.
- **The `poll` string is runnable as printed** — copied out of the JSON and executed
  unchanged, it polled and finalized the download.
- **Pre-flight refusals never reach the network**: an undeclared voice, a duration
  outside `[4, 6, 8]`, and pixel `--size` on a ratio-only binding all fail at exit 3.

### Bugs found and fixed

0. **Pixel geometry was unvalidated on the video path** (found 2026-07-28). The video
   branch of `_check_geometry` returned before comparing the request's geometry *form*
   against the binding's declared `mode`, so `--size 1280x720` on a ratio-only video
   binding validated clean, submitted, and came back as a **billed job at the
   provider's default geometry** — different output than was asked for, reported as
   success. Both real video bindings are ratio-only, so both were affected. Fixed by
   hoisting the mode check above the image/video split; `tests/test_contract.py` now
   asserts it for every binding that declares one form.
1. **Image output mime/format mismatch.** Gemini 3.x returns JPEG, but the adapter
   wrote raw bytes to the caller's path and hard-coded `image/png`. Fixed:
   `pillow.save_image_bytes` now writes the format the output extension asks for
   (transcoding when the model's format differs) and reports the true mime; native
   `output_formats` widened to `png/jpeg/webp`. Re-verified live.
2. **Veo extension used inline video.** The API rejects inline video for extension.
   Fixed: `--reference-video` now requires a **URI**; a local path is refused with a
   clear `validation` error. Re-verified by extending a freshly generated clip.
3. **Video usage recorded no `seconds`.** Veo returns no duration, so the ledger
   counted 0 for every Veo run. Fixed: bill by the **true output length**, probed from
   the downloaded clip with ffmpeg (also captures an extension's combined length).
   Verified: 4 s / 8 s clips probe to 4.00 s / 8.00 s, the extension to 11.01 s.

---

## openai — GPT Image

Smoke-tested 2026-07-12 against `api.openai.com/v1`.

| Path | Model | Result |
|---|---|---|
| text → image (`image generate`) | `gpt-image-2` | ✅ 1254×1254 PNG |

- GPT Image returns **base64 only**; the adapter decodes and transcodes to the output
  extension's format (`pillow.save_image_bytes`), reporting the true mime.
- The response echoes the settings the model actually used — the result `meta` carried
  `size: "1254x1254"` (from `size: "auto"`), `output_format: "png"`, `quality: "low"`,
  `background: "opaque"`, `created` — and `usage` carried
  `{input_tokens, output_tokens, total_tokens, *_tokens_details}` (token-billed).

**Not yet exercised live:** `image edit` (multipart references), moderation/safety
error mapping.

---

## elevenlabs — text-to-speech + dialogue + music + sound

Smoke-tested 2026-07-12 against `api.elevenlabs.io/v1` (auth via the `xi-api-key`
header).

| Path | Model | Result |
|---|---|---|
| text → speech (`speech generate`) | `eleven_multilingual_v2` | ✅ MP3 (`audio/mpeg`), `usage.characters` recorded |

**Not yet exercised live:** `speech dialogue` (`/text-to-dialogue`), `--timestamps`
sidecars, `music generate`/`plan`, `sound generate`.

---

## volc — Volcengine Ark

**Not yet live-tested** (no Ark key was available for these runs). The adapter is
covered by mocked-API tests (`tests/test_volc_api.py`, request bodies + response
parse + error mapping) and the parametrized contract suite. Ark model ids are
account-specific and must be enabled in the console before a live run — see
[BINDINGS.md](BINDINGS.md#volc--volcengine-ark-doubao-seedream--seedance).

---

## Reproduce

```bash
# Load a key for the process env (the CLI reads the process environment):
media-ai bindings list        # confirm the binding under test is configured

# gemini — cheap image + TTS
uv run media-ai image generate  --binding gemini/nano-banana-2 --prompt "a green leaf on white" --output /tmp/leaf.png
uv run media-ai speech generate --binding gemini/gemini-tts --text "hello there" --voice Kore --output /tmp/g.wav
# gemini — Veo video (async submit → poll; billed + slow)
uv run media-ai video generate  --binding gemini/veo-3.1 --wait false \
  --prompt "a kitten yawns" --first-frame /path/local.png --output /tmp/v.mp4 --duration 4 --resolution 720p
uv run media-ai job query       --binding gemini/veo-3.1 --id "<operation-id>" --output /tmp/v.mp4

# openai — image
uv run media-ai image generate  --binding openai/gpt-image-2 --prompt "a small red cube on white" --output /tmp/o.png

# elevenlabs — speech
uv run media-ai speech generate --binding elevenlabs/eleven-multilingual-v2 --text "hello there" --output /tmp/e.mp3
```

## Gated live pytest

A smaller smoke subset runs automatically when opted in:

```bash
MEDIA_LIVE_TESTS=1 uv run pytest -q tests/test_live.py     # + MEDIA_LIVE_VIDEO=1 for Veo
```

These self-skip unless `MEDIA_LIVE_TESTS=1` and the relevant provider key are set, so
the default suite stays offline and green.
