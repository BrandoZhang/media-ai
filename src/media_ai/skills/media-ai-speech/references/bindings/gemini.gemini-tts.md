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
