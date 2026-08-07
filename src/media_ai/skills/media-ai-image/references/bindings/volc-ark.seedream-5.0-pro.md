# volc-ark/seedream-5.0-pro — notes

> Parameters and limits: `media-ai capabilities --binding volc-ark/seedream-5.0-pro`.
> This file is only what that output cannot tell you.

## What it is good at

Precise placement and element control, and native text rendering in 14 languages
beyond English and Chinese. The one binding in the set that can be *directed by
coordinates*.

## Prompting

The family conventions are written out in full in `volc-ark.seedream-4.5.md` and all
apply here. The short form: a coherent sentence (*subject + action + setting*) rather
than a tag list; concise style words but as much concrete content as you need; name what
the picture is for; quote any text that must render; when editing, name what must stay
and never point with a bare pronoun; address multiple references by position
(`图一`/`图二`). `--negative-prompt` is refused on every Seedream binding, so describe
presence rather than absence.

## Interactive editing — the reason to pick this one

Two ways to say **where**, both inside `--prompt`. Only the second is exclusive to this
binding:

**Marks on the image.** Draw on the reference — circle, arrow, scribble — then describe
the mark in words. This works across the Seedream family, not just here; it needs no
declared support because it is only an annotated image plus prose.

```bash
media-ai image generate --binding volc-ark/seedream-5.0-pro \
  --reference marked.png --output out.png \
  --prompt "add a stack of art books in the lower-left marked area and a ceramic coffee
            cup in the right marked area. Remove all sketch lines. Keep the composition."
```

**Coordinates.** `<bbox>x1 y1 x2 y2</bbox>` or `<point>x y</point>` tags, which may
reference across multiple inputs:

```bash
--prompt "put the subject of image 1 <bbox>179 283 796 986</bbox> into image 2 <bbox>118 331 933 871</bbox>"
```

> **The numbers are not pixels.** Every coordinate is **normalized to a 0–999 grid** —
> top-left `0 0`, bottom-right `999 999` — regardless of the image's real size. Convert
> before writing the tag: `x = round(x_px / width * 1000)`, same for `y`, clamped to
> 999. Passing raw pixel coordinates is silently wrong: on a 4000px-wide image every
> value lands in the far left of the frame, and nothing errors.

`<point>` names a spot and lets the model judge how far the edit reaches; `<bbox>` fixes
the region. Two refinements the vendor calls out:

- **If the box contains more than one thing, say which one** — *"把图1 `<bbox>…</bbox>`
  区域内的左侧人物换成机器人"*. A box alone does not disambiguate.
- **Box what must survive, too**, and say so: *"…区域替换成花园，图1 `<bbox>…</bbox>`
  区域保持不变"*.

> **These tags only work here.** Sent to any other binding they are read as literal
> text and you get a quietly wrong image — no error, full charge. The CLI refuses that
> combination before the call; do not work around the refusal by rephrasing, switch
> binding instead.

## Traps

- **No group output.** `--count` above 1 is refused; the lite and 4.5 bindings do
  group generation, this one does not.
- **Prefer `--resolution 1.5K` over `1K`.** The vendor prices them the same and calls
  1.5K's output better, so `1K` is the tier with no reason to pick it.
- `--option optimize_prompt_mode=fast` trades quality for latency. Standard is the
  default and is worth keeping unless latency is the complaint.
- Its pixel floor is lower than 4.5's — a size 4.5 would coarsen to a preset is
  accepted here as-is. Its **ceiling** is lower too: this is the small-output binding
  of the set, topping out where the others start. For a 4K deliverable use 5.0 or 4.5.
- Keep the prompt near the family's working ceiling of ~300 Chinese characters /
  ~600 English words; past that, elements get dropped rather than refused.

## Further reading

Vendor prompt guide. It describes the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever that page shows.

- 火山方舟《Seedream 提示词指南》 — <https://www.volcengine.com/docs/82379/1829186>
  (its body is written against 5.0 lite, 4.5 and 4.0 — **not** this model. The family
  conventions above carry, but treat anything version-specific there with care, and note
  it documents no coordinate syntax: that is this binding's own.)
