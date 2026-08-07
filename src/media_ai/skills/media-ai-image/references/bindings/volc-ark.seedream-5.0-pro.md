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

> **These tags only work here.** Sent to any other binding they are read as literal
> text and you get a quietly wrong image — no error, full charge. The CLI refuses that
> combination before the call; do not work around the refusal by rephrasing, switch
> binding instead.

## Traps

- **No group output.** `--count` above 1 is refused; the lite and 4.5 bindings do
  group generation, this one does not.
- `--option optimize_prompt_mode=fast` trades quality for latency. Standard is the
  default and is worth keeping unless latency is the complaint.
- Its pixel floor is lower than 4.5's — a size 4.5 would coarsen to a preset is
  accepted here as-is.

## Further reading

Vendor prompt guide. It describes the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever that page shows.

- 火山方舟《Seedream 提示词指南》 — <https://www.volcengine.com/docs/82379/1829186>
  (its body is written against 5.0 lite, 4.5 and 4.0 — **not** this model. The family
  conventions above carry, but treat anything version-specific there with care, and note
  it documents no coordinate syntax: that is this binding's own.)
