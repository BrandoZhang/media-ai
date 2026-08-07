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
- **`<break time="1.5s" />` for an exact pause**, up to 3 seconds, where punctuation is
  not enough. Use it sparingly: a text peppered with break tags is documented to
  destabilise the model — the read speeds up, or artefacts appear. Voices trained with
  filler sounds ("uh", "ah") handle breaks differently, so a tag that works on one voice
  is not guaranteed on another. Dashes and ellipses are the lighter, less exact
  alternative.
- **Numbers are mostly handled — the exceptions are structural.** Normalisation is on by
  default, and this is the *large* model that generalises well: it reads `$1,000,000` as
  "one million dollars" where the small fast models produce "one thousand thousand
  dollars". So do not pre-spell everything. Do spell out the shapes the vendor lists as
  hard: phone numbers, addresses, URLs, times, `2024-01-01`, `Ctrl + Z`, `100km`, `TB`.
- **Break long paragraphs into sentences.** Delivery drifts across a very long
  unbroken block.
- **Pronunciation is fixed by respelling, not by markup.** SSML `<phoneme>` tags are
  documented as `eleven_flash_v2`-only and v3's `/IPA/` slashes are v3-only, so neither
  reaches this model. What works here is writing the word the way it should sound, with
  capitals, dashes or apostrophes for emphasis — *"trapezii"* → *"trapezIi"*. (The
  pronunciation-dictionary feature is a Studio/dictionary-id concept and is not
  reachable through this CLI.)
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

- **Direction written into the text gets spoken.** This is the documented behaviour of
  the emotion mechanism here: the way to convey feeling is narrative prose — *"'You're
  leaving?' she asked, her voice trembling"* — and the vendor states plainly that the
  model **still reads the delivery guide aloud**, expecting you to cut it in post. So
  anything shaped like an instruction rather than speech is at risk of being narrated.
- **Audio tags belong to the other two bindings.** `[whispers]`, `[laughs]`, `[sighs]`
  are an Eleven v3 feature, and `gemini/gemini-tts` takes the same shape; this model is
  the one of the three that does not. Combined with the point above, a v3 script moved
  here does not fail — it gets *performed as narration*, at full charge, and only
  listening catches it. Emotional direction here means casting a different voice.
- **The control mechanisms are mutually exclusive with v3, in both directions.** This
  model takes `<break>` and not audio tags; v3 takes audio tags and **not** `<break>` —
  the vendor states v3 does not support SSML break tags at all. Neither direction
  errors. Moving a script between the two means rewriting its timing, not just its
  emotion.
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
