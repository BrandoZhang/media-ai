# media-ai

Provider- and model-agnostic multimodal generation CLI — **no agent-framework
dependency**. Drop it into any agent sandbox (uni-agent, Claude Code, Codex,
OpenCode, a plain shell, …) and an Agent Skill generates images, video, and speech
by running ordinary commands.

The unit you configure and address is a **binding** — one callable
`(provider, model)` pair such as `volc-ark/seedance-2.0` or `gemini/veo-3.1`. The
same model reached through two providers is two bindings, each with its own endpoint,
credential and limits, because that is what they actually are.

```bash
media-ai bindings list        # what this machine can call right now
media-ai bindings available   # what could be added, and the command to add it
```

Backends ship as **manifests** (`src/media_ai/bindings/*.toml`) declaring scenes,
constraints, lifecycle and auth, paired with an adapter holding the wire. One
declaration is read by discovery, by pre-flight validation, by the setup wizard and
by the packaged skills — so what `capabilities` prints is what actually gates a call.

Highlights: **capability discovery** that cannot drift from enforcement,
**deterministic structured errors** (JSON, category exit codes, a stable `code` and a
runnable `hint`), **no silent fallback** — a missing or ambiguous binding refuses
rather than substituting — **secret-safe credentials** (keys never touch argv, logs,
metadata, or model-visible output), and a **token-cost ledger**.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/BrandoZhang/media-ai/main/install/install.sh | bash
```

Installs [uv](https://docs.astral.sh/uv/) if it is missing, installs the CLI, runs an
offline self-test, then hands over to the setup wizard. Options: `--version REF`,
`--skills-dest PATH`, `--no-init`, `--dry-run`.

Already installed, or configuring a second machine:

```bash
media-ai init                 # pick skills, bindings, keys, scene defaults
media-ai init --skills-only   # just install the Agent Skills
```

```text
┌  media-ai setup
│
◇  Heads up ──────────────────────────────────────────────────╮
│                                                             │
│  media-ai is under rapid development. Interfaces, flags,    │
│  and the result schema can change between releases…         │
│                                                             │
├─────────────────────────────────────────────────────────────╯
◇  Which skills should be installed?
│  media-ai-image, media-ai-video
│
│  Also installing:
│    media-ai-capabilities  always installed
│    media-ai-job           needed by media-ai-video
│
◆  Where should they be installed?
│  ◼ ~/.claude/skills  (Claude Code · all projects · 9 installed)
│  ◻ ./.claude/skills  (Claude Code · current folder)
│  ◻ ~/.agents/skills  (AGENTS.md · all projects)
│  ◻ somewhere else…  (type a path)
│  ↑↓ move · space toggle · a all · enter confirm · esc back
└
```

The wizard is skill-first — you choose what you want to do ("generate images"), each
option described in plain language as you move through it, and it works out which
**bindings** could serve that, straight from the manifests. It then asks for one
credential per binding and ends by naming a default per capability, so a later call
that names no binding still works.

Per binding, not per provider: three Seedream models are three questions. That is the
price of a config that says outright which key each call uses — and adding a binding
to a manifest makes it appear here with no change to the wizard.

Only genuine choices are offered: the shared contract, capability discovery and cost
accounting always install, and the async-job skill comes along with video. It writes
`~/.config/media-ai/credentials.toml` (chmod 600) and `config.toml`, merging into
whatever is already there rather than overwriting it.

The prompts follow [clack](https://github.com/bombshell-dev/clack)'s conventions —
one connected rail, answered steps kept on screen, `●`/`○` for pick-one and `◼`/`◻`
for pick-any, keys echoed as `▪▪▪▪` so you can see you are typing — drawn with
`termios` and ANSI escapes rather than a dependency, and degrading to numbered menus
(and to ASCII glyphs) wherever a real terminal is not available. **Esc** goes back to
the previous question (type `b` where there are no keypresses to read); **Ctrl-C**
cancels. Nothing is written until the last question is answered, so both are safe at
any point.

**Re-running is the upgrade path**, and it is quiet: a skill that already matches the
packaged version is neither rewritten nor asked about, and identical answers do not
leave a second `credentials.toml.bak`. A second `install.sh` with nothing to change
leaves the filesystem byte-identical.

### Checking and removing

```bash
media-ai doctor                        # offline check: PATH, ffmpeg, file modes, whether each binding resolves, skill drift
media-ai uninstall                     # remove the skills and the configuration
media-ai uninstall --keep-credentials  # …but hold on to the API keys
media-ai --version
```

`uninstall` finds the skills it installed (including custom paths, via a receipt at
`~/.config/media-ai/installed-skills.toml`) and asks before it removes anything.
**Uninstalling leaves nothing behind**: skills, `config.toml` and `credentials.toml`
all go, so a later install is a fresh start rather than something that has to migrate
whatever an older version left behind. Hold a file back with `--keep-config`,
`--keep-credentials`, or `--keep-skills`; `--dry-run` shows what would go and `--yes`
skips the questions. It leaves the CLI itself in place (it is what is running) and
prints the one command that removes it; to do the whole lot in one step:

```bash
curl -fsSL https://raw.githubusercontent.com/BrandoZhang/media-ai/main/install/install.sh | bash -s -- --uninstall
```

### From a clone

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
media-ai speech generate --text "the first move sets everything in motion" --output vo.mp3
media-ai speech dialogue --speaker Joe=<voiceA> --speaker Jane=<voiceB> --turn Joe "knock knock" --turn Jane "who is there?" --output d.mp3
media-ai music generate --prompt "upbeat lofi hip hop" --output song.mp3
media-ai sound generate --text "a spacious cinematic braam" --output sfx.mp3
media-ai video generate --continue-from <uri> --prompt "she keeps walking" --output next.mp4
media-ai video concat         --inputs '["a.mp4","b.mp4"]' --output film.mp4
media-ai animation export --input clip.mp4 --output demo.webp --max-width 640 --fps 12
media-ai animation export --frames matted/ --output sticker.gif --transparent
media-ai job    query   --binding <id> --id <op> --output clip.mp4
media-ai bindings       list | available | add <id> --credential env://VAR
media-ai config         show | set-default <scene|group> <binding> | migrate [--dry-run]   # migrate covers credentials.toml too
media-ai capabilities   [--binding ID] [--scene S] [--configured]
media-ai usage
media-ai init           [--skills-only] [--advanced] [--verify]
media-ai doctor
media-ai uninstall      [--keep-skills] [--keep-config] [--keep-credentials] [--yes] [--dry-run]
```

**Notice what the generation commands do not carry: a `--provider` flag.** Each call
derives a *scene* from the inputs you passed (`--first-frame` ⇒ image-to-video,
`--continue-from` ⇒ extend) and runs the binding configured as that scene's default.
Name one explicitly with `--binding <provider>/<model>` when you need a specific
account, model or region. `--model M` alone works when exactly one configured binding
serves it, and refuses with both candidates when two do.

### Normalized geometry

Ask for size two ways; each adapter maps it to its wire format and the binding's
declared constraints are what validate it:

```bash
--size 1024x1536                       # explicit pixels
--aspect-ratio 16:9 --resolution 2K    # ratio + named tier (1K/2K/4K, or 480p/720p/1080p for video)
```

### Binding-specific options

Cross-provider concepts are first-class flags (`--seed`, `--negative-prompt`,
`--background`, `--quality`, `--audio`, `--duration`). Anything one backend alone
understands goes through `--option key=value`, and only keys the binding declares are
accepted:

```bash
media-ai video generate --binding volc-ark/seedance-2.0 --prompt "..." --output c.mp4 --option camera_fixed=true
media-ai image generate --binding openai/gpt-image-2   --prompt "..." --output o.png --option moderation=low
```

Knobs belonging to the *integration* rather than the call — poll intervals, an org id,
an inline-upload ceiling — live on the binding in `config.toml`, not in the
environment. Two bindings on one provider can differ.

## Machine contract (for Agent Skills)

- **stdout** is exactly one JSON object — success *or* failure — so a Skill parses
  one line. Both carry `schema_version`, so one schema validates either. Success adds
  `artifacts[]`, `usage`, and a `meta` recording **which binding ran and what scene it
  was asked for**. Failure is `{"ok": false, "schema_version", "error": {"category",
  "code", "hint", "details", …}}`, where `hint` is usually a command to run verbatim.
- **Every produced file is an entry in `artifacts[]`**, each with its `path`, `kind`,
  `mime`, `bytes` and `role`. There is no flatter alias beside it: one call can produce
  several files (`--count 3`, a timestamps sidecar, a returned last frame), and a short
  path exposing only the first is how the rest get dropped without anyone noticing.
- **stderr** is redacted human logs only (never part of the contract).
- **Exit codes** map to the error category so a Skill can branch on `$?` alone:
  `0` ok · `2` CLI misuse · `3` validation/unsupported · `4` auth · `5`
  rate-limit/quota · `6` provider · `7` timeout · `8` safety · `9` not-found.
- **Ask what exists:** `media-ai bindings list` and `media-ai capabilities --scene S`
  report what this machine can call and what each accepts, read from the same
  declaration that enforces it. Unsupported requests fail with exit 3 and a
  machine-readable reason (override with `--on-unsupported warn|ignore`).
- **Nothing falls back.** A missing, ambiguous or unsuitable binding refuses and names
  the candidates. An unconfigured machine never quietly produces a mock placeholder —
  the one failure an agent cannot tell from success.

See **[docs/AGENT_SKILLS.md](docs/AGENT_SKILLS.md)** for the full integration guide.

## Credentials

Each binding names **one** source — `env://`, `cred://` (a `chmod 600` account file),
`keychain://`, `broker://`, or a registered secret-manager scheme — and there is no
fallback between them, so "which key did this call use?" has a one-line answer. The
value is returned as a **reveal-only `Secret`** used only at call time and **redacted
from every log and output**. The CLI never accepts a key as a flag. For hosted agents
a broker injects the key at egress, so the CLI holds only a session token. See
**[docs/CREDENTIALS.md](docs/CREDENTIALS.md)**.

## Usage ledger

Every generation appends a line to `$MEDIA_USAGE_LOG` (default
`./media_usage.jsonl`). `media-ai usage` aggregates token/artifact cost **per binding
and per scene** — not per provider, because two models behind one provider cost
different amounts and a provider total cannot tell you which to stop calling. Point
`MEDIA_USAGE_LOG` and each `--output` at a per-task directory to isolate concurrent
runs on a shared filesystem.

## Example

```bash
export MEDIA_USAGE_LOG=/tmp/run/usage.jsonl
media-ai image generate --prompt "silver astronaut on a red dune" --output /tmp/run/ref.png --seed 7
media-ai video generate --first-frame /tmp/run/ref.png --prompt "he turns to camera" \
    --output /tmp/run/s1.mp4 --duration 3 --resolution 480p
media-ai video generate --prompt "twin suns setting" --output /tmp/run/s2.mp4 --duration 3 --resolution 480p
media-ai video concat --inputs '["/tmp/run/s1.mp4","/tmp/run/s2.mp4"]' --output /tmp/run/final.mp4
media-ai usage
```

## Docs

- [src/media_ai/skills/](src/media_ai/skills/) — packaged Agent Skills, one per command group (image, video, speech, music, sound, animation, job, capabilities, usage) plus the shared contract
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — uv-based dev environment + workflow
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layered design + request-flow diagrams
- [docs/BINDINGS.md](docs/BINDINGS.md) — what a binding is, configuring one, `extends`, per-binding options
- [docs/CREDENTIALS.md](docs/CREDENTIALS.md) — credential resolution, redaction, broker
- [docs/AGENT_SKILLS.md](docs/AGENT_SKILLS.md) — invocation contract for Agent Skills
- [docs/EXTENDING.md](docs/EXTENDING.md) — add a model or a whole backend (no core changes)
- [docs/LIVE_TESTS.md](docs/LIVE_TESTS.md) — real-API validation log (all providers)
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — unresolved provider-specific items
- [docs/history/](docs/history/) — design records for changes already shipped. Kept for the arguments, not as a description of the current system

## Adding a model, or a whole backend

**No changes to core.** A backend is a **manifest** (what it can do) plus an
**adapter** (how to call it):

- A sibling model on an existing provider is usually one `[[binding]]` entry and no
  code — the wire is already implemented.
- A new provider is a manifest plus an `Adapter` subclass, registered in-process with
  `register_manifest(...)` or shipped as a `media_ai.bindings` entry point. The
  manifest's `adapter` field is an import path, so the code may live in a private
  package.
- `transport = "rpc"` gets no base URL, no HTTP client and no status mapping — an
  internal gRPC or Thrift platform registers on exactly the same terms, which is why
  the wire stayed in code rather than becoming a DSL.

Every new model arrives with four answers, because they are the manifest's required
fields and `tests/test_manifests.py` enforces them: which provider, which model and
its wire id, what it supports, and how to authenticate. The tests also check the named
adapter imports and implements every scene declared. See
[docs/EXTENDING.md](docs/EXTENDING.md).
