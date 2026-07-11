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

| Skill | Covers | CLI surface |
|---|---|---|
| **[`media-ai-shared`](media-ai-shared/SKILL.md)** | **Read first.** Machine contract, exit codes, provider/model selection, credentials, discover-first rule | global flags, `capabilities` |
| [`media-ai-image`](media-ai-image/SKILL.md) | Generate & edit images (Seedream / GPT-Image·DALL·E / Nano Banana) | `image generate`, `image edit` |
| [`media-ai-video`](media-ai-video/SKILL.md) | Generate video from text / frames / references (Seedance / Veo / Sora) | `video generate` |
| [`media-ai-job`](media-ai-job/SKILL.md) | Poll, finalize, or cancel async jobs | `job query`, `job cancel` |
| [`media-ai-concat`](media-ai-concat/SKILL.md) | Join clips into one film (local ffmpeg) | `concat` |
| [`media-ai-capabilities`](media-ai-capabilities/SKILL.md) | Discover what each provider/model supports | `capabilities` |
| [`media-ai-usage`](media-ai-usage/SKILL.md) | Token/artifact cost accounting | `usage` |

The offline `mock` provider (the default) is intentionally **not** a documented
target — it's only the credential-free backend used for local testing; the skills
cover the real providers (`volc`, `openai`, `gemini`).

## Prerequisites

- The `media-ai` CLI on `PATH` (`uv sync` / `pip install -e .` from the repo root).
  Each skill declares `metadata.requires.bins: ["media-ai"]`.
- Provider credentials set in the **environment** (never in argv) — see
  [`media-ai-shared/references/credentials.md`](media-ai-shared/references/credentials.md).
  No keys are needed to try the offline `mock` default.

## Activating in Claude Code

This folder is the **distribution** home. To make a skill available to Claude Code,
copy or symlink it into a discovered skills directory (project-level
`.claude/skills/` or user-level `~/.claude/skills/`):

```bash
mkdir -p .claude/skills
ln -s "$(pwd)/skills/media-ai-shared"       .claude/skills/media-ai-shared
ln -s "$(pwd)/skills/media-ai-image"        .claude/skills/media-ai-image
ln -s "$(pwd)/skills/media-ai-video"        .claude/skills/media-ai-video
ln -s "$(pwd)/skills/media-ai-job"          .claude/skills/media-ai-job
ln -s "$(pwd)/skills/media-ai-concat"       .claude/skills/media-ai-concat
ln -s "$(pwd)/skills/media-ai-capabilities" .claude/skills/media-ai-capabilities
ln -s "$(pwd)/skills/media-ai-usage"        .claude/skills/media-ai-usage
```

Skills are also portable to any agent runtime that reads the `SKILL.md` format.
