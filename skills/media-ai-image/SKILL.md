---
name: media-ai-image
description: >-
  Generate or edit images from a text prompt (with optional reference images and
  an inpaint mask) via the media-ai CLI, across OpenAI GPT-Image, Gemini Nano Banana,
  and Volcengine Seedream. Use when asked to create, generate, make, draw, or edit an
  image / picture / logo / icon / illustration / photo from the command line, or to
  inpaint or restyle an existing image.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai capabilities --model gpt-image-2"
---

# media-ai-image — generate & edit images

> **Read `../media-ai-shared/SKILL.md` first** for the machine contract (one JSON
> object on stdout, exit-code categories), provider selection, and credentials.

Two operations, one normalized request:

- `media-ai image generate` — text (+ optional references) → image.
- `media-ai image edit` — reference image(s) (+ optional `--mask`) → image. **Requires
  at least one `--reference`** (else exit 2).

## Discover first

Different models allow different geometry, counts, and options. Check before you commit:

```bash
media-ai capabilities --provider openai --model gpt-image-2 --pretty
media-ai capabilities --provider gemini --model gemini-3.1-flash-image --pretty
```

Read `image.geometry_mode` (`pixels` / `aspect_ratio` / `both`), `pixel_sizes` /
`aspect_ratios`, `max_count`, `output_formats`, `supports_*`, and `options[]`. An
unsupported field fails with **exit 3** and names the field — override deliberately
with `--on-unsupported warn|ignore`.

## Core flags (shared by generate & edit)

| flag | meaning |
|---|---|
| `--prompt` (required) | text prompt |
| `--output` (required) | output image path; the extension is a filename hint — the image format is set by `--format` (where supported) or the provider/model default |
| `--reference PATH...` | reference image(s); space-separated or a JSON array string. **Required for `edit`.** |
| `--mask PATH` | *(edit only)* PNG alpha mask marking the inpaint region |
| `--count N` | request N images (default 1); adds extra `artifacts[]` |
| `--seed N` | reproducibility (where the model supports it) |
| `--negative-prompt TEXT` | what to avoid |
| `--background {transparent,opaque,auto}` | transparency (model-dependent) |
| `--quality {low,medium,high,auto}` | quality tier (model-dependent) |
| `--format {png,jpeg,webp}` | force output format |
| geometry | `--size WxH` **or** `--aspect-ratio 16:9 --resolution 2K` (see below) |
| `--option key=value` | provider-specific, capability-gated |

## Geometry

Two mutually-exclusive ways; each adapter maps/validates it to the model:

```bash
--size 1024x1536                       # explicit pixels (pixel-mode models: openai, volc)
--aspect-ratio 16:9 --resolution 2K    # ratio + named tier (Gemini image is ratio-mode; 1K|2K|4K)
```

## Quick starts

```bash
# OpenAI GPT-Image (pixels or ratio+tier, quality, up to 10; gpt-image-1* adds transparency)
media-ai image generate --provider openai --model gpt-image-2 \
    --prompt "a red bicycle on a white seamless background" \
    --size 1024x1024 --quality high --output bike.png

# Gemini Nano Banana (aspect-ratio mode, not pixels)
media-ai image generate --provider gemini --model gemini-3.1-flash-image \
    --prompt "isometric city block, soft light" \
    --aspect-ratio 16:9 --resolution 2K --output city.png

# Volcengine Seedream (pixels; min 2560x1440 or it falls back to the 2K preset)
media-ai image generate --provider volc \
    --prompt "silver astronaut on a red dune" --size 2560x1440 --output astro.png

# Edit / inpaint with a mask
media-ai image edit --provider openai --model gpt-image-1 \
    --reference room.png --mask sofa-region.png \
    --prompt "replace the sofa with a green velvet one" --output room2.png
```

## References

- `references/generate.md` — full `image generate` flag semantics + per-provider examples.
- `references/edit.md` — `image edit`, `--reference`/`--mask`, inpainting, per-model reference limits.
- `references/providers.md` — image model matrix: geometry modes, sizes/ratios, max references, per-model `--option` keys.
