# elevenlabs/sound-v2 — notes

> Parameters and limits: `{{cli}} capabilities --binding elevenlabs/sound-v2`.
> This file is only what that output cannot tell you.

## What it is good at

**One sound, described physically.** Foley, impacts, whooshes, UI clicks, ambience —
and seamless loops, which is the hard part to fake by trimming.

## Prompting

**Clear and concise wins.** The vendor's own examples are short — *"Glass shattering on
concrete"*, *"Heavy wooden door creaking open"*, *"Thunder rumbling in the distance"*.
Reach for detail when a specific physical result matters, not by default.

Say the physics, not the vibe. Four things carry most of the result:

- **Material and size** — *"a heavy oak door"*, not *"a door"*.
- **Acoustic space** — *"in a small tiled bathroom"* vs *"in a stone cathedral"*. This is
  the single biggest lever on whether it sits in a mix.
- **Distance and perspective** — *"close-miked"*, *"heard from across the street"*.
- **How it evolves in time** — *"starts as a low rumble and builds to a sharp crack"*.

**Sequences are a supported shape, not a workaround.** Chain events with *"then"* or
*"followed by"* and it renders them in order: *"Footsteps on gravel, then a metallic door
opens"*, *"Sword being drawn, then clashing with another blade"*. Split into separate
calls when you need each element on its own track for mixing — not because one call
cannot hold a sequence.

**It knows the trade vocabulary**, and using it is shorter and more precise than
describing the sound longhand:

| term | what it gets you |
|---|---|
| `impact` | collision/contact, from a tap to a crash |
| `whoosh` | movement through air |
| `ambience` | background environment that establishes a space |
| `braam` | the big brassy cinematic trailer hit |
| `drone` | continuous textured bed, for suspense |
| `glitch` | malfunction and jitter, for transitions and sci-fi |
| `one-shot` / `loop` / `stem` | single hit / repeating segment / isolated element |

**It also makes musical parts.** Drum loops, bass lines and melodic samples are in
scope — *"90s hip-hop drum loop, 90 BPM"*, *"vintage brass stabs in F minor"*,
*"atmospheric synth pad with subtle modulation"*. For an actual piece of music use
`{{cli}} music` instead; this binding is for the loop, not the track.

```bash
# a placed sequence in one call
{{cli}} sound generate --binding elevenlabs/sound-v2 \
    --text "A heavy oak door in a stone hallway: slow groaning creak as it swings
            open, then a deep resonant slam with a long tail." \
    --duration-seconds 6 --output door.mp3

# a loop — describe a steady texture, not an event
{{cli}} sound generate --binding elevenlabs/sound-v2 \
    --text "Steady gentle rain on a tin roof, close, no thunder, no wind gusts." \
    --duration-seconds 10 --option loop=true prompt_influence=0.7 --output rain.mp3
```

## Traps

- **No `--seed`, at all.** Nothing here is reproducible — the same text twice gives two
  different sounds. Keep the file; there is no recipe to keep.
- **A loop needs a *texture*, not an *event*.** `--option loop=true` produces audio with
  no perceptible start or end, which is what atmospheres, ambient beds and background
  textures want; a prompt describing something that begins and finishes (a slam, a
  whoosh) leaves an audible seam. The intended use is generating one bounded clip —
  30 seconds of soft rain — and repeating it indefinitely downstream, which is how you
  get a bed longer than the model's ceiling.
- **Looping costs you the high-quality format.** The vendor documents WAV/48 kHz output
  for non-looping effects only; a looping request is MP3 territory. If a project needs
  both an uncompressed master and a seamless bed, those are two different renders.
- **`prompt_influence` toward `1` sticks to the text**, toward `0` invents. Raising it is
  the fix for "it ignored half my description"; lowering it is the fix for "every take
  sounds the same".
- **Duration is bounded and short.** A request outside the declared bounds is exit 3
  before the call; omit `--duration-seconds` entirely and the model picks a length that
  fits the description, which is usually the better default for a one-shot. Specifying a
  duration also moves the call onto per-second billing, so "let it decide" is the cheaper
  habit while you are still iterating on wording.
- Cost is metered in the `speech_characters` ledger field like other audio ops — a
  library built from hundreds of small calls adds up quietly. Check `{{cli}} usage`.

## Further reading

Vendor guides. They describe the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever these pages show.

- Sound effects capability (prompt length, prompt influence) —
  <https://elevenlabs.io/docs/overview/capabilities/sound-effects>
- Sound effects product guide — <https://elevenlabs.io/docs/eleven-creative/playground/sound-effects>
