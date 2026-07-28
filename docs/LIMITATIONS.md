# Unresolved provider-specific items

Open questions and deliberate scope boundaries. Items marked `[verify]` were not
fully confirmable against live provider docs at build time and should be re-checked
before relying on them in production; the adapters are written to fail safely
(deterministic errors) rather than guess.

## OpenAI

- **Image-only.** OpenAI no longer exposes a public video API, so this adapter
  drops video entirely; no OpenAI binding declares a video scene, so such a request fails pre-flight with a
  deterministic `unsupported` error (exit 3).
- **`input_fidelity`** is scoped to `gpt-image-1` / `gpt-image-1.5` only. Per the
  docs, `gpt-image-2` always processes inputs at high fidelity and rejects the
  parameter, so it is neither declared as an option nor forwarded for that model.
- **Streaming partial images** (`partial_images`) are intentionally not supported:
  the CLI's machine contract emits one final JSON object, so it takes the
  synchronous full-image path and does not write `<stem>_partial_N` artifacts.
- **gpt-image-2 sizes** are validated against the documented constraints (both
  edges ÷16, max edge 3840px, edge ratio ≤3:1, total pixels 655360–8294400) rather
  than a fixed enum; a `--resolution 2K|4K` + `--aspect-ratio` maps to a documented
  larger size.
- **DALL·E** returns a temporary URL by default; we force `response_format=b64_json`
  to unify on bytes. Flat-priced (no token `usage`).

## Gemini

- **`generateAudio` on the Developer API** is unreliable for Veo 3.x (audio is
  native/always-on; Veo 2 is silent). We forward `--audio` when set but do not
  depend on it being honored. `[verify]`
- **Veo cancellation** is unsupported on the Developer API — `media-ai job cancel` returns a
  deterministic `unsupported` error (exit 3).
- **SynthID watermarking is unconditional** (image + video); there is no flag to
  disable it, so `--option`/capabilities do not expose one.
- **Generated Veo files expire (~48h)** on Google's servers — download promptly
  (the CLI does so in `media-ai job query --output` / `--wait true`).
- **`imageSize` varies by model.** `gemini-3.1-flash-image` accepts 512px/1K/2K/4K;
  `gemini-3-pro-image` 1K/2K/4K; `gemini-3.1-flash-lite-image` and legacy
  `gemini-2.5-flash-image` are 1K only. Capabilities reflect this per model, and the
  extreme banner ratios (`1:4`/`4:1`/`1:8`/`8:1`) are 3.1 Flash-only.
- **Imagen was removed.** `imagen-*` model ids still route to this provider but
  return a deterministic `unsupported` error (exit 3) pointing at Nano Banana, rather
  than silently falling back to the mock provider.
- **Output format follows the file extension.** Gemini 3.x image models return
  **JPEG** by default (2.5 returns PNG); the adapter writes the format your `--output`
  extension asks for (`.png`/`.jpg`/`.webp`), transcoding when needed, and reports the
  matching mime. No re-encode happens when the model's format already matches.
- **Grounding / thinking (verified live).** `--option grounding=true` sends
  `tools:[{google_search:{}}]` (Flash + Pro) and `thinking_level=high` (3.1 Flash)
  sends `generationConfig.thinkingConfig.thinkingLevel="high"` — both accepted by
  `generateContent`. Google *Image* Search grounding is Interactions-API-only (its
  `search_types` field isn't part of `generateContent`), so it is **not** exposed here.
  A grounded response's `groundingMetadata` is returned under the result's
  `meta.grounding`; the Google Search grounding terms require **displaying its
  `searchEntryPoint` search suggestions** — the adapter surfaces it but can't enforce
  that downstream.
- **Veo extension needs a URI, not a local file.** `--reference-video` must be the
  **URI of a previously generated Veo clip** (e.g. the operation's `video.uri`, valid
  ~2 days); the API rejects inline video bytes ("Video URI not found"). A local path is
  refused with a clear `validation` error. Google only extends Veo-generated clips
  (≤141s, 720p, `durationSeconds=8`); a non-Veo source is rejected by the API.
- **Large local images auto-upload (Files API).** For `generateContent` image
  references, small inputs are inlined and anything past the inline budget
  (`GEMINI_INLINE_MAX_BYTES`, default 12 MB summed) is uploaded via the **Files API**
  and referenced by `fileData.fileUri` — so references above the ~20 MB inline cap
  just work (verified live with a 23 MB PNG). Only the direct API-key path supports
  upload (the resumable protocol can't be brokered).
- **Veo image inputs stay inline-only.** Veo `predictLongRunning` rejects file URIs
  for `image`/`lastFrame`/`referenceImages` (`` `uri`/`fileData` isn't supported ``),
  so those are inlined and an input over ~20 MB fails with a clear `validation` error.
  (Veo *extension* is the exception — it takes a video URI; see above.)
- **Vertex AI** (OAuth/service-account, different host) is a separate auth path and
  is out of scope for this build (Developer API-key path only).

## Cross-cutting

- **Model IDs drift.** Volc IDs are account-specific; OpenAI/Gemini snapshot names
  change. The registry's default models are sensible current picks; override with
  `--model` or the `*_IMAGE_MODEL`/`*_VIDEO_MODEL` env vars.
- **Cost estimation** branches by model family (token-based vs flat vs absent). The
  ledger tolerates missing token counts (records artifact/second counts instead).
- **Live provider calls are not covered by CI** — the offline suite (mock + mocked
  HTTP + contract tests) runs without credentials or network. Run a manual gated
  smoke test with real keys before a release.
