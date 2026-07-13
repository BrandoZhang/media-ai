# Speech provider / voice matrix

`media-ai capabilities --provider <p>` is authoritative and live; this maps the field
values you'll see. All speech is **synchronous** (`audio` modality).

## ElevenLabs (`elevenlabs`)

Auth: `ELEVENLABS_API_KEY` (header `xi-api-key`). Base URL configurable via
`ELEVENLABS_BASE_URL` / profile `base_url` (regional residency endpoints).

| aspect | value |
|---|---|
| ops | `speech.generate` (`POST /v1/text-to-speech/{voice_id}`), `speech.dialogue` (`POST /v1/text-to-dialogue`) |
| models | `eleven_multilingual_v2` (TTS default), `eleven_turbo_v2_5`, `eleven_flash_v2_5`, `eleven_v3` (dialogue default) |
| default voice | `JBFqnCBsd6RMkjVDRZzb` (override with `--voice` or `$ELEVENLABS_VOICE_ID`) |
| dialogue | ✓, up to **10** unique voices; **no** global `--instruction` |
| seed / language_code / timestamps | ✓ / ✓ / ✓ |
| max characters | ≈ 2000 per request |
| output formats | `mp3_*` (`22050_32` … `44100_192`), `pcm_8000`…`pcm_48000`, `wav_8000`…`wav_48000`, `opus_48000_*`, `ulaw_8000`, `alaw_8000` |
| `--option` keys | `stability`, `similarity_boost`, `style`, `speed`, `use_speaker_boost`, `previous_text`, `next_text`, `apply_text_normalization`, `apply_language_text_normalization`, `optimize_streaming_latency`, `enable_logging` |

Notes (from `capabilities`):
- `mp3_44100_192` needs Creator tier+; PCM/WAV 44.1 kHz needs Pro tier+.
- `language_code` is ignored by `multilingual_v2` models.
- `--timestamps true` switches to the `/with-timestamps` endpoint and writes a
  `<output>.timestamps.json` sidecar (per-character alignment; dialogue adds
  `voice_segments`).

Extra env: `ELEVENLABS_MODEL`, `ELEVENLABS_DIALOGUE_MODEL`, `ELEVENLABS_VOICE_ID`.

## Gemini TTS (`gemini`)

Auth: `GEMINI_API_KEY` (or `GOOGLE_API_KEY`). Routed by the `tts` model family via
`:generateContent` with `responseModalities:["AUDIO"]`.

| aspect | value |
|---|---|
| ops | `speech.generate`, `speech.dialogue` |
| models | `gemini-2.5-flash-preview-tts` (default), `gemini-2.5-pro-preview-tts`, `gemini-3.1-flash-tts-preview` |
| voices | 30 named voices (e.g. `Kore`, `Puck`); see `capabilities` |
| dialogue | ✓, up to **2** speakers; global `--instruction` supported |
| output | **WAV only** (headerless 24 kHz PCM wrapped to WAV) |
| seed / output-format / timestamps | ✗ / ✗ / ✗ (all ignored) |
| direction | style / tone / accent / pace and inline `[whispers]` / `[laughs]` tags go **in the prompt text**; language is auto-detected |

Notes:
- A 200-OK with no audio (silent safety drop) → `safety` error (**exit 8**).
- Env: `GEMINI_TTS_MODEL`.

## Choosing

- **Many distinct voices, precise codec/format, per-character timing, or a seed** →
  `elevenlabs`.
- **Prompt-directed delivery, named voices, a 2-speaker scene, WAV output** →
  `gemini`.
