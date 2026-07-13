# `speech dialogue` — the cast model in full

Multi-voice speech is expressed once and mapped per provider: ElevenLabs stitches
`(voice, text)` turns; Gemini takes one prompt plus a `speaker → voice` cast (≤2).
The CLI hides that split behind a single **cast + turns** model.

## Three ways to supply it

**1. Flags** — a cast, then ordered turns:

```bash
media-ai speech dialogue --provider elevenlabs \
    --speaker Ana=EXAVITQu4vr4xnSDxMaL \
    --speaker Ben=JBFqnCBsd6RMkjVDRZzb \
    --turn Ana "Ready for the demo?" \
    --turn Ben "Born ready." \
    --output scene.mp3
```

- `--speaker NAME=VOICE` — repeatable; NAME is a label you reuse in turns, VOICE is a
  provider voice id/name.
- `--turn NAME TEXT` — repeatable; **order is preserved**. NAME must be in the cast.

**2. A script file** — `--script dialogue.json`, in either shape:

```json
{
  "cast": {"Ana": "EXAVITQu4vr4xnSDxMaL", "Ben": "JBFqnCBsd6RMkjVDRZzb"},
  "turns": [
    {"speaker": "Ana", "text": "Ready for the demo?"},
    {"speaker": "Ben", "text": "Born ready."}
  ],
  "instruction": "Playful, quick banter"
}
```

or a flat list (cast is inferred from the turns):

```json
[
  {"speaker": "Ana", "voice": "EXAVITQu4vr4xnSDxMaL", "text": "Ready for the demo?"},
  {"speaker": "Ben", "voice": "JBFqnCBsd6RMkjVDRZzb", "text": "Born ready."}
]
```

**3. Both** — flags override / extend the script (handy to swap one voice).

## `--instruction` (global director note)

A single scene-wide direction (tone, pacing, energy). **Gemini and mock honor it;
ElevenLabs does not** (`supports_instruction: false` → passing it fails with exit 3
unless `--on-unsupported warn|ignore`). Check `audio.supports_instruction` first.

## Provider limits

| | ElevenLabs | Gemini TTS |
|---|---|---|
| max unique voices | 10 | 2 |
| `--instruction` | ✗ | ✓ |
| `--timestamps` | ✓ (`voice_segments` in the sidecar) | ✗ |
| output | codec via `--output-format` | WAV only |

Keep the combined turn text within the model's `max_characters` (ElevenLabs ≈ 2000).

## Output

One audio artifact for the whole conversation. With `--timestamps true` a second
artifact — `<output>.timestamps.json` — carries per-character alignment plus
`voice_segments` marking who speaks when.
