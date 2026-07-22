# `media-ai image generate` — full reference

Text (+ optional reference images) → one or more images, synchronously (image
generation is never async). Emits a `GenerationResult` (see
`../../media-ai-shared/references/machine-contract.md`).

## All flags

| flag | default | notes |
|---|---|---|
| `--prompt` | — (required) | text prompt |
| `--output` | — (required) | path; the extension is only a filename hint (set `--format` to choose the image format — keep the extension consistent with it) |
| `--reference PATH...` | `[]` | style/subject reference image(s); accepts a JSON array string |
| `--count N` | `1` | N images in one call (a related group); capped by the model's `max_count` |
| `--seed N` | none | only where `supports_seed` is true (openai GPT-Image does **not** expose seed) |
| `--negative-prompt TEXT` | none | only where `supports_negative_prompt` is true |
| `--background {transparent,opaque,auto}` | none | transparency; `gpt-image-2` rejects `transparent` |
| `--quality {low,medium,high,auto}` | none | GPT-Image quality tier |
| `--format {png,jpeg,webp}` | provider/model default | output image format where the model supports it (see `output_formats`) |
| `--size WxH` | none | pixel geometry (openai, volc; gemini is ratio-only) |
| `--aspect-ratio R` / `--ratio R` | none | ratio geometry (gemini; also openai GPT-Image `2K\|4K`) |
| `--resolution TIER` | none | named tier `1K\|2K\|4K` |
| `--option key=value ...` | `[]` | provider-specific; capability-gated (unknown → exit 3) |
| global | | `--provider`, `--model`, `--provider-profile`, `--on-unsupported`, `--pretty`, `--metadata-out`, `--log-level` |

## Per-provider examples

```bash
# --- OpenAI ---
# gpt-image-2: arbitrary sizes (multiple of 16, up to 3840x2160), no seed, transparency off
media-ai image generate --provider openai --model gpt-image-2 \
    --prompt "product photo of a matte black kettle" \
    --size 1536x1024 --quality high --format webp --output kettle.webp \
    --option moderation=low

# gpt-image-1.5: fixed sizes, transparency + input_fidelity supported
media-ai image generate --provider openai --model gpt-image-1.5 \
    --prompt "watercolor fox on a transparent background" \
    --size 1024x1536 --background transparent --quality high \
    --option input_fidelity=high --output fox.png

# --- Gemini (aspect-ratio mode; do NOT pass --size) ---
media-ai image generate --provider gemini --model gemini-3.1-flash-image \
    --prompt "logo: a friendly origami crane, flat vector" \
    --aspect-ratio 1:1 --resolution 1K --output crane.png \
    --option thinking_level=high

# multiple candidates (max_count 4 on Gemini)
media-ai image generate --provider gemini --model gemini-3-pro-image \
    --prompt "concept art, alien bazaar" --aspect-ratio 16:9 --resolution 4K \
    --count 4 --output bazaar.png --option grounding=false

# --- Volcengine Seedream ---
# Below 2560x1440 total pixels falls back to the 2K preset; up to 15 images
media-ai image generate --provider volc \
    --prompt "storybook illustration, a whale over a city" \
    --aspect-ratio 16:9 --resolution 2K --count 3 --seed 7 \
    --output whale.png --option watermark=false
```

## Reading the result

```bash
media-ai image generate --provider gemini --prompt "..." --output out.png --metadata-out meta.json
# stdout: {"ok":true,...,"artifacts":[{"path":"out.png",...}], "usage":{...}}
# on --count > 1, artifacts[] has each image; extra_paths mirrors artifacts[1:]
```

If it fails with exit 3, `error.details.unsupported[]` names the rejected fields —
adjust geometry/option/count to what `media-ai capabilities` reports, or pass
`--on-unsupported warn` to attempt best-effort.
