# Image model matrix

`media-ai capabilities --provider <p>` is authoritative and live. This table maps
the field values you'll see, to pick a valid request quickly. All image generation
is **synchronous**.

## OpenAI — GPT-Image / DALL·E

| model | ops | geometry | sizes | max_count | seed | transparency | mask | max_refs | options |
|---|---|---|---|---|---|---|---|---|---|
| `gpt-image-2` (+`.5`, `-2026-…`) | generate, edit | pixels (mult. 16, ≤3840×2160, ratio 1:3–3:1) | arbitrary | 10 | ✗ | ✗ (rejects transparent) | ✓ | 16 | `moderation`, `output_compression`, `input_fidelity` |
| `gpt-image-1`, `gpt-image-1-mini` | generate, edit | pixels (fixed) | `1024x1024`, `1536x1024`, `1024x1536`, `auto` | 10 | ✗ | ✓ | ✓ | 16 | `moderation`, `output_compression` (`input_fidelity` except `-mini`) |
| `dall-e-3` | generate | pixels (fixed) | `1024x1024`, `1792x1024`, `1024x1792` | 1 | ✗ | ✗ | ✗ | 0 | `style` |
| `dall-e-2` | generate, edit | pixels (fixed) | `256x256`, `512x512`, `1024x1024` | 10 | ✗ | ✗ | ✓ | 1 | — |

Supports `--quality` on GPT-Image and DALL·E 3. Output formats `png, jpeg, webp`
(GPT-Image). Aspect ratios are mapped to the nearest allowed size for DALL·E.

## Gemini — Nano Banana (native image) — **aspect-ratio mode** (no `--size`)

| model (tier) | ratios | resolutions | max_count | max_refs | options |
|---|---|---|---|---|---|
| `gemini-3.1-flash-image` (flash) | standard 10 **+ ultrawide** `1:4,4:1,1:8,8:1` | `512, 1K, 2K, 4K` | 4 | 14 | `grounding`, `thinking_level` (`minimal\|high`) |
| `gemini-3-pro-image` (pro) | standard 10 | `1K, 2K, 4K` | 4 | 14 | `grounding` |
| `gemini-3.1-flash-lite-image` (lite) | standard 10 | `1K` | 4 | 14 | — |
| `gemini-2.5-flash-image` (legacy) | standard 10 | `1K` | 4 | 3 | — |

Standard 10 ratios: `1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9`. No seed,
no negative prompt. Output `png/jpeg/webp` (models emit JPEG; transcoded to the
`--output` extension). Large local references auto-upload via the Files API. SynthID
watermark is applied unconditionally. Ops: `image.generate`, `image.edit`.

## Volcengine — Seedream (`doubao-seedream-4-5-…`)

| aspect | value |
|---|---|
| ops | `image.generate`, `image.edit` |
| geometry | **both** (pixels or ratio+tier) |
| ratios | `1:1, 16:9, 9:16, 4:3, 3:4, 21:9` |
| named sizes / tiers | `1K, 2K, 4K` |
| pixel bounds | min `1280×720`, max `4096×4096`; **below 2560×1440 total pixels → 2K preset** |
| max_count | 15 (multi-image) |
| seed | ✓ |
| max_refs | 9 |
| options | `watermark` |

Custom Ark endpoint ids (`ep-…`) are treated as modality-agnostic; geometry is
validated by the Ark API rather than pre-checked. Force a fixed size with
`$ARK_IMAGE_SIZE`.

> Reminder: transparency, seed, ultrawide ratios, and options vary by model — always
> confirm with `media-ai capabilities --model <m>` before relying on one.
