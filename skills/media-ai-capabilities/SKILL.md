---
name: media-ai-capabilities
description: >-
  Discover what the media-ai CLI's providers and models support — operations,
  geometry modes, allowed sizes/aspect-ratios/resolutions, durations, provider
  options, and async behavior — as machine-readable JSON, BEFORE requesting a
  generation. Use when asked which media-ai models/providers exist or what a model
  supports, or (always) to pick a valid request and avoid unsupported-option errors.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai capabilities --help"
---

# media-ai-capabilities — discover before you generate

> Read `../media-ai-shared/SKILL.md` for the machine contract. This is the
> **discovery entry point** every generation skill should call first.

`media-ai capabilities` reports exactly what each provider/model supports, so you can
build a valid request instead of guessing. Requesting an unsupported operation,
geometry, or option otherwise fails with **exit 3** *before any network call*.

## Command

```bash
media-ai capabilities                                   # every registered provider/model
media-ai capabilities --provider openai --pretty        # one provider
media-ai capabilities --provider gemini --model veo-3.1-generate-preview --pretty   # one model
```

| flag | notes |
|---|---|
| `--provider <p>` | limit to one provider |
| `--model <m>` | limit to one model |
| `--pretty` | indent the JSON |
| `--metadata-out PATH` | also write the JSON to a file |

## Output shape

```json
{"ok": true, "schema_version": 1,
 "providers": [
   {"provider": "openai",
    "models": [
      {"provider": "openai", "model": "gpt-image-2", "modalities": ["image"],
       "image": {"operations": ["image.generate","image.edit"], "geometry_mode": "pixels",
                 "pixel_sizes": [...], "max_count": 10, "output_formats": ["png","jpeg","webp"],
                 "supports_seed": false, "supports_mask": true, "max_references": 16,
                 "options": ["moderation","output_compression","input_fidelity"]},
       "video": null, "notes": [...], "experimental": false, "aliases": [...]}
    ]}
 ]}
```

## Fields that decide a valid request

- **image**: `operations`, `geometry_mode` (`pixels|aspect_ratio|both|none`),
  `pixel_sizes` / `aspect_ratios` / `named_sizes`, `pixel_min` / `pixel_max` /
  `pixel_multiple`, `max_count`, `output_formats`, `supports_seed` /
  `supports_negative_prompt` / `supports_transparency` / `supports_quality` /
  `supports_mask`, `max_references`, `options`.
- **video**: `operations`, `is_async`, `aspect_ratios`, `resolutions`, `durations`,
  `supports_first_frame` / `supports_last_frame`, `supports_reference_images/videos/audios`,
  `supports_seed` / `supports_negative_prompt` / `supports_audio` / `audio_default`,
  `supports_watermark_control`, `supports_return_last_frame`, `supports_cancel`, `options`.

## How this pairs with `--on-unsupported`

Capabilities is the same source of truth used for pre-flight validation. If you
already know a field is unsupported but want best-effort anyway, pass
`--on-unsupported warn` (log and proceed) or `ignore` (drop silently) on the
generation command; the default `error` turns any mismatch into exit 3 with
`error.details.unsupported[]` naming each rejected field.
