# volc-ark/seedream-5.0-pro — notes

> Parameters and limits: `media-ai capabilities --binding volc-ark/seedream-5.0-pro`.
> This file is only what that output cannot tell you.

## What it is good at

Precise placement and element control, and native text rendering in 14 languages
beyond English and Chinese. The one binding in the set that can be *directed by
coordinates*.

## Interactive editing — the reason to pick this one

Two ways to say **where**, both inside `--prompt`:

**Marks on the image.** Draw on the reference — circle, arrow, scribble — then describe
the mark in words:

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
