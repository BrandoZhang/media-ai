# openai/gpt-image-2 — notes

> Parameters and limits: `media-ai capabilities --binding openai/gpt-image-2`.
> This file is only what that output cannot tell you.

## What it is good at

**Legible in-image typography and believable material/lighting.** The dependable pick
when the picture has to carry words — a UI mock, a poster, a label, an infographic —
and when an edit has to keep a face or a product recognisably the same object.

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
media-ai image generate --binding openai/gpt-image-2 --size 1024x1536 \
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

## Traps

- **No `--seed`.** Reproducibility comes from pinning the prompt text and the reference,
  not from a seed value.
- **No transparent background on this tier.** `--background transparent` is refused
  (exit 3); a cut-out has to be keyed afterwards.
- **Sizes are constrained arithmetic, not a menu.** Both edges divisible by 16, longest
  edge within the declared maximum, and edges no more than 3:1 apart. An odd request is
  refused before the call — read `constraints.geometry` rather than guessing.
- **References are always processed at high fidelity**; the API's `input_fidelity` knob
  is rejected here, so there is no cheaper low-fidelity edit to fall back on.
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
