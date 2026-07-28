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

## Traps

- **No `--instruction`.** There is no dialogue-wide director note here; it is refused
  (exit 3). Direct each line inside its own `--turn` text instead.
- **Voice ids are opaque and account-specific.** They are not names you can guess —
  take them from your ElevenLabs account, and note that a script written against one
  account's ids does not run on another.
- Voice knobs travel as `--option` (`stability`, `similarity_boost`, `style`, …) and
  are gated on `constraints.options[]`. Read them from `capabilities` rather than
  assuming the set matches another ElevenLabs binding.
- Cost is per character across the whole script, cast list included where it is
  spoken. Long dialogues are the expensive case here — check `media-ai usage` after a
  batch.
