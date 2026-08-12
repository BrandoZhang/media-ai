# openai/gpt-image-2 — notes

> Parameters and limits: `{{cli}} capabilities --binding openai/gpt-image-2`.
> This file is only what that output cannot tell you.

## What it is good at

**Legible in-image typography and believable material/lighting.** The dependable pick
when the picture has to carry words — a UI mock, a poster, a label, an infographic —
and when an edit has to keep a face or a product recognisably the same object.

It also reasons from world knowledge: *"an outdoor crowd in Bethel, New York, August
1969"* produces a period-accurate Woodstock scene without the event being named. Dating
and placing a scene is often shorter and truer than describing its props.

## Prompting

**Keep one order and keep it every time**, so a prompt stays diffable across
iterations:

> `[background/scene] → [subject] → [key details] → [constraints]`

**Name the intended use.** *"as a mobile app onboarding screen"*, *"as a print ad"*,
*"as a technical diagram"* — this sets polish and framing more reliably than any
adjective, because it tells the model which conventions apply.

**For anything complex, use short labelled lines rather than one long paragraph.**
Format is not the point — a paragraph, labelled segments, JSON-ish, tags all work — but
a skimmable template is what survives being edited six times:

```bash
{{cli}} image generate --binding openai/gpt-image-2 --size 1024x1536 \
  --output promo.png \
  --prompt "Use: retail promo poster.
            Scene: a sunlit terrazzo counter, soft morning shadows.
            Subject: a matte-ceramic cold-brew bottle, label facing forward.
            Details: condensation on the glass, one sprig of mint, shallow depth.
            Text: \"SLOW BREW\" in a condensed uppercase serif, centred below.
            Constraints: no other props, no visible branding beyond the label."
```

**Consistency across pictures is an anchor, not a memory.** There is no session state
between calls: write the character or product once — build, proportions, wardrobe,
palette, lighting — and paste that same block into every later prompt, changing only
the scene and the action.

**Iterate; do not front-load.** A long prompt can work, but a failure in one is hard to
locate. Start from a clean base and refine with single-change follow-ups — *"make the
lighting warmer"*, *"remove the extra tree"*. Re-state any critical detail that starts to
drift.

### `--quality` is a real decision, not a polish dial

Start at `low` and check whether it clears your bar — the vendor's position is that it is
sufficient for a lot of production work at markedly lower latency. Move to `medium` or
`high` for the cases where it demonstrably is not: **small or dense text, detailed
infographics, close-up portraits, identity-sensitive edits, and large outputs.**

### Photorealism has a keyword

**Put the word "photorealistic" in the prompt.** The vendor states it engages the model's
photorealistic mode directly; *"real photograph"*, *"taken on a real camera"* and *"iPhone
photo"* work similarly. Then do two more things:

- **Ask for imperfection explicitly** — pores, wrinkles, fabric wear, worn materials,
  everyday detail — and **avoid words that imply studio polish or staging**. "Candid" and
  "unposed" pull harder than any lens spec.
- **Treat camera specs as look, not physics.** "50mm, shallow depth of field, film grain"
  steers framing and mood; it is interpreted loosely and will not simulate an exact lens.

For wide, cinematic, low-light, rain or neon scenes, spend extra words on **scale,
atmosphere and colour** — otherwise the model trades the mood for surface realism.

### Text in the image

Put the literal string in **quotes** or **ALL CAPS**, and treat typography as a
constraint: font style, size, colour, placement. For brand names and unusual spellings,
**spell the word out letter by letter** to hold character accuracy. Small text and
multi-font layouts want `--quality high`.

### Composition and people

Name framing (close-up, wide, top-down), angle (eye-level, low-angle) and lighting, and
call out placement when layout matters — *"logo top-right"*, *"subject centred with
negative space on the left"*. For people, the details that fix anatomy and gaze are
**scale, body framing, gaze direction and object interaction**: *"full body visible, feet
included"*, *"looking down at the open book, not at the camera"*, *"hands naturally
gripping the handlebars"*.

## Editing: separate what changes from what must not

This is the discipline the whole edit flow rests on, and `image edit` exists to make you
supply the reference for it.

- **Say "change only X" and "keep everything else the same."**
- **Repeat the preserve list on every iteration.** Drift accumulates across turns, and
  restating the invariants is what stops it.
- **For a surgical edit, name the things that quietly move**: saturation, contrast,
  layout, arrows, labels, camera angle, surrounding objects.
- **With several references, address them by index *and* role** — *"Image 1: product
  photo. Image 2: style reference. Apply Image 2's style to Image 1."* When compositing,
  say which element goes where.

```bash
{{cli}} image edit --binding openai/gpt-image-2 \
  --reference room.jpg --output room-edit.png --quality medium \
  --prompt "Replace ONLY the white chairs with wooden ones.
            Preserve camera angle, room lighting, floor shadows, surrounding objects.
            Keep every other aspect of the image unchanged."
```

## Traps

- **No `--seed`.** Reproducibility comes from pinning the prompt text and the reference,
  not from a seed value.
- **No transparent background on this tier.** `--background transparent` is refused
  (exit 3). The vendor's own workaround is the right one: ask for a clean subject on a
  plain opaque background — *"crisp silhouette, no halos or fringing"* — and key it
  downstream.
- **Sizes are constrained arithmetic, not a menu.** Both edges divisible by 16, longest
  edge within the declared maximum, and edges no more than 3:1 apart. An odd request is
  refused before the call — read `constraints.geometry` rather than guessing.
- **Past ~2560x1440 the model gets less predictable.** That is well below the hard pixel
  ceiling, so `capabilities` will happily accept sizes the vendor itself calls
  experimental. Treat 2K as the reliability boundary and budget retries above it. 4K is
  the ragged edge in another way too: the vendor documents the max edge as *less than*
  3840, so an exact `3840x2160` may be refused upstream — `3824x2144` is the safe
  neighbour.
- **References are always processed at high fidelity**; the API's `input_fidelity` knob
  is rejected here, so there is no cheaper low-fidelity edit to fall back on. Note the
  vendor's cookbook passes `input_fidelity="high"` to this model in several worked
  examples anyway — copying one of those verbatim will not work.
- **A moderation block is exit 8**, carrying the provider's stable code. Rephrasing the
  same idea usually fails the same way — change what is depicted.
- Results come back as base64, never as a URL. Nothing to fetch, but a large `--count`
  is one large response rather than several small downloads.

## Further reading

Vendor prompt guides. They describe the model, not this CLI — flag names, limits and
the machine contract above are what `capabilities` says, whatever these pages show.

- Image generation guide — <https://developers.openai.com/api/docs/guides/image-generation>
- "GPT Image Generation Models Prompting Guide" (patterns, production examples) —
  <https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide>
