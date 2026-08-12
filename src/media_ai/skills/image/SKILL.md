---
name: {{skill}}image
description: >-
  Generate an image from a text prompt, or transform one from reference images, via
  the {{cli}} CLI. Handles geometry (pixel size or aspect-ratio + resolution tier),
  multiple candidates, output format, transparency and per-binding options. Use when
  asked to create, generate, make, draw, edit, restyle or modify an image / picture /
  logo / icon / illustration / photo on the command line.
version: 2.0.0
metadata:
  requires:
    bins: ["{{cli}}"]
  cliHelp: "{{cli}} capabilities --scene image.text_to_image"
  install:
    tier: optional
    summary: >-
      Make and edit pictures from a prompt — logos, icons, illustrations, photos —
      with optional reference images. Ask the CLI which bindings can serve it; the
      lineup is not fixed.
---

# {{skill}}image — generate & edit images

> **Read `../{{skill}}shared/SKILL.md` first** for the machine contract (one JSON
> object on stdout, exit-code categories), how a binding is named, and credentials.

Image generation is **synchronous** — every command here returns the finished
`artifacts[]`, never a job to poll.

## Two scenes, decided by what you pass

| you pass | scene | meaning |
|---|---|---|
| just `--prompt` | `image.text_to_image` | make a new image from words |
| `--prompt` + `--reference` | `image.image_to_image` | transform / restyle / compose the input(s) |

No flag selects the scene — the references do. `{{cli}} image edit` is the same
request as `generate` with `--reference` made **mandatory**; use it when you mean to
transform an input, and a forgotten `--reference` fails (exit 2) instead of quietly
producing an unrelated new picture.

## Ask before you commit

Geometry mode, counts, formats and options differ sharply between bindings, and this
skill deliberately does not list them — lineups change faster than skill text:

```bash
{{cli}} bindings list                                # what can this machine call?
{{cli}} capabilities --scene image.image_to_image    # who serves this scene?
{{cli}} capabilities --binding <id> --pretty         # what does that one accept?
```

Read `constraints.geometry.mode` (`pixels` / `aspect_ratio` / `both`),
`constraints.output.formats` / `.max_count`, `constraints.references.max`,
`constraints.supports.*` and `constraints.options[]`. An unsupported field fails with
**exit 3** and names the field; override deliberately with `--on-unsupported warn|ignore`.

## Flags

| flag | meaning |
|---|---|
| `--prompt` (required) | text prompt |
| `--output` (required) | output path; the extension is a filename hint — the image format comes from `--format` (where supported) or the binding's default |
| `--reference PATH...` | reference image(s); space-separated or a JSON array string. **Required for `edit`.** |
| `--count N` | request N images (default 1); each extra one is another entry in `artifacts[]` with `role: "group"` |
| `--seed N` | reproducibility, where `supports.seed` is true |
| `--negative-prompt TEXT` | what to avoid, where `supports.negative_prompt` is true |
| `--background {transparent,opaque,auto}` | transparency, where `supports.transparency` is true |
| `--quality {low,medium,high,auto}` | quality tier, where `supports.quality` is true |
| `--format {png,jpeg,webp}` | output format; must be one of `constraints.output.formats` |
| geometry | `--size WxH` **or** `--aspect-ratio 16:9 --resolution 2K` |
| `--option key=value` | per-binding extras, gated on `constraints.options[]` |

Plus the global flags from `{{skill}}shared`: `--binding` / `--provider` / `--model`,
`--on-unsupported`, `--pretty`, `--metadata-out`, `--log-level`.

## Geometry

Two mutually exclusive ways to ask; which one a binding accepts is
`constraints.geometry.mode`:

```bash
--size 1024x1536                       # explicit pixels
--aspect-ratio 16:9 --resolution 2K    # ratio + named tier (1K|2K|4K)
```

Passing pixels to a ratio-only binding (or the reverse) is exit 3 before the call.

## Quick starts

```bash
# Text -> image, using the configured default for this scene
{{cli}} image generate --prompt "a red bicycle on a white seamless background" \
    --output bike.png

# Same, on a specific binding with its own geometry and options
{{cli}} image generate --binding <provider>/<model> \
    --prompt "isometric city block, soft light" \
    --aspect-ratio 16:9 --resolution 2K --output city.png

# Transform an existing image (image.image_to_image)
{{cli}} image edit --reference room.png \
    --prompt "replace the sofa with a green velvet one" --output room2.png

# Compose several references, passed as a JSON array
{{cli}} image edit --reference '["subject.png","style.png"]' \
    --prompt "the subject rendered in the reference's art style" --output combined.png

# Several candidates in one call
{{cli}} image generate --prompt "concept art, alien bazaar" \
    --count 4 --output bazaar.png
```

## Reading the result

Every file is in `artifacts[]` — with `--count N` there are N entries, the first with
`role: null` and the rest `role: "group"`. `meta.binding` and `meta.scene` record what
actually ran.

## References

- `references/generate.md` — flag-by-flag semantics, geometry and result details.
- `references/bindings/<provider>.<model>.md` — what one binding is good at, how to
  prompt it, and its traps. Only bindings with something non-obvious to say have one.
- `../{{skill}}shared/references/bindings.md` — how to read `bindings list` /
  `capabilities` output and pick between candidates.
