# volc-ark/seedream-5.0 — notes

> Parameters and limits: `media-ai capabilities --binding volc-ark/seedream-5.0`.
> This file records the API behavior that capability output cannot express.

## Prompting

The Seedream family reads a **layered description**, not a keyword list. Stack the
layers in this order and stop wherever you have said enough:

> `[subject] + [what it is doing] + [environment] + [composition & camera] + [light] + [style]`

Chinese and English prompts both work, and the vendor's own guide and examples are
written in Chinese — worth matching when a prompt is not behaving. For text *in* the
picture, quote the literal string; unquoted text is treated as a description and comes
back paraphrased.

- **For a group, describe the axis of variation** — *"same character, four seasons"*,
  *"same bottle, four angles"* — rather than repeating the subject. See below: `--count`
  generates the set *as a set*, which is why it stays coherent where four separate calls
  would not.
- **With several references, number them.** They keep their input order on the wire, so
  "image 1"/"image 2" in the prompt resolves as written.
- **Describe presence, not absence.** No Seedream binding declares `negative_prompt`,
  so `--negative-prompt` is refused (exit 3). Say what should be in the frame — *"a bare
  concrete wall"* — rather than what should not.
- **Coordinate direction is not available here.** `<bbox>`/`<point>` tags belong to
  5.0 Pro; this binding declares `interactive_edit = false` and the CLI refuses the
  combination rather than letting them be read as literal text.

## Grouped image generation

Use `--count` to ask for a coherent set. It maps to Ark's
`sequential_image_generation=auto` and `max_images`; each result is reported in
`artifacts[]`, with later images marked `role: "group"`. References and generated
images share the binding's total-image budget, so query capabilities before choosing a
large set.

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

- 火山方舟《Seedream 4.0-5.0 提示词指南》 — <https://www.volcengine.com/docs/82379/1829186>
