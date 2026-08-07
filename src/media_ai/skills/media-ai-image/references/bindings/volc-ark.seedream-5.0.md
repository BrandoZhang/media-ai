# volc-ark/seedream-5.0 — notes

> Parameters and limits: `media-ai capabilities --binding volc-ark/seedream-5.0`.
> This file records the API behavior that capability output cannot express.

## Prompting

The family conventions are written out in full in `volc-ark.seedream-4.5.md`. The
short form:

- **A coherent sentence, not a tag list** — *subject + action + setting*, with style,
  colour, light and composition as an optional layer on top.
- **Concise beats ornate.** Adjective-stacking makes results worse. Enumerating many
  concrete objects does not — be brief with style words, not with content. The vendor's
  working ceiling is about **300 Chinese characters or 600 English words**; past that
  the model starts dropping elements rather than failing.
- **Name what the picture is for** (a logo, a poster, an infographic); it sets layout
  and polish in one phrase.
- **Quote text that must appear in the image**; unquoted, it comes back paraphrased.
- **When editing, name what must stay** ("保持动作不变") and never refer to the target
  with a bare pronoun.
- **With several references, address them by position** — `图一`/`图二`, "image 1"/"image
  2". They keep their input order on the wire, so this resolves as written.
- **Mark up the reference** — arrow, box, scribble — and refer to the mark by colour or
  shape when a region is easier to point at than to describe.
- **Describe presence, not absence.** No Seedream binding declares `negative_prompt`,
  so `--negative-prompt` is refused (exit 3). Say what should be in the frame — *"a bare
  concrete wall"* — rather than what should not.
- **Coordinate direction is not available here.** `<bbox>`/`<point>` tags belong to
  5.0 Pro; this binding declares `interactive_edit = false` and the CLI refuses the
  combination rather than letting them be read as literal text. Marking up the image,
  above, is the substitute.

## Grouped image generation

Use `--count` to ask for a coherent set. It maps to Ark's
`sequential_image_generation=auto` and `max_images`; each result is reported in
`artifacts[]`, with later images marked `role: "group"`. References and generated
images share the binding's total-image budget, so query capabilities before choosing a
large set.

In the prompt, name the **axis of variation** rather than repeating the subject — the
vendor's own triggers are words like *"一系列"*, *"一套"*, *"组图"* or an explicit count,
followed by what differs across the set.

```bash
media-ai image edit --binding volc-ark/seedream-5.0 \
  --reference person.png outfit.png --count 3 --resolution 2K \
  --prompt "three connected scenes: morning, noon, and night" --output scenes.jpg
```

Remote HTTPS references are forwarded to Ark unchanged and retain their input order,
so prompts can refer to “image 1” and “image 2”.

## Streaming response

`--option stream=true` requests Ark's server-sent-event response. The CLI waits for
the finished stream, orders images by Ark's `image_index`, downloads them, and still
prints one final JSON result — it does not interleave events with stdout.

Use `--option stream=false` only when the endpoint needs that field explicitly; when
omitted, the adapter leaves it off the wire. `--option response_format=b64_json` is
available when a signed URL cannot be fetched from the calling network.

## Further reading

Vendor prompt guide. It describes the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever that page shows.

- 火山方舟《Seedream 提示词指南》 — <https://www.volcengine.com/docs/82379/1829186>
  (written against "5.0 lite", 4.5 and 4.0 — **this binding is the lite model**: the
  vendor lists `doubao-seedream-5-0-260128` under that name, so the guide covers it
  directly.)
