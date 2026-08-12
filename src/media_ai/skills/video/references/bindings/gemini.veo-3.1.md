# gemini/veo-3.1 — notes

> Parameters and limits: `{{cli}} capabilities --binding gemini/veo-3.1`.
> This file is only what that output cannot tell you.

## What it is good at

Photoreal motion and natural lighting; strong first-frame adherence. Audio is native
to Veo 3.x rather than a separate pass.

## Prompting

**Direct the shot; do not describe the edit.** One continuous take reads better than a
list of cuts. The order that works:

> `[cinematography] + [subject] + [action] + [context] + [style & ambiance]`

```bash
{{cli}} video generate --binding gemini/veo-3.1 --aspect-ratio 16:9 \
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

**This is a Veo feature, not a video-prompting convention.** Seedance's vendor guide
states the opposite for its own models — explicit timings destabilise the generation
there, and a shot list is the supported form. A prompt does not port between the two.

**Camera vocabulary is literal**: dolly in, tracking shot, crane shot, whip pan, POV,
low angle, two-shot, shallow depth of field, macro. These land; "cinematic" does not.

**For faces, say "portrait".** The vendor's own tip: naming the shot type is what makes
the model spend detail on facial features rather than on the scene around them.

**References preserve a subject, and there are at most three of them.** They are asset
references — one person, character or product carried into the clip — not style plates
and not a storyboard. Describe the scene fully in the prompt; the references only hold
the identity.

**Audio comes with the generation.** The explicit `--audio` flag is unreliable on the
Developer API — expect sound whether or not you ask, and do not treat its absence as a
failed request.

## Traps

- **`--continue-from` takes a URI of a clip this API produced.** A local file is
  refused outright. Extension is also duration-locked — the constraint block says
  which value — and **720p only**, so a 1080p or 4K clip cannot be continued.
- **Extension grows the clip by ~7 seconds per call**, and the result is the input plus
  the new footage as one file, not the new part alone. It can be repeated up to about
  twenty times.
- **Audio does not carry across an extension unless it is present in the final second**
  of the input. A clip whose dialogue ends early extends silent, and nothing warns you —
  plan the hand-off so speech runs up to the cut.
- **Generated videos are deleted from the server after two days.** That is fine for
  `--wait true`, which downloads immediately, but it is a real deadline for
  `--wait false`: a `job query` run three days later has nothing to fetch. Extending a
  clip resets its two-day clock, so a chain of extensions stays alive while you work.
- **Jobs cannot be cancelled** on the Developer API. `{{cli}} job cancel` exits 3.
  Submitting is committing; think before `--wait false` on an expensive request. Budget
  the wait itself at anywhere from ~11 seconds to ~6 minutes at peak.
- **A generation blocked over audio is not billed.** Veo sometimes refuses a clip
  because of the audio track rather than the picture — a safety filter or a processing
  fault. The vendor states you are not charged for a blocked generation, so this is one
  failure worth simply retrying with reworded dialogue.
- **`--option person_generation` is constrained by scene and by region**, and the
  allowed value is not the same across them: text-to-video and extension take one
  setting, while image-to-video, keyframes and reference images take another. In the EU,
  UK, Switzerland and MENA only the adult setting is available at all. A refusal here is
  policy, not a typo.
- **SynthID watermarking is unconditional.** There is no flag to disable it, and
  `watermark_control` is declared false for exactly that reason.
- Several combinations (extension, reference images, 1080p, 4K) force a specific
  duration. If a request is refused on duration, that is usually why.
- **`--seed` exists but is not determinism.** The vendor describes it as improving
  repeatability slightly, not guaranteeing it. Two runs on one seed are similar, not
  identical — do not build a pipeline that assumes otherwise.
- **One video in, one video out.** Reasoning across several input clips is unsupported
  and degrades the result rather than erroring, so do not pass a second clip hoping it
  will be compared or combined.
- **Only English is evaluated.** Other prompt languages may work but are untested, and
  quality varies without any signal that the language is the cause.

## Further reading

Vendor prompt guides. They describe the model, not this CLI — flag names, limits and
the machine contract above are what `capabilities` says, whatever these pages show.

- Veo API reference and prompt guide — <https://ai.google.dev/gemini-api/docs/veo>
- "Ultimate prompting guide for Veo 3.1" (formula, timestamp prompting, workflows) —
  <https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1>
