# elevenlabs/music-v2 — notes

> Parameters and limits: `media-ai capabilities --binding elevenlabs/music-v2`.
> This file is only what that output cannot tell you.

## What it is good at

**Brief-shaped requests.** It responds well to *what the music is for* — "ad bed for a
sneaker brand", "peaceful meditation under a voiceover" — and turns that into something
structurally sensible. Plus a credit-free planning pass, which is the real reason to
pick it: you can argue about structure before paying for audio.

## Prompting

**Prompt length is a dial between creativity and control, not a quality setting.** The
vendor is explicit that longer does not mean better — but also that *"more detailed
prompts lead to greater control and expressiveness"*. Pick the end you want:

- **Short and evocative** — *"rainy day jazz café"*, *"eerie, foreboding"* — hands the
  composition to the model. Use it when you want to be surprised.
- **Detailed and musical** — *"dissonant violin screeches over a pulsing sub-bass, 140
  BPM, in D minor"* — buys control. Use it when the track has to fit something.

Both registers work: abstract mood words and precise musical language are equally
understood. What does not work is mixing two intents that contradict each other.

**Say the intent, not just the genre.** *"Track for a high-end mascara commercial,
upbeat and polished"* gives the model structure and pacing that *"upbeat pop"* does not.

Literal syntax the model acts on:

- **`solo` before an instrument** — `solo piano in C minor`, `solo electric guitar`.
- **`a cappella` before a vocal description** — `a cappella female vocals`.
  Both of these are the vendor's stem-isolation levers; they buy you a cleaner
  single-element track, which matters here because per-track stem separation is a
  web-app feature and is not reachable through this CLI.
- **Key, BPM and tone are read as written** — `a cappella vocals in A major, 90 BPM,
  soulful and raw`.
- **Vocal delivery takes descriptors** — `raw`, `live`, `breathy`, `glitching`,
  `aggressive` — and arrangements can be directed: `two singers harmonizing in C`.
- **Timing cues place the vocal** — `lyrics begin at 15 seconds`,
  `instrumental only after 1:45`.

**You can supply the lyrics.** Paste the verse into the prompt and the model sets it
against the requested length rather than writing its own.

**For anything with sections, plan first.** `music plan` spends no credits and returns
editable JSON; fix the section list, lengths and per-section styles there rather than
trying to describe a whole arrangement in one prompt:

```bash
media-ai music plan --prompt "upbeat indie-pop, verse/chorus/bridge, ~2 min" \
    --duration-ms 120000 --output plan.json
# edit plan.json — section names, durations, per-section styling
media-ai music generate --plan plan.json --seed 42 --output song.mp3
```

- Want the plan's section lengths honoured literally?
  `--option respect_sections_durations=true`.

## Traps

- **It sings by default.** Most prompts produce vocals unless told otherwise, so
  "background music for a product video" comes back with someone singing over it. Say
  **`instrumental only`** in the prompt — the vendor's documented phrase — and/or set
  `--option force_instrumental=true`. This is the single most common surprise here.
- **`--seed` is plan mode only.** In prompt mode there is nothing to pin, so a prompt you
  liked is not re-renderable. If a take matters, keep the audio — or move to a plan.
- **`--duration-ms` is prompt mode only.** In plan mode the length comes from the plan's
  sections; passing both is a contradiction the plan wins.
- **`--detailed true` costs nothing extra to read but changes the call** — it uses the
  multipart endpoint and writes a `<output>.metadata.json` sidecar carrying the
  composition plan actually used. That sidecar is the fastest way to find out why a
  track came out the way it did, and it is a second entry in `artifacts[]`.
- Lyrics are not a separate field — they go in the prompt text or the plan. Supplying
  them is well supported (and multilingual), but nothing *guarantees* a given line is
  sung as written; check the take, or use a plan when the words matter.
- **Omitting `--duration-ms` is a real mode**, not a missing argument: the model picks a
  length and writes lyrics to fit it. State the length only when something downstream
  needs it.
- Cost is metered in the `speech_characters` ledger field like other audio ops, so a
  batch of long tracks shows up where you might not look for it — check `media-ai usage`.

## Further reading

Vendor guides. They describe the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever these pages show.

- Music best practices (prompt structure, isolation syntax, timing cues) —
  <https://elevenlabs.io/docs/overview/capabilities/music/best-practices>
- Composition plans — section structure, global vs local styles, lyric formatting —
  <https://elevenlabs.io/docs/eleven-api/guides/how-to/music/composition-plans>
