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

One skill per **command group**, which is also one skill per set of **scenes** — the
group in a scene id (`video.extend`) is the group in the command (`media-ai video`),
so a skill covers exactly the scenes under its name and nothing overlaps.

| Skill | Tier | Covers | CLI surface |
|---|---|---|---|
| **[`media-ai-shared`](media-ai-shared/SKILL.md)** | core | **Read first.** Machine contract, exit codes, how a binding is named, credentials, the ask-the-CLI rule | global flags |
| [`media-ai-capabilities`](media-ai-capabilities/SKILL.md) | core | Discover which bindings exist, which are reachable, and what each accepts | `capabilities` |
| [`media-ai-usage`](media-ai-usage/SKILL.md) | core | Cost accounting per binding and per scene | `usage` |
| [`media-ai-image`](media-ai-image/SKILL.md) | optional | `image.*` — text→image, image→image | `image generate`, `image edit` |
| [`media-ai-video`](media-ai-video/SKILL.md) | optional | `video.*` — text/image/keyframe/reference→video, extend, concat | `video generate`, `video concat` |
| [`media-ai-speech`](media-ai-speech/SKILL.md) | optional | `speech.*` — text→speech, multi-voice dialogue | `speech generate`, `speech dialogue` |
| [`media-ai-music`](media-ai-music/SKILL.md) | optional | `music.*` — prompt→song, plan→song, prompt→plan | `music generate`, `music plan` |
| [`media-ai-sound`](media-ai-sound/SKILL.md) | optional | `sound.*` — text→sound effect | `sound generate` |
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

### Skills name no models

A skill says "run `media-ai bindings list`", never a model id. Lineups change faster
than skill text, and a stale list read confidently is worse than none — so the
manifests are the only source of truth and the skills ask them at runtime.

What a manifest *cannot* generate — what a binding is good at, how to prompt it, the
traps — lives in `references/bindings/<provider>.<model>.md` beside the skill. Only
bindings with something non-obvious to say have one.

`mock/mock` is deliberately not a documented target. It draws placeholders, and a
placeholder returned as a deliverable is worse than a failure; it is a normal binding
that must be asked for by name, never a fallback.

## Prerequisites

- The `media-ai` CLI on `PATH` (`uv sync` / `pip install -e .` from the repo root).
  Each skill declares `metadata.requires.bins: ["media-ai"]`.
- A configured binding. Each names exactly one credential source (`env://`, `cred://`,
  `keychain://`, …); keys never travel in argv and there is no `--api-key` flag — see
  [`media-ai-shared/references/credentials.md`](media-ai-shared/references/credentials.md).
  `local/ffmpeg` and `mock/mock` need none.

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
