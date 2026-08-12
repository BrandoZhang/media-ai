# gemini/nano-banana-2 — notes

> Parameters and limits: `{{cli}} capabilities --binding gemini/nano-banana-2`.
> This file is only what that output cannot tell you.

## What it is good at

**Instruction-following on an existing image.** It edits by being *told what to change*
rather than by being given a mask, and it holds the untouched parts of the frame while
doing it. Also the widest reference budget in the set, and the only image binding here
that can pull in live facts (`--option grounding=true`).

**The reference budget is tiered by role, not one flat pool.** The declared maximum is
the *total*; underneath it the vendor documents up to **10 objects** carried at high
fidelity and up to **4 characters** held for resemblance. Style references are a
different matter — that role is documented for the Pro model, not this one, so transfer
a look by *describing* it (*"in the style of Van Gogh's Starry Night, swirling impasto
brushstrokes"*) rather than by attaching an exemplar and hoping.

## Prompting

**Text to image — say all five parts.** The model responds to a full sentence far
better than to a keyword pile:

> `[subject] + [action] + [location/context] + [composition] + [style]`

```bash
{{cli}} image generate --binding gemini/nano-banana-2 --aspect-ratio 3:2 \
  --output editorial.png \
  --prompt "A fashion model in a tailored brown dress, posing with a confident
            statuesque stance, against a seamless deep-red studio backdrop.
            Medium-full shot, centre-framed. Editorial magazine style, shot on
            medium-format film, pronounced grain, cinematic lighting."
```

**Break a complex scene into steps.** For a picture with several interacting elements,
sequence the instruction rather than listing: *"First, a misty forest at dawn. Then, in
the foreground, a moss-covered stone altar. Finally, a single glowing sword on top."*

**With references — state the *relationship*, not just the contents.** Each reference
needs a job in the sentence, or the model averages them:

```bash
{{cli}} image edit --binding gemini/nano-banana-2 \
  --reference sketch.png fabric.jpg --output chair.png \
  --prompt "Use the sketch as the structure and the fabric sample as the upholstery
            texture. Render as a high-fidelity 3D armchair in a sun-lit minimalist
            living room."
```

**Editing is a text mask.** There is no mask input and none is needed — name the region
in words and everything unnamed is meant to survive: *"remove the man on the left,
keep the reflection in the window"*. Say what should stay, not only what should go.

**Text in the image:** quote the literal string and specify the face —
`the words "URBAN EXPLORER" in a bold white sans-serif`. Unquoted text is treated as a
description of the scene and tends to come back paraphrased or misspelled.

**Write the copy before you ask for the picture.** The vendor's own guidance is that
the model does best when the text is settled first and *then* rendered — so draft the
headline with a text model, and pass the finished string into the image prompt rather
than asking this one to invent and typeset in a single step.

**Describe presence, not absence.** "an empty street at dawn" works; "a street with no
cars" tends to produce cars. There is no `--negative-prompt` on this binding to fall
back on.

**Grounding is a separate sentence.** With `--option grounding=true` the useful shape is
*[what to look up] → [what to conclude from it] → [how to draw the conclusion]*; a
lookup with no stated visual consequence changes nothing in the picture. Two documented
limits shape what it is good for: images found by the search are **not** passed through
as visual context, and real-world photos of people from search are not usable here. It
grounds *facts* — weather, scores, dates, prices — not appearances.

## Traps

- **No `--seed`.** The same prompt twice is two different pictures, and there is no
  knob that makes a render reproducible. Keep the picture, not the recipe.
- **SynthID watermarking is unconditional**, as on every Gemini image and video path
  here. There is no flag, which is why `watermark_control` is not declared.
- **JPEG is what the model returns.** `--output x.png` is honoured — the CLI writes the
  format the filename asks for — but the re-encode happens after a lossy step, so a
  `.png` from here is not a lossless original.
- **`--count` is a request, not a contract.** The vendor states the model will not
  always return the number of images asked for, so `artifacts[]` can hold fewer than
  `--count`. Read the array rather than assuming its length.
- **Thinking is always on and always billed.** It cannot be disabled: the model renders
  up to two interim compositions before the final image, and those tokens are charged
  whether or not you look at them. `--option thinking_level=high` only *raises* the
  budget from the default minimal — it is not the difference between thinking and not.
  Worth it for several interacting constraints; wasted on a single-subject shot.
- **Geometry defaults to the input.** With a reference and no `--aspect-ratio`, the
  output takes the reference's shape; with neither, you get a square. State the ratio
  when it matters rather than inferring it from the first result.
- Large references cross into the Files API and are passed by URI instead of inline.
  That is automatic, but it makes the first call of a batch noticeably slower.
- Prompt quality falls off outside the vendor's tested languages — English plus roughly
  fifteen others (zh-CN, ja-JP, ko-KR, es-MX, fr-FR, de-DE, pt-BR, ru-RU, hi-IN, ar-EG,
  it-IT, id-ID, vi-VN, uk, and similar). An untested language is not refused, just
  worse.

## Further reading

Vendor prompt guides. They describe the model, not this CLI — flag names, limits and
the machine contract above are what `capabilities` says, whatever these pages show.

- Nano Banana image generation, incl. the prompting guide and templates —
  <https://ai.google.dev/gemini-api/docs/image-generation#prompt-guide>
- "Ultimate prompting guide for Nano Banana" (formulas, creative-director technique) —
  <https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-nano-banana>
