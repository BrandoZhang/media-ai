# media-ai

Provider- and model-agnostic multimodal generation CLI — **no agent-framework
dependency**. Drop it into any agent sandbox (uni-agent, Claude Code, Codex,
OpenCode, a plain shell, …) and an Agent Skill generates images, video, and speech
by running ordinary commands. One normalized interface drives multiple backends:

| Provider | Images | Video | Speech | Auth env |
|---|---|---|---|---|
| **`mock`** (default, offline) | ✓ Pillow placeholders | ✓ ffmpeg clips | ✓ WAV placeholders | — |
| **`volc`** — Volcengine Ark (Doubao Seedream/Seedance) | ✓ | ✓ (async) | — | `ARK_API_KEY` |
| **`openai`** — GPT Image | ✓ | — (image-only) | — | `OPENAI_API_KEY` |
| **`gemini`** — Gemini native image (Nano Banana) / Veo / TTS | ✓ | ✓ (async) | ✓ (+ multi-speaker) | `GEMINI_API_KEY` |
| **`elevenlabs`** — TTS + dialogue + music + sound effects | — | — | ✓ (+ music/sfx) | `ELEVENLABS_API_KEY` |

Highlights: **capability discovery** (`media-ai capabilities`), **deterministic
structured errors** (JSON + category exit codes), **secret-safe credentials**
(keys never touch argv, logs, metadata, or model-visible output), and a
**token-cost ledger**.

## Install

Development uses **[uv](https://docs.astral.sh/uv/)** (full guide:
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)):

```bash
uv sync                          # .venv + editable install + dev tools (pytest, ruff)
uv run media-ai image generate --prompt "a red bicycle" --output bike.png
```

Or with pip:

```bash
pip install -e .                 # from a clone
pip install -e ".[keychain]"     # + OS keychain credential source
```

Pulls in Pillow and a bundled ffmpeg (`imageio-ffmpeg`) for the offline mock
backend — no system packages needed. Real providers use only the stdlib.

## Commands

```bash
media-ai image generate --prompt "a red bicycle" --output bike.png
media-ai image edit     --reference bike.png --prompt "make it blue" --output blue.png
media-ai video generate --prompt "twin suns setting" --output clip.mp4 --resolution 480p
media-ai speech generate --text "the first move sets everything in motion" --output vo.mp3 --provider elevenlabs
media-ai speech dialogue --speaker Joe=<voiceA> --speaker Jane=<voiceB> --turn Joe "knock knock" --turn Jane "who is there?" --output d.mp3
media-ai music generate --prompt "upbeat lofi hip hop" --output song.mp3 --provider elevenlabs
media-ai sound generate --text "a spacious cinematic braam" --output sfx.mp3 --provider elevenlabs
media-ai concat         --inputs '["a.mp4","b.mp4"]' --output film.mp4
media-ai job    query   --provider gemini --id <op> --output clip.mp4
media-ai capabilities   [--provider P] [--model M]
media-ai usage
```

Speech works on `gemini` (Gemini 2.5/3.1 TTS — style directed in the prompt text, 30
named voices, ≤2-speaker dialogue) and `elevenlabs` (voice settings + `--timestamps`
alignment, ≤10-voice dialogue). `elevenlabs` also does **music** (`media-ai music
generate/plan`) and **sound effects** (`media-ai sound generate`). Pick a backend with
`--provider {mock,volc,openai,gemini,elevenlabs}` (or `$MEDIA_PROVIDER`,
default `mock`) and a model with `--model`. A model id can imply its provider
(`--model gpt-image-2` ⇒ openai). The eight original console-scripts
(`text2image`, `image2image`, `text2video`, `image2video`, `ref2video`,
`concat_video`, `video_task`, `media_usage`) remain installed as compatibility
shims.

### Normalized geometry

Ask for size two ways; each adapter maps/validates it against the model:

```bash
--size 1024x1536                       # explicit pixels
--aspect-ratio 16:9 --resolution 2K    # ratio + named tier (1K/2K/4K, or 480p/720p/1080p for video)
```

### Provider-specific options

Cross-provider concepts are first-class flags (`--seed`, `--negative-prompt`,
`--background`, `--quality`, `--audio`, `--duration`). Anything provider-specific
goes through `--option key=value` and is **capability-gated**:

```bash
media-ai video generate --provider volc --prompt "..." --output c.mp4 --option camera_fixed=true
media-ai image generate --provider openai --model gpt-image-2 --prompt "..." --output o.png --option moderation=low
```

## Machine contract (for Agent Skills)

- **stdout** is exactly one JSON object — success *or* failure — so a Skill parses
  one line. Success carries `artifacts[]`, `usage`, `meta`, `provider`, `model`.
  Failure is `{"ok": false, "error": {"category", "code", "retryable", …}}`.
- **stderr** is redacted human logs only (never part of the contract).
- **Exit codes** map to the error category so a Skill can branch on `$?` alone:
  `0` ok · `2` CLI misuse · `3` validation/unsupported · `4` auth · `5`
  rate-limit/quota · `6` provider · `7` timeout · `8` safety · `9` not-found.
- **Discover before you ask:** `media-ai capabilities --provider openai` reports
  which operations/options/geometry each model supports, so a Skill can pick a
  valid request. Unsupported requests fail with exit 3 and a machine-readable
  reason (override with `--on-unsupported warn|ignore`).

See **[docs/AGENT_SKILLS.md](docs/AGENT_SKILLS.md)** for the full integration guide.

## Credentials

Keys are resolved through a chain (broker → secret-manager reference → OS keychain
→ `chmod 600` config file → env var), returned as a **reveal-only `Secret`** used
only at HTTP-call time, and **redacted from every log/output**. The CLI never
accepts a key as a flag. For hosted/managed agents, a broker injects the key at
egress so the CLI holds only a session token. See
**[docs/CREDENTIALS.md](docs/CREDENTIALS.md)**.

## Usage ledger

Every generation appends a line to `$MEDIA_USAGE_LOG` (default
`./media_usage.jsonl`). `media-ai usage` aggregates token/artifact cost. Point
`MEDIA_USAGE_LOG` and each `--output` at a per-task directory to isolate
concurrent runs on a shared filesystem.

## Example

```bash
export MEDIA_USAGE_LOG=/tmp/run/usage.jsonl
media-ai image generate --prompt "silver astronaut on a red dune" --output /tmp/run/ref.png --seed 7
media-ai video generate --first-frame /tmp/run/ref.png --prompt "he turns to camera" \
    --output /tmp/run/s1.mp4 --duration 3 --resolution 480p
media-ai video generate --prompt "twin suns setting" --output /tmp/run/s2.mp4 --duration 3 --resolution 480p
media-ai concat --inputs '["/tmp/run/s1.mp4","/tmp/run/s2.mp4"]' --output /tmp/run/final.mp4
media-ai usage
```

## Docs

- [skills/](skills/) — packaged Agent Skills, one per CLI functionality (image, video, concat, job, capabilities, usage)
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — uv-based dev environment + workflow
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layered design + request-flow diagrams
- [docs/PROVIDERS.md](docs/PROVIDERS.md) — per-provider setup + capability matrix
- [docs/CREDENTIALS.md](docs/CREDENTIALS.md) — credential resolution, redaction, broker
- [docs/AGENT_SKILLS.md](docs/AGENT_SKILLS.md) — invocation contract for Agent Skills
- [docs/EXTENDING.md](docs/EXTENDING.md) — add a custom provider (no core changes)
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — unresolved provider-specific items

## Custom providers

Adding a backend requires **no changes to core**. Subclass `Provider` /
`HttpProvider`, declare a `ModelCapabilities` schema per model (which drives
discovery + validation), expose provider-specific functions via capability-gated
`--option`, and register it — in-process with `register_provider(...)` or as an
installed package via a `media_ai.providers` entry point. See
[docs/EXTENDING.md](docs/EXTENDING.md).
