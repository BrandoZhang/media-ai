# Unresolved provider-specific items

Open questions and deliberate scope boundaries. Items marked `[verify]` were not
fully confirmable against live provider docs at build time and should be re-checked
before relying on them in production; the adapters are written to fail safely
(deterministic errors) rather than guess.

## OpenAI

- **Image-only.** OpenAI no longer exposes a public video API, so this adapter
  drops video entirely; `video generate --provider openai` fails pre-flight with a
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
- **Veo cancellation** is unsupported on the Developer API — `job cancel` returns a
  deterministic `unsupported` error (exit 3).
- **SynthID watermarking is unconditional** (image + video); there is no flag to
  disable it, so `--option`/capabilities do not expose one.
- **Generated Veo files expire (~48h)** on Google's servers — download promptly
  (the CLI does so in `job query --output` / `--wait true`).
- **`imageSize` is ignored by `gemini-2.5-flash-image`** (always ~1K); honored by
  `gemini-3-pro-image`. Capabilities reflect this per model.
- **Inline request size limit** (~20 MB, reportedly raised toward ~100 MB) is not
  enforced client-side, and the **Files API** upload path for very large inputs is
  not yet implemented — large local media may be rejected by the API. `[verify]`
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
