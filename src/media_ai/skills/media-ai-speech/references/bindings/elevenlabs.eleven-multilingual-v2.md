# elevenlabs/eleven-multilingual-v2 — notes

> Parameters and limits: `media-ai capabilities --binding elevenlabs/eleven-multilingual-v2`.
> This file is only what that output cannot tell you.

## What it is good at

**Long, steady narration in one voice.** Consistent timbre across a long read, a wide
language range from a single voice, and a `--timestamps` sidecar for subtitles. It is
the workhorse read, not the performance.

## Prompting

There is no director's note on this binding — **delivery is controlled by how the text
is written**.

- **Punctuation is the pacing.** Commas give a short break, full stops a real one,
  ellipses a trail-off, exclamation marks lift the energy. Prose already punctuated for
  reading aloud needs almost nothing else.
- **`<break time="1.5s" />` for an exact pause**, where punctuation is not enough. Use
  it sparingly: a text peppered with break tags is documented to destabilise the
  model — the read speeds up, or artefacts appear.
- **Spell out anything that is not a word.** `2.0` → "two point oh", `$14.99` → "fourteen
  ninety-nine", `1998` → "nineteen ninety-eight". The normalisation options exist
  (`apply_text_normalization`, `apply_language_text_normalization`) but writing it out
  is what removes the ambiguity.
- **Break long paragraphs into sentences.** Delivery drifts across a very long
  unbroken block.
- **Chunking a chapter? Pass the neighbours.** `--option previous_text=…` and
  `next_text=…` carry prosody across a split so consecutive files do not sound like
  separate takes.

```bash
media-ai speech generate --binding elevenlabs/eleven-multilingual-v2 \
    --text "It arrived on a Tuesday... unmarked, and heavier than it looked.
            <break time=\"1s\" /> Nobody signed for it." \
    --voice <voice-id> --option stability=0.5 similarity_boost=0.75 \
    --timestamps true --output ch1.mp3
```

## Traps

- **Eleven v3 audio tags do not work here.** `[whispers]`, `[laughs]`, `[sighs]` are a v3
  feature; this model speaks them or mangles them, and there is no error — you get a
  charged, quietly wrong take. Emotional direction on this binding means choosing a
  different voice, not annotating the text. For tagged performance use
  `elevenlabs/eleven-v3`.
- **One voice, no dialogue.** It declares `dialogue = false`; a cast script is refused
  (exit 3) rather than flattened into a single narrator.
- **Some formats are plan-gated.** `mp3_44100_192` needs Creator tier or above, and
  44.1 kHz PCM/WAV needs Pro or above. A refusal here is a billing fact, not a bad flag.
- `stability` low makes the read more expressive *and* less repeatable; for a long book
  keep it mid and let the writing carry the variation.
- Cost is per character, and the `<break>` tags count. Long silences are not free.

## Further reading

Vendor guides. They describe the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever these pages show.

- Text to speech best practices — <https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices>
- Text to speech overview — <https://elevenlabs.io/docs/overview/capabilities/text-to-speech>
