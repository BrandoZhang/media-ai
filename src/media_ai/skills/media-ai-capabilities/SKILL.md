---
name: media-ai-capabilities
description: >-
  Discover what the media-ai CLI can actually do on this machine — which bindings
  exist, which are reachable right now, which scenes each serves, and the geometry,
  durations, formats and provider options each accepts — as machine-readable JSON,
  BEFORE requesting a generation. Use when asked which media-ai models or providers
  are available or what one supports, or (always) to build a request that will be
  accepted instead of rejected after a billed call.
version: 2.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai capabilities --help"
  install:
    tier: core
    summary: >-
      Ask the CLI what each configured binding actually supports — scenes, sizes,
      ratios, durations, options — before generating, so a request fails at the
      keyboard instead of after a billed call.
---

# media-ai-capabilities — discover before you generate

> Read `../media-ai-shared/SKILL.md` for the machine contract. This is the
> **discovery entry point** every generation skill should call first.

`media-ai capabilities` prints the binding manifests. Discovery and pre-flight
validation read the *same* declaration, so "what does this support?" cannot drift
from what actually gets enforced — an unsupported scene, geometry or option fails
with **exit 3 before any network call**.

## Command

```bash
media-ai capabilities --pretty                        # every binding this package knows
media-ai capabilities --configured                    # only the ones reachable right now
media-ai capabilities --scene video.image_to_video    # only the ones serving this scene
media-ai capabilities --binding <provider>/<model> --pretty     # one, in full
```

| flag | notes |
|---|---|
| `--scene <group>.<what>` | only bindings serving that scene (an unknown name lists the valid ones) |
| `--configured` | drop bindings with no credential configured — what you can call *now* |
| `--binding` / `--provider` / `--model` | narrow to one binding, one provider's, or one model's |
| `--pretty` | indent the JSON |
| `--metadata-out PATH` | also write the JSON to a file |

## Output shape

```json
{"ok": true, "schema_version": 2,
 "bindings": [
   {"binding": "<provider>/<model>", "provider": "…", "model": "…", "model_id": "<wire id>",
    "title": "…", "lifecycle": "ga", "replacement": null, "verified": "2026-05-14",
    "scenes": ["image.text_to_image", "image.image_to_image"],
    "constraints": {
      "supports": {"seed": false, "negative_prompt": false, "transparency": true},
      "options": ["moderation", "output_compression"],
      "geometry": {"mode": "pixels", "pixel_sizes": ["1024x1024"], "pixel_multiple": 16},
      "output": {"formats": ["png", "jpeg", "webp"], "max_count": 10},
      "references": {"max": 16},
      "video": {"is_async": true},
      "audio": {"formats": ["mp3_44100_128"]}},
    "notes": ["…"],
    "transport": "http", "needs_credential": true,
    "available": true, "configured": true}],
 "defaults": {"image.text_to_image": "<provider>/<model>"}}
```

## The fields that decide a request

- **`scenes[]`** — what this binding serves. The CLI derives the scene from your
  inputs (see `../media-ai-shared/SKILL.md`); a binding that does not declare it is
  refused before the call, naming alternatives you have configured.
- **`available` / `configured`** — `available` means this machine can call it (its
  credential resolves, or it needs none); `configured` means the config names it
  explicitly. **Filter on `available` before choosing** — a binding that exists in the
  package but has no key is not a place you can send work.
- **`constraints.geometry`** — `mode` is `pixels`, `aspect_ratio`, `both` or `none`,
  plus whichever of `pixel_sizes` / `aspect_ratios` / `named_sizes` / `pixel_min` /
  `pixel_max` / `pixel_multiple` / `ratio_range` that binding actually declares.
- **`constraints.supports`** — booleans for the cross-provider knobs (`seed`,
  `negative_prompt`, `transparency`, `quality`, `first_frame`, `last_frame`,
  `reference_images` / `_videos` / `_audios`, `audio`, `watermark_control`,
  `return_last_frame`, `cancel`, `dialogue`, `composition_plan`, `timestamps`, …).
- **`constraints.options[]`** — the `--option key=value` keys this binding accepts.
  Any other key is exit 3.
- **`constraints.output` / `.references` / `.video` / `.audio`** — formats, `max_count`,
  reference limits, `is_async`, durations, character caps.
- **`defaults`** — which binding a request with no `--binding`/`--provider`/`--model`
  will use, per scene.

> **An absent field means "not declared", never "zero" or "unlimited".** If
> `max_count` is missing, the limit is unknown — do not read it as 0 and do not
> invent one. Nothing here is filled with a placeholder, which is what lets you trust
> the values that *are* present.

## `lifecycle` and `verified`

`lifecycle` is `ga` / `preview` / `deprecated`; a deprecated binding names its
`replacement`. `verified` is the date someone ran this declaration against the live
API — **absent means unverified, not broken**, and it is never invented.

## How this pairs with `--on-unsupported`

Capabilities is the same source of truth as pre-flight validation. If you already
know a field is unsupported but want best-effort anyway, pass `--on-unsupported warn`
(log and proceed) or `ignore` (drop silently) on the generation command; the default
`error` turns any mismatch into exit 3 with `error.details.unsupported[]` naming
each rejected field.
