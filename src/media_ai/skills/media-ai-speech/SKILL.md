---
name: media-ai-speech
description: >-
  Turn text into spoken audio via the media-ai CLI — single-voice text-to-speech
  and multi-voice dialogue — across ElevenLabs and Google Gemini TTS. Handles voice
  selection, output codec/format, per-character timestamp sidecars, and the
  speaker/cast dialogue model. Use when asked to narrate, voice, read aloud, or
  synthesize speech / a voiceover / a conversation / a podcast-style dialogue from
  text on the command line.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai capabilities --binding elevenlabs/eleven-multilingual-v2 --pretty"
  install:
    tier: optional
    summary: >-
      Read text aloud — a single narrator or a multi-voice conversation — with
      ElevenLabs voices or Gemini TTS, plus per-character timestamp sidecars for
      subtitles.
---

# media-ai-speech — text → speech

> **Read `../media-ai-shared/SKILL.md` first** for the machine contract (one JSON
> object on stdout, exit-code categories), provider selection, and credentials.
> Speech is part of the **`audio`** modality and is **synchronous** on every real
> provider — no `job` polling.

Two operations under `media-ai speech`:

1. **`speech generate`** — one voice reads `--text` → an audio file.
2. **`speech dialogue`** — several voices act out a scripted conversation → one file.

Real backends:

| provider | ops | voices | key differences |
|---|---|---|---|
| `elevenlabs` | generate, dialogue | up to **10** unique voices/dialogue | rich voice knobs (`stability`, `style`, …), `--seed`, `--output-format`, `--timestamps`; **no** `--instruction` |
| `gemini` (TTS) | generate, dialogue | up to **2** speakers/dialogue | style/tone/accent **directed in the prompt text**, named voices, global `--instruction`; **WAV only** (no format/seed/timestamps) |

The offline `mock` default synthesizes tones so the path runs credential-free.

## Discover first

Voice ids, output formats, per-request character budgets, and dialogue limits differ
sharply per provider/model. Check before you submit:

```bash
media-ai capabilities --binding elevenlabs/eleven-multilingual-v2 --pretty
media-ai capabilities --binding gemini/gemini-2.5-flash-preview-tts --pretty
```

Read `audio.operations`, `output_formats`, `default_voice`, `supports_seed` /
`supports_language_code` / `supports_timestamps`, `supports_dialogue` /
`max_dialogue_voices` / `supports_instruction`, `max_characters`, and `options[]`.
An unsupported field fails with **exit 3** *before any network call*.

## `speech generate` — core flags

| flag | meaning |
|---|---|
| `--text` (required) | the text to speak |
| `--output` (required) | output audio path (extension is a hint; codec follows `--output-format`) |
| `--voice ID` | provider voice id / name; falls back to the model default |
| `--output-format FMT` | codec_samplerate_bitrate, e.g. `mp3_44100_128` (ElevenLabs; Gemini is WAV-only) |
| `--language-code ISO` | ISO 639-1 hint (ignored by some models — see notes) |
| `--seed N` | reproducibility where supported (ElevenLabs) |
| `--timestamps true` | also write a `<output>.timestamps.json` per-character alignment sidecar (extra artifact) |
| `--option key=value` | provider-specific voice knobs, capability-gated |

```bash
# ElevenLabs single voice, tuned
media-ai speech generate --binding elevenlabs/eleven-multilingual-v2 \
    --text "Welcome aboard. Please fasten your seatbelt." \
    --voice JBFqnCBsd6RMkjVDRZzb --output-format mp3_44100_128 \
    --option stability=0.4 similarity_boost=0.8 style=0.2 \
    --output welcome.mp3

# Gemini TTS — direct the delivery in the prompt; pick a named voice
media-ai speech generate --binding gemini/gemini-2.5-flash-preview-tts \
    --text "Say cheerfully: We did it — the launch is live!" \
    --voice Kore --output launch.wav
```

## `speech dialogue` — the cast model

One representation serves both providers: a **cast** (speaker name → voice) plus
ordered **turns** (which speaker says what).

| flag | meaning |
|---|---|
| `--speaker NAME=VOICE` | cast entry; repeatable |
| `--turn NAME TEXT` | one line by a cast speaker; repeatable, order preserved |
| `--instruction TEXT` | global director note (**Gemini/mock only**; ElevenLabs rejects it) |
| `--script FILE.json` | `{"cast":{NAME:VOICE},"turns":[{"speaker":..,"text":..}],"instruction"?}` **or** a flat `[{"speaker":..,"voice":..,"text":..}]` list |
| `--output` (required), `--output-format`, `--language-code`, `--seed`, `--timestamps`, `--option` | as for `generate` |

```bash
# ElevenLabs multi-voice (≤10 voices)
media-ai speech dialogue --binding elevenlabs/eleven-v3 \
    --speaker Ana=EXAVITQu4vr4xnSDxMaL --speaker Ben=JBFqnCBsd6RMkjVDRZzb \
    --turn Ana "Did you see the numbers?" \
    --turn Ben "I did — they're up thirty percent." \
    --timestamps true --output chat.mp3

# Gemini TTS dialogue (≤2 speakers) with a global director note
media-ai speech dialogue --binding gemini/gemini-2.5-flash-preview-tts \
    --speaker Host=Kore --speaker Guest=Puck \
    --instruction "Warm late-night radio tone, unhurried" \
    --turn Host "Thanks for coming on." \
    --turn Guest "Happy to be here." \
    --output show.wav
```

## Gotchas

- **Gemini TTS is WAV-only** and ignores `--output-format` / `--seed` / `--timestamps`;
  express delivery (accent, pace, whispering, `[laughs]`) **in the prompt text**.
- **ElevenLabs has no `--instruction`** (dialogue-wide direction is Gemini-only).
- Keep total dialogue text within the model's `max_characters` (ElevenLabs ≈ 2000).
- On **Gemini TTS**, a 200-OK with **no audio** (silent safety drop) surfaces as a
  `safety` error (**exit 8**), not an empty file.
- `--timestamps true` adds a **second artifact** (the sidecar); dialogue sidecars also
  carry `voice_segments`. Cost is metered in `speech_characters` — see `media-ai-usage`.

## References

- `../media-ai-shared/references/bindings.md` — per-provider voice/model/format matrix, option keys, tier notes.
- `references/dialogue.md` — the cast/turns/`--script` model in full, with the JSON shapes.
