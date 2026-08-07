# gemini/nano-banana-2 — notes

> Parameters and limits: `media-ai capabilities --binding gemini/nano-banana-2`.
> This file is only what that output cannot tell you.

## What it is good at

**Instruction-following on an existing image.** It edits by being *told what to change*
rather than by being given a mask, and it holds the untouched parts of the frame while
doing it. Also the widest reference budget in the set, and the only image binding here
that can pull in live facts (`--option grounding=true`).

## Prompting

**Text to image — say all five parts.** The model responds to a full sentence far
better than to a keyword pile:

> `[subject] + [action] + [location/context] + [composition] + [style]`

```bash
media-ai image generate --binding gemini/nano-banana-2 --aspect-ratio 3:2 \
  --output editorial.png \
  --prompt "A fashion model in a tailored brown dress, posing with a confident
            statuesque stance, against a seamless deep-red studio backdrop.
            Medium-full shot, centre-framed. Editorial magazine style, shot on
            medium-format film, pronounced grain, cinematic lighting."
```

**With references — state the *relationship*, not just the contents.** Each reference
needs a job in the sentence, or the model averages them:

```bash
media-ai image edit --binding gemini/nano-banana-2 \
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

**Describe presence, not absence.** "an empty street at dawn" works; "a street with no
cars" tends to produce cars. There is no `--negative-prompt` on this binding to fall
back on.

**Grounding is a separate sentence.** With `--option grounding=true` the useful shape is
*[what to look up] → [what to conclude from it] → [how to draw the conclusion]*; a
lookup with no stated visual consequence changes nothing in the picture.

## Traps

- **No `--seed`.** The same prompt twice is two different pictures, and there is no
  knob that makes a render reproducible. Keep the picture, not the recipe.
- **SynthID watermarking is unconditional**, as on every Gemini image and video path
  here. There is no flag, which is why `watermark_control` is not declared.
- **JPEG is what the model returns.** `--output x.png` is honoured — the CLI writes the
  format the filename asks for — but the re-encode happens after a lossy step, so a
  `.png` from here is not a lossless original.
- Large references cross into the Files API and are passed by URI instead of inline.
  That is automatic, but it makes the first call of a batch noticeably slower.
- `--option thinking_level` trades latency for deliberation on compositions with
  several interacting constraints; it does nothing for a simple single-subject shot.

## Further reading

Vendor prompt guides. They describe the model, not this CLI — flag names, limits and
the machine contract above are what `capabilities` says, whatever these pages show.

- Nano Banana image generation — <https://ai.google.dev/gemini-api/docs/nanobanana>
- "Ultimate prompting guide for Nano Banana" (formulas, creative-director technique) —
  <https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-nano-banana>
