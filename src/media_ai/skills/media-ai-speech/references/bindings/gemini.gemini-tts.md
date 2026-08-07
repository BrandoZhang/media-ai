# gemini/gemini-tts — notes

> Parameters and limits: `media-ai capabilities --binding gemini/gemini-tts`.
> This file is only what that output cannot tell you.

## What it is good at

**Performance direction written in plain language.** Tone, accent, pace, emotion and
stage business go into the text you send, not into flags:

```bash
media-ai speech generate --binding gemini/gemini-tts \
    --text "Say cheerfully: We did it — the launch is live!" \
    --voice <voice-name> --output launch.wav
```

It also takes a dialogue-wide `--instruction`, which is the one place a note applies
to the whole performance rather than a line:

```bash
media-ai speech dialogue --binding gemini/gemini-tts \
    --speaker Host=<voice> --speaker Guest=<voice> \
    --instruction "Warm late-night radio tone, unhurried" \
    --turn Host "Thanks for coming on." \
    --turn Guest "Happy to be here." \
    --output show.wav
```

Voices are *named* rather than opaque ids, so a voice can be picked from its name
without a lookup call.

## Prompting — two levels of direction

**Whole-passage instruction, in plain English.** A leading clause sets tone, accent,
pace and mood for everything that follows: *"Read this as a weary night-shift dispatcher,
slow and flat:"*, *"Deliver this warmly, with a slight Irish accent:"*. Style, accent,
emotion and speed all belong here — none of them is a flag.

**Word-level markup, for the places that need it.** The API also reads inline tags —
`<emotion>`, `<pace>`, `<emphasis>`, `<pause>` — which scope to the words they wrap
rather than the passage:

```bash
media-ai speech generate --binding gemini/gemini-tts \
    --text "Read this as a calm museum guide: The vault held <emphasis>one</emphasis>
            object. <pause /> Nobody has seen it since." \
    --voice <voice-name> --output guide.wav
```

Use the instruction for the performance and the tags for the exceptions. A passage
carrying a tag on every clause tends to come out mechanical — and note the markup is
not universal across TTS bindings, so a script written this way does not port to
ElevenLabs.

## Traps

- **WAV only.** `--output-format` has nothing to select; name the output `.wav` or you
  get WAV bytes in a file called `.mp3`. There is no `--seed` and no `--timestamps`
  sidecar either — for subtitles you need a binding that reports
  `supports.timestamps`.
- **The dialogue cast is small.** Check `constraints.audio.max_dialogue_voices` before
  writing a script with a third character; exceeding it is refused before the call.
- **A silent drop is an exit-8 safety error.** The API can return 200 with no audio
  part; that surfaces as a `safety` failure rather than a zero-byte file, so do not
  retry it unchanged — change the text.
- Prompt-level direction means the direction is *also* charged as characters. A long
  stage note costs the same as dialogue.
- **The direction can be read aloud.** Because the note lives in the text, an unusual
  phrasing occasionally comes back spoken instead of performed. Keep instructions to a
  short leading clause ending in a colon, and listen to the first take.

## Further reading

Vendor guide. It describes the model, not this CLI — flag names, limits and the machine
contract above are what `capabilities` says, whatever that page shows.

- Speech generation (style control, inline markup, voice names) —
  <https://ai.google.dev/gemini-api/docs/speech-generation>
