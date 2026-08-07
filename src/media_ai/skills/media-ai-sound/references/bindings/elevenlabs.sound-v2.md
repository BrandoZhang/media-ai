# elevenlabs/sound-v2 — notes

> Parameters and limits: `media-ai capabilities --binding elevenlabs/sound-v2`.
> This file is only what that output cannot tell you.

## What it is good at

**One sound, described physically.** Foley, impacts, whooshes, UI clicks, ambience —
and seamless loops, which is the hard part to fake by trimming.

## Prompting

**Aim for roughly 10–60 words.** Under that you get a generic stock sound; over it the
description starts contradicting itself and quality does not improve.

Say the physics, not the vibe. Four things carry most of the result:

- **Material and size** — *"a heavy oak door"*, not *"a door"*.
- **Acoustic space** — *"in a small tiled bathroom"* vs *"in a stone cathedral"*. This is
  the single biggest lever on whether it sits in a mix.
- **Distance and perspective** — *"close-miked"*, *"heard from across the street"*.
- **How it evolves in time** — *"starts as a low rumble and builds to a sharp crack"*.

**Onomatopoeia alongside the description helps**, not instead of it: *"a wet squelch —
schlop — as the boot pulls free of deep mud"*.

**One event per call.** A sequence — *"footsteps, then a door, then a scream"* — comes back
as a muddle. Generate the parts separately and assemble them; `media-ai` gives you the
files, an editor gives you the timeline.

```bash
# a placed, evolving one-shot
media-ai sound generate --binding elevenlabs/sound-v2 \
    --text "A heavy oak door in a stone hallway: slow groaning creak as it swings
            open, a beat of silence, then a deep resonant slam with a long tail." \
    --duration-seconds 6 --output door.mp3

# a loop — describe a steady texture, not an event
media-ai sound generate --binding elevenlabs/sound-v2 \
    --text "Steady gentle rain on a tin roof, close, no thunder, no wind gusts." \
    --duration-seconds 10 --option loop=true prompt_influence=0.7 --output rain.mp3
```

## Traps

- **No `--seed`, at all.** Nothing here is reproducible — the same text twice gives two
  different sounds. Keep the file; there is no recipe to keep.
- **A loop needs a *texture*, not an *event*.** `--option loop=true` seams the ends
  together; if the prompt describes something with a beginning and an end (a slam, a
  whoosh) the seam is audible. Describe a continuous state instead.
- **`prompt_influence` toward `1` sticks to the text**, toward `0` invents. Raising it is
  the fix for "it ignored half my description"; lowering it is the fix for "every take
  sounds the same".
- **Duration is bounded and short.** A request outside the declared bounds is exit 3
  before the call; omit `--duration-seconds` entirely and the model picks a length that
  fits the description, which is usually the better default for a one-shot.
- Cost is metered in the `speech_characters` ledger field like other audio ops — a
  library built from hundreds of small calls adds up quietly. Check `media-ai usage`.

## Further reading

Vendor guides. They describe the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever these pages show.

- Sound effects capability (prompt length, prompt influence) —
  <https://elevenlabs.io/docs/overview/capabilities/sound-effects>
- Sound effects product guide — <https://elevenlabs.io/docs/eleven-creative/playground/sound-effects>
