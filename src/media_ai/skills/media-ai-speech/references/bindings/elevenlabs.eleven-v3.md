# elevenlabs/eleven-v3 — notes

> Parameters and limits: `media-ai capabilities --binding elevenlabs/eleven-v3`.
> This file is only what that output cannot tell you.

## What it is good at

**Scripted conversation with a real cast.** It serves `speech.dialogue` and takes far
more unique voices per script than the alternatives, so an ensemble scene is one call
rather than a per-speaker render plus a mix.

```bash
media-ai speech dialogue --binding elevenlabs/eleven-v3 \
    --speaker Ana=<voice-id> --speaker Ben=<voice-id> --speaker Cara=<voice-id> \
    --turn Ana "Did you see the numbers?" \
    --turn Ben "I did — they're up thirty percent." \
    --turn Cara "Then we ship on Friday." \
    --timestamps true --output chat.mp3
```

`--timestamps true` writes a per-character alignment sidecar with `voice_segments`,
which is what makes subtitle timing and per-speaker cutting possible without a
second pass.

## Prompting — the direction is inside the line

**Audio tags in lowercase square brackets** are this model's whole performance
language, written inline in the turn text. They cover more ground than emotion:

- **Delivery and feeling** — `[whispers]`, `[sarcastic]`, `[curious]`, `[excited]`,
  `[crying]`, `[mischievously]`
- **Non-verbal sounds** — `[laughs]`, `[laughs harder]`, `[starts laughing]`, `[sighs]`,
  `[exhales]`, `[snorts]`, `[wheezing]`
- **Environmental sound effects** — `[applause]`, `[clapping]`, `[gunshot]`,
  `[explosion]`, `[gulps]`. The model produces the *sound*, not a description of it.
- **Accent, inline** — `[strong French accent]`, `[strong Russian accent]`. Substitute
  any accent; this is the cheapest way to switch a performance mid-line.
- **Experimental** — `[sings]`, `[woo]`. Less consistent across voices; test before
  committing.

```bash
--turn Ana "[whispers] Did you see the numbers?" \
--turn Ben "[laughs] I did — [excited] they're up thirty percent!"
```

Five rules that decide whether they land:

- **The voice has to be able to do it.** A tag fights the voice's own character rather
  than overriding it — `[whispers]` on a voice sampled shouting mostly fails. Casting is
  the first parameter, not the last.
- **Give it room.** Very short text produces inconsistent delivery; it is worth pushing
  past roughly 250 characters before judging a take. A one-line test is not a test.
- **Punctuation still does the pacing.** Ellipses trail off, commas and full stops
  create natural breaths, an exclamation mark lifts to excitement.
- **Capitalisation is emphasis.** Upper case reads as raised volume or stress — useful
  in small doses, shouty in large ones.
- **`stability` is the setting that decides if tags work at all.** It controls how
  closely the read sticks to the reference voice, and the vendor is explicit that the
  stable end "reduces responsiveness to directional prompts". A script full of tags and
  a high stability value are the same request pulling in opposite directions — keep it
  low-to-mid here and accept the variability that buys.

Tags are not a closed vocabulary; unusual ones sometimes work and sometimes come out
spoken. Listen to the take before shipping a script full of them.

**Pronunciation: IPA between forward slashes**, natively, with no XML — this model
understands it across 70+ languages:

```
--turn Ana 'The term "/ˌbaɪoʊˈkemɪstri/" is what she actually said.'
```

Include stress markers (`ˈ` primary, `ˌ` secondary) and apply it only to the words that
need it. The vendor puts consistency at 80–90%, so it is a strong lever rather than a
guarantee — identical IPA can still vary between takes.

## Traps

- **No `--instruction`.** There is no dialogue-wide director note here; it is refused
  (exit 3). Direct each line inside its own `--turn` text instead.
- **`<break time="…" />` does nothing here.** v3 does not support SSML break tags at
  all — that is `elevenlabs/eleven-multilingual-v2`'s mechanism, and the two models are
  mutually exclusive in both directions (it takes breaks and not tags; this one takes
  tags and not breaks). Neither refuses the other's markup. Control pauses here with
  ellipses, `[long pause]`-style tags and line structure.
- **Voice ids are opaque and account-specific.** They are not names you can guess —
  take them from your ElevenLabs account, and note that a script written against one
  account's ids does not run on another.
- **A Professional Voice Clone is the wrong casting choice here.** The vendor states
  PVCs are not yet fully optimised for v3 and clone quality suffers relative to earlier
  models; an Instant Voice Clone or a designed voice performs better. Voices from the
  general library are also more variable on v3 than on v2, because v3 voices are built
  from a broader emotional range.
- Voice knobs travel as `--option` (`stability`, `similarity_boost`, `style`, …) and
  are gated on `constraints.options[]`. Read them from `capabilities` rather than
  assuming the set matches another ElevenLabs binding.
- Cost is per character across the whole script, cast list included where it is
  spoken. Long dialogues are the expensive case here — check `media-ai usage` after a
  batch. **Audio tags are characters too**, so a heavily annotated script costs more
  than it reads.

## Further reading

Vendor guides. They describe the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever these pages show.

- Prompting Eleven v3 (audio tags, pacing, multi-speaker) —
  <https://elevenlabs.io/docs/best-practices/prompting/eleven-v3>
- Text to dialogue — <https://elevenlabs.io/docs/overview/capabilities/text-to-dialogue>
