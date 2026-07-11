# Unresolved provider-specific items

Open questions and deliberate scope boundaries. Items marked `[verify]` were not
fully confirmable against live provider docs at build time and should be re-checked
before relying on them in production; the adapters are written to fail safely
(deterministic errors) rather than guess.

## OpenAI

- **Sora video is experimental.** The Videos API (`POST /v1/videos` → poll) is
  implemented but its availability/shape was not fully verifiable, and some
  third-party sources claim a deprecation timeline we could not confirm in the
  official docs. Treat `--provider openai` video as best-effort. `[verify]`
- **`input_fidelity` on gpt-image-2** — declared as an option; docs say
  "gpt-image-1 and gpt-image-1.5 and later," which is ambiguous for gpt-image-2. `[verify]`
- **Streaming partial images** (`partial_images`) are declared in capabilities but
  not yet wired to write `<stem>_partial_N` artifacts (sync full-image path only).
- **DALL·E** returns a temporary URL by default; we force `response_format=b64_json`
  to unify on bytes. Flat-priced (no token `usage`).

## Gemini

- **`generateAudio` on the Developer API** is unreliable for Veo 3.x (audio is
  native/always-on; Veo 2 is silent). We forward `--audio` when set but do not
  depend on it being honored. `[verify]`
- **Veo cancellation** is unsupported on the Developer API — `job cancel` returns a
  deterministic `unsupported` error (exit 3).
- **SynthID watermarking is unconditional** (image + video); there is no flag to
  disable it, so `--option`/capabilities do not expose one.
- **Generated Veo files expire (~48h)** on Google's servers — download promptly
  (the CLI does so in `job query --output` / `--wait true`).
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
- **Veo extension needs a URI, not a local file.** `--reference-video` must be the
  **URI of a previously generated Veo clip** (e.g. the operation's `video.uri`, valid
  ~2 days); the API rejects inline video bytes ("Video URI not found"). A local path is
  refused with a clear `validation` error. Google only extends Veo-generated clips
  (≤141s, 720p, `durationSeconds=8`); a non-Veo source is rejected by the API.
- **Inline request size limit** (~20 MB) *is* now enforced client-side: local media
  over the cap fails with a clear `validation` error (exit 3) instead of an opaque API
  rejection. The **Files API** upload path for larger inputs is not yet implemented,
  so very large clips/images can't be sent inline.
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
