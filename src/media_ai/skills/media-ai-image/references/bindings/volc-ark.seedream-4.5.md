# volc-ark/seedream-4.5 — notes

> Parameters and limits: `media-ai capabilities --binding volc-ark/seedream-4.5`.
> This file is only what that output cannot tell you.

## What it is good at

**Batches.** The largest group budget in the Seedream set — a storyboard, a set of
product angles, a run of variations in one call — and it takes `--seed`, which neither
Gemini nor OpenAI image binding here does, so a run is reproducible.

## Prompting

The Seedream family reads a **layered description**, not a keyword list. Stack the
layers in this order and stop wherever you have said enough:

> `[subject] + [what it is doing] + [environment] + [composition & camera] + [light] + [style]`

```bash
media-ai image generate --binding volc-ark/seedream-4.5 --seed 42 \
  --resolution 2K --aspect-ratio 16:9 --output shot.jpg \
  --prompt "一位穿藏青风衣的女性，倚在旧书店的木质门框上向外望；黄昏的街道，暖色路灯刚亮；
            中景，略低机位，浅景深；侧逆光勾出轮廓；写实摄影风格，细腻胶片颗粒"
```

Chinese and English prompts both work, and the vendor's own guide and examples are
written in Chinese — worth matching when a prompt is not behaving, since that is the
phrasing the guide's patterns were written against. For text *in* the picture, quote the
literal string; unquoted text is treated as a description and comes back paraphrased.

**For a group, describe the axis of variation** rather than repeating the subject —
*"same character, four seasons"*, *"same bottle, four camera angles"*. `--count` maps to
Ark's sequential generation, so the set is generated as a set and stays coherent; asking
four times separately does not.

**Describe presence, not absence.** No Seedream binding declares `negative_prompt`, so
`--negative-prompt` is refused (exit 3) — there is nowhere to put "no text, no
watermark". Say what should be in the frame instead: *"a bare concrete wall"* rather
than *"a wall with no posters"*.

## Traps

- **Coordinate direction does not work here.** `<bbox>`/`<point>` tags are a
  Seedream 5.0 Pro feature; this binding declares `interactive_edit = false` and the CLI
  refuses the combination. Sent as prose they would be read as literal text — switch
  binding rather than rephrasing.
- **JPEG only.** `--output x.png` writes JPEG bytes under a `.png` name. Name outputs
  `.jpg` so the file does not lie about itself.
- **There is a 2K pixel floor.** A smaller `--size` is not honoured as asked — it falls
  back to the `2K` preset. If you need something small, downscale afterwards; if you
  need a genuinely small render, use 5.0, whose floor is lower.
- The documented aspect ratios are **examples, not an enum**. Only the declared ratio
  range gates, so an unusual ratio is worth trying rather than rounding to a preset.
- References and generated images share one total-image budget. A large `--count` plus
  several references can exceed it — check `constraints.output.max_total_images`.

## Further reading

Vendor prompt guide. It describes the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever that page shows.

- 火山方舟《Seedream 4.0-5.0 提示词指南》 — <https://www.volcengine.com/docs/82379/1829186>
