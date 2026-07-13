---
name: media-ai-shared
description: >-
  Foundation for driving the media-ai generation CLI (images, video, audio —
  speech/music/sound — concat, async jobs) from an agent. Read this FIRST before
  using any media-ai-* skill. Covers the machine contract (one JSON object on stdout,
  category exit codes), provider/model selection (volc, openai, gemini, elevenlabs),
  the discover-before-you-generate rule via `media-ai capabilities`, and the
  secret-safe credential model. Use when running any `media-ai` command, wiring up
  provider credentials, interpreting its JSON output or exit codes, or deciding which
  provider/model to use.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai capabilities"
---

# media-ai-shared — foundation for the media-ai CLI

`media-ai` is a provider- and model-agnostic multimodal generation CLI spanning
three modalities — **image**, **video**, and **audio** (speech / music / sound). One
normalized interface drives multiple backends (`volc`, `openai`, `gemini`,
`elevenlabs`) plus an offline `mock` default. **Read this skill first** — every
`media-ai-*` skill (`media-ai-image`, `media-ai-video`, `media-ai-speech`,
`media-ai-music`, `media-ai-sound`, `media-ai-concat`, `media-ai-job`,
`media-ai-capabilities`, `media-ai-usage`) relies on the contract and rules below.

## The one rule that prevents most failures

> **Discover before you generate.** Run `media-ai capabilities --provider <p>`
> (or `--model <m>`) and pick a request that fits the model's reported
> operations / geometry / options. Guessing an unsupported option fails
> deterministically with **exit 3** *before any network call*. See the
> `media-ai-capabilities` skill.

## Machine contract (never break these assumptions)

- **stdout is exactly one JSON object** — for success *and* failure. Parse the
  last line of stdout; do **not** parse stderr.
- **stderr is redacted human logs only.**
- **Exit code encodes the failure category** — branch on `$?` without parsing:

  | code | meaning | what to do |
  |---|---|---|
  | 0 | success | read `artifacts[]` |
  | 2 | CLI misuse (bad flags) | fix the invocation |
  | 3 | validation / unsupported option | fix the request (run `capabilities`) |
  | 4 | auth / missing credentials | set the provider's API key in the env |
  | 5 | rate limit / quota | retry with backoff |
  | 6 | provider / upstream error | maybe retry |
  | 7 | timeout | retry / poll the job |
  | 8 | safety / moderation block | change the prompt |
  | 9 | not found (job / model) | — |

Success carries `artifacts[]` + `usage` + `meta` (+ compat aliases `path`,
`extra_paths`, `bytes`). Failure is `{"ok": false, "error": {...}}`. Full JSON
shapes → `references/machine-contract.md`.

## Selecting a provider and model

Every generation command accepts these global flags (from `add_global_args`):

| Flag | Meaning |
|---|---|
| `--provider {mock,volc,openai,gemini,elevenlabs}` | backend; default `$MEDIA_PROVIDER` else `mock` |
| `--model <id>` | model id; **a model id can imply the provider** (e.g. `--model gpt-image-2` ⇒ openai) |
| `--provider-profile <name>` | a named profile (provider + model + endpoint + credential ref) from `~/.config/media-ai/config.toml` |
| `--on-unsupported {error,warn,ignore}` | how to handle unsupported options (default `error` → exit 3) |
| `--pretty` | pretty-print the JSON result |
| `--metadata-out <path>` | also write the (secret-free) result JSON to a file |
| `--log-level {debug,info,warning,error}` | stderr verbosity |

Model-id → provider routing (bare `--model`): `doubao*`/`seedream*`/`seedance*` ⇒
`volc`; `gpt-image*` ⇒ `openai`; `gemini-*` (incl. `*-tts`)/`veo-*` ⇒ `gemini`;
`eleven_*`/`eleven-*` ⇒ `elevenlabs`. (Retired `dall-e*`/`sora*` ids still route to
`openai` only to return a clear unsupported/removal error — DALL·E and Sora are gone.)
Provider/model selection detail and per-provider matrices → `references/providers.md`.

## Credentials — keys never touch argv

Set the provider's key in the **environment** (or a broker/keychain/secret-manager);
there is **no `--api-key` flag**. The CLI resolves keys lazily at HTTP-call time and
redacts them from every sink.

| provider | env var(s) |
|---|---|
| `volc` | `ARK_API_KEY` (or `VOLC_API_KEY`) |
| `openai` | `OPENAI_API_KEY` |
| `gemini` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) |
| `elevenlabs` | `ELEVENLABS_API_KEY` (or `ELEVEN_API_KEY`) |
| `mock` | none (offline) |

Full resolution chain, profiles, and secret rules → `references/credentials.md`.

## Recommended flow (discover → generate → poll → account)

```bash
# per-task dir isolates concurrent runs on a shared filesystem
export MEDIA_USAGE_LOG=/tmp/run/usage.jsonl

media-ai capabilities --provider gemini --pretty          # 1. discover
media-ai image generate --provider gemini --prompt "..." \
    --aspect-ratio 16:9 --resolution 2K --output /tmp/run/ref.png   # 2. generate
media-ai video generate --provider gemini --first-frame /tmp/run/ref.png \
    --prompt "..." --output /tmp/run/clip.mp4 --wait false          # 3. async submit → JobHandle
# poll the `poll` string it returned until status == succeeded
media-ai usage                                            # 4. account
```

## References

- `references/machine-contract.md` — full success / JobHandle / JobStatus / error JSON shapes, exit codes, `--metadata-out`, JSON-array list flags.
- `references/credentials.md` — env vars, resolution chain, profiles, broker, secret-safety rules.
- `references/providers.md` — provider/model routing, the capability matrix per provider, base_url + tuning env vars.

Repo docs (deeper): `../../docs/AGENT_SKILLS.md`, `../../docs/CREDENTIALS.md`, `../../docs/PROVIDERS.md`.
