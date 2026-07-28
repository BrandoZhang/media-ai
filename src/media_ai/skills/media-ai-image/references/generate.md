# `media-ai image generate` / `image edit` — full reference

Text (+ optional reference images) → one or more images, **synchronously** (image
generation is never async). Emits a `GenerationResult` — see
`../../media-ai-shared/references/machine-contract.md`.

`generate` and `edit` take the same flags and run the same code. The only difference
is that `edit` requires at least one `--reference` and fails with exit 2
(`missing_reference`) without one, so "transform this image" cannot silently become
"invent a new one".

## All flags

| flag | default | notes |
|---|---|---|
| `--prompt` | — (required) | text prompt |
| `--output` | — (required) | path; the extension is only a filename hint. Set `--format` to choose the encoding, and keep the extension consistent with it |
| `--reference PATH...` | `[]` | reference image(s); accepts a JSON array string. **Required for `edit`.** Capped by `constraints.references.max` |
| `--count N` | `1` | N images in one call; capped by `constraints.output.max_count` |
| `--seed N` | none | only where `constraints.supports.seed` is true |
| `--negative-prompt TEXT` | none | only where `constraints.supports.negative_prompt` is true |
| `--background {transparent,opaque,auto}` | none | only where `constraints.supports.transparency` is true |
| `--quality {low,medium,high,auto}` | none | only where `constraints.supports.quality` is true |
| `--format {png,jpeg,webp}` | binding default | must be in `constraints.output.formats` |
| `--size WxH` | none | pixel geometry; needs `geometry.mode` `pixels` or `both` |
| `--aspect-ratio R` / `--ratio R` | none | ratio geometry; needs `geometry.mode` `aspect_ratio` or `both` |
| `--resolution TIER` | none | named tier, e.g. `1K\|2K\|4K` |
| `--option key=value ...` | `[]` | per-binding extras, gated on `constraints.options[]` (unknown key → exit 3) |
| global | | `--binding`, `--provider`, `--model`, `--on-unsupported`, `--pretty`, `--metadata-out`, `--log-level` |

## Geometry, in detail

A binding declares one geometry `mode`, and mixing them is refused before the call:

- `pixels` — pass `--size WxH`. Additional declared bounds may apply:
  `pixel_sizes` (a closed list of accepted sizes), `pixel_multiple` (each side must be
  a multiple of it), `pixel_min` / `pixel_max` (per-side), `pixel_total_min` /
  `pixel_total_max` (width × height), `ratio_range` (the widest and narrowest aspect
  ratio accepted).
- `aspect_ratio` — pass `--aspect-ratio` and usually `--resolution`. `aspect_ratios`
  lists ratios where the vendor really does restrict them; **its absence means the
  binding accepts more than a fixed list**, and `ratio_range` is the actual gate.
- `both` — either form works.
- `none` — the binding takes no geometry; pass neither.

Whichever bound a binding does not declare is **not** a bound of zero — it is unknown.
Do not synthesize one.

## References

`--reference` is style/subject/edit input. Its role is what makes the request
`image.image_to_image`; the file type does not. Limits come from
`constraints.references.max` and, for local files, the binding's declared input
ceilings (bytes, pixel dimensions, accepted formats) — all checked before upload, so
an oversized reference fails at the keyboard rather than after the transfer.

## Per-binding options

`--option` keys are declared per binding in `constraints.options[]` and are the *only*
place provider-specific features live. Read them from `capabilities`; passing a key a
binding does not declare is exit 3 with the key named. Values are coerced:
`true`/`false`, ints, floats (`guidance_scale=7.5`), else string.

Some bindings also accept prompt-level techniques nothing can derive from the request
— coordinate-scoped editing, for instance, where the region is written into the prompt
text itself. Those are documented in that binding's fragment under
`bindings/<provider>.<model>.md`, not here, because only prose can describe them.

## Reading the result

```bash
media-ai image generate --prompt "..." --output out.png --metadata-out meta.json
# stdout: {"ok":true, ..., "artifacts":[{"path":"out.png","kind":"image",...}], "usage":{...},
#          "meta":{"binding":"<provider>/<model>","scene":"image.text_to_image", ...}}
```

**Every image is in `artifacts[]`.** With `--count N` there are N entries: the first
has `role: null`, the rest `role: "group"`, written as `out.png`, `out_2.png`, … Some
bindings echo back what they actually did (resolved size, quality, output format) in
`meta` — trust that over what you asked for.

If it fails with exit 3, `error.details.unsupported[]` names the rejected fields —
adjust to what `media-ai capabilities` reports, or pass `--on-unsupported warn` to
attempt best-effort anyway.
