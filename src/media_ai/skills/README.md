# media-ai Agent Skills

Packaged [Agent Skills](https://docs.claude.com/en/docs/claude-code/skills) that
teach an agent to drive the `media-ai` CLI — one skill per functionality. They
implement the invocation contract described in
[`../docs/AGENT_SKILLS.md`](../docs/AGENT_SKILLS.md).

Each skill is a folder with a `SKILL.md` (YAML frontmatter + a compact overview and
"prefer these commands" guide) and, for the larger ones, a `references/` subfolder
loaded on demand (progressive disclosure). Layout and conventions follow
[larksuite/cli](https://github.com/larksuite/cli).

**These files are templates, not finished skills.** A directory here is named for its
command *group* (`image/`), and its text says `{{cli}}` and `{{skill}}` rather than
naming the CLI — `media-ai init` renders both while copying, so a build distributed
under another name ships skills that spell out the command it actually installs. See
[`../brand.py`](../brand.py). The installed copy is `media-ai-image/`; the table below
lists both. Never write the command name into a skill by hand: `tests/test_brand.py`
fails the build for it, because a literal only works for the unrenamed distribution.

## Skills

One skill per **command group**, which is also one skill per set of **scenes** — the
group in a scene id (`video.extend`) is the group in the command (`media-ai video`),
so a skill covers exactly the scenes under its name and nothing overlaps.

| Skill (installed name) | Tier | Covers | CLI surface |
|---|---|---|---|
| **[`media-ai-shared`](shared/SKILL.md)** | core | **Read first.** Machine contract, exit codes, how a binding is named, credentials, the ask-the-CLI rule | global flags |
| [`media-ai-capabilities`](capabilities/SKILL.md) | core | Discover which bindings exist, which are reachable, and what each accepts | `capabilities` |
| [`media-ai-usage`](usage/SKILL.md) | core | Cost accounting per binding and per scene | `usage` |
| [`media-ai-image`](image/SKILL.md) | optional | `image.*` — text→image, image→image | `image generate`, `image edit` |
| [`media-ai-video`](video/SKILL.md) | optional | `video.*` — text/image/keyframe/reference→video, extend, concat | `video generate`, `video concat` |
| [`media-ai-speech`](speech/SKILL.md) | optional | `speech.*` — text→speech, multi-voice dialogue | `speech generate`, `speech dialogue` |
| [`media-ai-music`](music/SKILL.md) | optional | `music.*` — prompt→song, plan→song, prompt→plan | `music generate`, `music plan` |
| [`media-ai-sound`](sound/SKILL.md) | optional | `sound.*` — text→sound effect | `sound generate` |
| [`media-ai-animation`](animation/SKILL.md) | optional | `animation.*` — clip or stills→animated image (GIF, WebP, APNG), with keying to alpha | `animation export` |
| [`media-ai-job`](job/SKILL.md) | dependency | Poll, finalize, or cancel async jobs | `job query`, `job cancel` |

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

To do it by hand instead, point `init` at the directory — **do not symlink or copy
these folders**, because what is in them is unrendered: an agent following a symlink
finds `{{cli}} image generate` and runs nothing. Working from a git checkout:

```bash
uv run media-ai init --skills-only --skills-dest .claude/skills
```

That is a sync, not a copy: re-running it after editing a skill rewrites what changed,
drops what the packaged skill no longer ships, and is silent when there is nothing to
do — so it is cheap to repeat while working on a skill. `media-ai doctor` reports any
installed copy that has drifted, including a symlink left over from before rendering
existed.

Skills are portable to any agent runtime that reads the `SKILL.md` format.

> **Installing into this repo leaves no trace in `git status`.** `.gitignore` drops
> `media-ai-*` from every agent directory the wizard offers, because those are copies
> of *this* folder: committing them would mean maintaining each file twice, and the
> copy goes stale the moment the packaged one is edited (`media-ai doctor` reports the
> drift). Install freely while working on the CLI. A skill this project writes by hand
> is not `media-ai-*`, so it still tracks normally.
