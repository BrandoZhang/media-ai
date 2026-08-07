# gemini/veo-3.1 — notes

> Parameters and limits: `media-ai capabilities --binding gemini/veo-3.1`.
> This file is only what that output cannot tell you.

## What it is good at

Photoreal motion and natural lighting; strong first-frame adherence. Audio is native
to Veo 3.x rather than a separate pass.

## Prompting

**Direct the shot; do not describe the edit.** One continuous take reads better than a
list of cuts. The order that works:

> `[cinematography] + [subject] + [action] + [context] + [style & ambiance]`

```bash
media-ai video generate --binding gemini/veo-3.1 --aspect-ratio 16:9 \
  --resolution 720p --output office.mp4 \
  --prompt "Medium shot, a tired corporate worker rubbing his temples in exhaustion,
            in front of a bulky 1980s computer in a cluttered office late at night.
            Lit by harsh fluorescent overheads and the green glow of the monitor.
            Retro aesthetic, shot as if on 1980s colour film, slightly grainy."
```

**Full sentences beat comma-separated tags here.** Cause and effect — *"the wind picks
up, so the curtain lifts"* — is read as motion; a pile of adjectives is read as a
still.

**Audio is written into the prompt, in three distinct forms:**

- **Dialogue in quotes** — `A woman says, "We have to leave now."` Quoting is what
  drives the lip movement; narration about speech does not.
- **`SFX:` for effects** — `SFX: thunder cracks in the distance`.
- **`Ambient noise:` for the bed** — `Ambient noise: the quiet hum of a starship bridge`.

**Timestamp prompting choreographs one generation.** Assign actions to timed segments
inside a single prompt rather than generating and concatenating:

```
[00:00-00:02] Medium shot from behind the explorer pushing a jungle vine aside.
[00:02-00:04] Reverse shot, her face, awe-struck.
[00:04-00:06] Tracking shot following her hand across the stone carvings.
[00:06-00:08] Wide crane shot revealing the temple complex.
```

Keep the segments inside the clip's actual duration — segments past the end are simply
not rendered, and nothing warns you.

**Camera vocabulary is literal**: dolly in, tracking shot, crane shot, whip pan, POV,
low angle, two-shot, shallow depth of field, macro. These land; "cinematic" does not.

**Audio comes with the generation.** The explicit `--audio` flag is unreliable on the
Developer API — expect sound whether or not you ask, and do not treat its absence as a
failed request.

## Traps

- **`--continue-from` takes a URI of a clip this API produced.** A local file is
  refused outright. Extension is also duration-locked — the constraint block says
  which value.
- **Jobs cannot be cancelled** on the Developer API. `media-ai job cancel` exits 3.
  Submitting is committing; think before `--wait false` on an expensive request.
- **SynthID watermarking is unconditional.** There is no flag to disable it, and
  `watermark_control` is declared false for exactly that reason.
- Several combinations (extension, reference images, 1080p, 4K) force a specific
  duration. If a request is refused on duration, that is usually why.

## Further reading

Vendor prompt guides. They describe the model, not this CLI — flag names, limits and
the machine contract above are what `capabilities` says, whatever these pages show.

- Video generation with Veo — <https://ai.google.dev/gemini-api/docs/video>
- "Ultimate prompting guide for Veo 3.1" (formula, timestamp prompting, workflows) —
  <https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1>
