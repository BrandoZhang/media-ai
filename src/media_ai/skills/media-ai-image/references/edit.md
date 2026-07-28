# `media-ai image edit` — full reference

Reference image(s) (+ optional inpaint `--mask`) → an edited image, synchronously.
Same flags as `image generate` **plus `--mask`**, and `--reference` is **required**
(at least one; otherwise exit 2 `image edit requires at least one --reference`).

## When to use edit vs generate

- **generate** — make a new image; references (if any) only guide style/subject.
- **edit** — transform a specific input image. With `--mask`, restrict changes to
  the masked (inpaint) region; without a mask, the whole image is restyled/modified.

## Flags specific to edit

| flag | notes |
|---|---|
| `--reference PATH...` | **required**; the image(s) to edit. Accepts a JSON array string. `role=reference_image`. |
| `--mask PATH` | PNG alpha mask marking the region to inpaint (`role=mask`). Model must report `supports_mask`. |

All other flags (`--prompt`, `--output`, `--count`, `--seed`, `--background`,
`--quality`, `--format`, geometry, `--option`, globals) behave as in
`generate.md`.

## Reference / mask support by model

| model | `image.edit`? | `supports_mask`? | `max_references` |
|---|---|---|---|
| openai `gpt-image-2` / `gpt-image-1.5` / `gpt-image-1` / `-mini` | ✓ | ✓ | 16 |
| gemini `gemini-3.1-flash-image` / `-pro` / `-lite` | ✓ | model-dependent | 14 |
| gemini `gemini-2.5-flash-image` (legacy) | ✓ | model-dependent | 3 |
| volc Seedream | ✓ | — | 9 |

Always confirm with `media-ai capabilities --model <m>` (`supports_mask`,
`max_references`, `operations` including `image.edit`).

## Examples

```bash
# Masked inpaint (OpenAI): change only the masked region
media-ai image edit --binding openai/gpt-image-1 \
    --reference room.png --mask sofa-region.png \
    --prompt "a green velvet sofa" --output room-green.png

# Maskless restyle (Gemini): recolor the whole image
media-ai image edit --binding gemini/gemini-3.1-flash-image \
    --reference bike.png --prompt "make it a blue bike, keep the background" \
    --aspect-ratio 1:1 --resolution 1K --output bike-blue.png

# Multiple references (compose subjects), passed as a JSON array
media-ai image edit --binding gemini/gemini-3-pro-image \
    --reference '["subject.png","style.png"]' \
    --prompt "the subject rendered in the reference's art style" --output combined.png
```

Missing `--reference` on `edit` → exit 2. A mask on a model without `supports_mask`,
too many references, or an unsupported option → exit 3 with the offending field named.
