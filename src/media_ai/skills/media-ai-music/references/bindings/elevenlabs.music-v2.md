# elevenlabs/music-v2 — notes

> Parameters and limits: `media-ai capabilities --binding elevenlabs/music-v2`.
> This file is only what that output cannot tell you.

## What it is good at

**Brief-shaped requests.** It responds well to *what the music is for* — "ad bed for a
sneaker brand", "peaceful meditation under a voiceover" — and turns that into something
structurally sensible. Plus a credit-free planning pass, which is the real reason to
pick it: you can argue about structure before paying for audio.

## Prompting

**Short beats long.** A focused phrase — *"rainy day jazz café"* — reliably outperforms a
paragraph, because a paragraph almost always contains two instructions that fight. Say
the genre and mood first, then add at most a few layers.

> `[genre + mood] → [instrumentation] → [key / tempo] → [use or setting]`

Three pieces of literal syntax the model acts on:

- **`solo` before an instrument** isolates it — `solo piano in C minor`, `solo electric
  guitar`. Without it you tend to get a full arrangement around the named instrument.
- **`a cappella` before a vocal description** isolates voices — `a cappella female
  vocals`.
- **Key, BPM and tone are read as written** — `a cappella vocals in A major, 90 BPM,
  soulful and raw`.

**For anything with sections, plan first.** `music plan` spends no credits and returns
editable JSON; fix the section list, lengths and per-section styles there rather than
trying to describe a whole arrangement in one prompt:

```bash
media-ai music plan --prompt "upbeat indie-pop, verse/chorus/bridge, ~2 min" \
    --duration-ms 120000 --output plan.json
# edit plan.json — section names, durations, per-section styling
media-ai music generate --plan plan.json --seed 42 --output song.mp3
```

- Want no vocals? `--option force_instrumental=true` is more reliable than writing
  "instrumental" in the prompt.
- Want the plan's section lengths honoured literally?
  `--option respect_sections_durations=true`.

## Traps

- **`--seed` is plan mode only.** In prompt mode there is nothing to pin, so a prompt you
  liked is not re-renderable. If a take matters, keep the audio — or move to a plan.
- **`--duration-ms` is prompt mode only.** In plan mode the length comes from the plan's
  sections; passing both is a contradiction the plan wins.
- **`--detailed true` costs nothing extra to read but changes the call** — it uses the
  multipart endpoint and writes a `<output>.metadata.json` sidecar carrying the
  composition plan actually used. That sidecar is the fastest way to find out why a
  track came out the way it did, and it is a second entry in `artifacts[]`.
- Lyrics are not a separate field. Vocal content is described in the prompt or placed
  in the plan; there is no flag that guarantees a given line is sung.
- Cost is metered in the `speech_characters` ledger field like other audio ops, so a
  batch of long tracks shows up where you might not look for it — check `media-ai usage`.

## Further reading

Vendor guides. They describe the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever these pages show.

- Music best practices (prompt structure, isolation syntax) —
  <https://elevenlabs.io/docs/overview/capabilities/music/best-practices>
- Music overview — <https://elevenlabs.io/docs/eleven-creative/products/music>
