# media-ai Agent Skills

Packaged [Agent Skills](https://docs.claude.com/en/docs/claude-code/skills) that
teach an agent to drive the `media-ai` CLI — one skill per functionality. They
implement the invocation contract described in
[`../docs/AGENT_SKILLS.md`](../docs/AGENT_SKILLS.md).

Each skill is a folder with a `SKILL.md` (YAML frontmatter + a compact overview and
"prefer these commands" guide) and, for the larger ones, a `references/` subfolder
loaded on demand (progressive disclosure). Layout and conventions follow
[larksuite/cli](https://github.com/larksuite/cli).

## Skills

| Skill | Tier | Covers | CLI surface |
|---|---|---|---|
| **[`media-ai-shared`](media-ai-shared/SKILL.md)** | core | **Read first.** Machine contract, exit codes, provider/model selection, credentials, discover-first rule | global flags, `capabilities` |
| [`media-ai-capabilities`](media-ai-capabilities/SKILL.md) | core | Discover what each provider/model supports | `capabilities` |
| [`media-ai-usage`](media-ai-usage/SKILL.md) | core | Token/artifact/character cost accounting | `usage` |
| [`media-ai-image`](media-ai-image/SKILL.md) | optional | Generate & edit images (Seedream / GPT-Image / Nano Banana) | `image generate`, `image edit` |
| [`media-ai-video`](media-ai-video/SKILL.md) | optional | Generate video from text / frames / references (Seedance / Veo) | `video generate` |
| [`media-ai-speech`](media-ai-speech/SKILL.md) | optional | Text → speech & multi-voice dialogue (ElevenLabs / Gemini TTS) | `speech generate`, `speech dialogue` |
| [`media-ai-music`](media-ai-music/SKILL.md) | optional | Compose songs from a prompt or plan (ElevenLabs Music) | `music generate`, `music plan` |
| [`media-ai-sound`](media-ai-sound/SKILL.md) | optional | Text → sound effect (ElevenLabs) | `sound generate` |
| [`media-ai-concat`](media-ai-concat/SKILL.md) | optional | Join clips into one film (local ffmpeg) | `concat` |
| [`media-ai-job`](media-ai-job/SKILL.md) | dependency | Poll, finalize, or cancel async jobs | `job query`, `job cancel` |

### Install tiers

Each skill declares its own tier under `metadata.install` in its `SKILL.md`, which is
what `media-ai init` reads to decide whether to *ask* about it:

- **`core`** — always installed, never offered. The shared contract, capability
  discovery, and cost accounting are assumed by every other skill; making them a
  choice only produces selections that half-work.
- **`optional`** — a genuine choice, shown in the menu with the one-paragraph
  `metadata.install.summary` beneath it.
- **`dependency`** — never offered on its own; installed because something that *was*
  chosen names it in `metadata.install.needs`. `media-ai-video` needs `media-ai-job`,
  because generation is asynchronous on every real backend and a job you cannot poll
  is a video you cannot collect.

The wizard prints every skill it adds on your behalf, and why.

Three modalities — **image**, **video**, and **audio** (speech / music / sound). The
offline `mock` provider (the default) is intentionally **not** a documented target —
it's only the credential-free backend used for local testing; the skills cover the
real providers (`volc`, `openai`, `gemini`, `elevenlabs`).

## Prerequisites

- The `media-ai` CLI on `PATH` (`uv sync` / `pip install -e .` from the repo root).
  Each skill declares `metadata.requires.bins: ["media-ai"]`.
- Provider credentials set in the **environment** (never in argv) — see
  [`media-ai-shared/references/credentials.md`](media-ai-shared/references/credentials.md).
  No keys are needed to try the offline `mock` default.

## Installing

These ship inside the installed package, so the CLI can copy them out for you:

```bash
media-ai init                 # full wizard: pick which skills, pick where
media-ai init --skills-only   # just the skills step
media-ai uninstall            # take them back out again
```

The wizard offers the conventions agents actually read — `.claude/skills`,
`.agents/skills`, `.codex/skills`, `.trae/skills`, `.openclaw/skills` — each at
user level (`~/`, every project on the machine) or in the current folder (`./`),
plus a "somewhere else…" row for any other path. Directories that already exist
are pre-ticked; you can install to several at once. Every destination
is recorded in `~/.config/media-ai/installed-skills.toml` so `media-ai uninstall`
can find them again, and `media-ai doctor` reports any copy that has drifted from
the version the CLI ships.

To do it by hand instead, copy or symlink the skill directories out of this folder
into whichever of those directories your agent reads. Working from a git checkout:

```bash
mkdir -p .claude/skills
for s in src/media_ai/skills/media-ai-*; do
  ln -s "$(pwd)/$s" ".claude/skills/$(basename "$s")"
done
```

Skills are portable to any agent runtime that reads the `SKILL.md` format.
