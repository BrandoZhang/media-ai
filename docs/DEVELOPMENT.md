# Development guide

This project uses **[uv](https://docs.astral.sh/uv/)** as the environment and
dependency manager. `uv` creates the virtualenv, installs the project (editable)
plus the dev tools, and pins everything in `uv.lock` for reproducible installs.
You do **not** need to create a venv or `pip install` anything by hand.

## Prerequisites

- **uv** — install once:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh      # macOS / Linux
  # or: brew install uv   |   pipx install uv   |   winget install astral-sh.uv
  ```
- **Python 3.11+** — required (the code uses `tomllib`). You don't have to install
  it yourself: `uv` fetches a matching interpreter automatically. To pin one:
  ```bash
  uv python pin 3.11        # writes .python-version; uv sync then uses it
  ```
- **No system packages needed.** ffmpeg and Pillow arrive as Python dependencies
  (`imageio-ffmpeg` bundles an ffmpeg binary), so the offline/mock path and its
  tests work out of the box. A system `ffmpeg` is used automatically if present
  (slightly faster) but is optional.

## Set up the environment

```bash
git clone <repo> && cd media-ai
uv sync
```

`uv sync` creates `.venv/`, installs `media-ai` in **editable** mode, installs the
`dev` dependency group (`pytest`, `ruff`), and writes/updates `uv.lock`. Re-run it
any time after pulling changes (or just use `uv run`, which auto-syncs first).

You never need to activate the venv — prefix commands with `uv run`. If you prefer
an activated shell anyway: `source .venv/bin/activate`.

## Project layout

The package lives under **`src/`** — the [PEP&nbsp;517/518 "src layout"](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
recommended by the Python Packaging Authority (and the direct analog of the
`src/` convention JavaScript/TypeScript CLIs use). Its point: tests and tools run
against the **installed** package, never against a directory that just happens to
be importable from the repo root — so a broken `pyproject.toml`, a missing
package entry, or a stray top-level module gets caught here instead of after
release.

```
media-ai/
├── pyproject.toml          # build config; setuptools discovers packages under src/
├── src/
│   └── media_ai/           # the importable package (import name unchanged)
│       ├── __main__.py     # `media-ai` console entry point
│       ├── cli/            # per-group CLI front ends
│       ├── core/           # provider-agnostic core (never imports providers/)
│       ├── credentials/    # secret resolution + redaction
│       ├── media/          # local ffmpeg / Pillow helpers
│       ├── providers/       # per-provider adapters
│       └── skills/          # agent skills (shipped as package data)
├── tests/                  # offline test suite
└── docs/                   # this guide + architecture/credentials/…
```

Because it's a src layout, the package must be **installed** to import it (there is
no `media_ai/` at the repo root to pick up implicitly). `uv sync` / `uv run` handle
that editable install automatically, so nothing changes in day-to-day use. Point
lint and other path-taking tools at `src` (e.g. `ruff check src tests`).

## Everyday commands

| Task | Command |
|---|---|
| Install / refresh the env | `uv sync` |
| Run the full test suite (offline) | `uv run pytest -q` |
| Run a single test | `uv run pytest -q tests/test_gemini_api.py::test_veo_lro_poll_and_download` |
| Run one file | `uv run pytest -q tests/test_volc_errors.py` |
| Lint | `uv run ruff check src tests` |
| Autofix lint | `uv run ruff check src tests --fix` |
| Run the CLI (mock by default) | `uv run media-ai image generate --prompt "a red bike" --output /tmp/x.png` |
| Python REPL in the env | `uv run python` |

`uv run` re-syncs the env if `pyproject.toml`/`uv.lock` changed, so after `git pull`
you can go straight to `uv run pytest`.

## Managing dependencies

Edit dependencies through `uv` so `pyproject.toml` and `uv.lock` stay in sync:

```bash
uv add httpx                     # runtime dep -> [project.dependencies]
uv add --group dev mypy          # dev-only tool -> [dependency-groups].dev
uv add --optional keychain keyring   # into the `keychain` extra
uv remove httpx
uv lock --upgrade                # re-resolve all deps to latest allowed
uv lock --upgrade-package ruff   # bump just one
```

Keep **runtime** dependencies minimal: `core/` and the provider adapters use only
the standard library plus `pillow`/`imageio-ffmpeg`. Anything a single provider
needs (e.g. an SDK, `keyring`) belongs in an optional extra, not the core deps.

The `[project.optional-dependencies]` extras remain for the pip / CI path:
`uv sync --extra keychain` installs the OS-keychain credential source; `--all-extras`
installs them all.

## Running against a real provider

The default `mock` provider needs no credentials or network. For a real backend,
set the provider and its key in the **environment** (never as a CLI flag). The
fastest way to see every variable is [`.env.example`](../.env.example) — copy it and
fill in the block for your provider:

```bash
cp .env.example .env          # then edit .env (OPENAI_API_KEY=…, ARK_API_KEY=…, etc.)
uv run --env-file .env media-ai capabilities --provider openai
uv run --env-file .env media-ai image generate --model gpt-image-2 --prompt "a fox" --output fox.png
```

`uv run --env-file .env` loads the file into the process environment; the CLI does
not read `.env` on its own. (Equivalently: `set -a && . ./.env && set +a`.) Never
commit your real `.env` — only `.env.example` is committed.

Credential resolution (env is the last link of a broker → secret-manager → keychain
→ config-file → env chain), the `credentials.toml` file, and **profiles** (per-
endpoint/tenant keys) are described in [CREDENTIALS.md](CREDENTIALS.md); per-provider
setup is in [PROVIDERS.md](PROVIDERS.md).

## Live / regression tests (real APIs)

The default suite is fully offline. The `-m live` tests hit real provider APIs and
are **double-gated** so they never run by accident or fail without creds:

```bash
# opt in AND provide the key(s); anything unset simply skips
MEDIA_LIVE_TESTS=1 OPENAI_API_KEY=… uv run --env-file .env pytest -m live -v
```

Each test skips unless `MEDIA_LIVE_TESTS=1` **and** that provider's key is present
(Volc also needs `ARK_IMAGE_MODEL`, since Ark model/endpoint ids are account-specific;
the video smoke needs `MEDIA_LIVE_VIDEO=1` too). Keep them cheap — one small image
per provider.

**CI:** `.github/workflows/ci.yml` runs the offline suite (`-m "not live"`) on every
push/PR. `.github/workflows/live.yml` runs `-m live` **manually only** (Actions →
*live* → *Run workflow*), reading keys from repo **Secrets** (`OPENAI_API_KEY`,
`GEMINI_API_KEY`, `ARK_API_KEY`, plus optional `*_BASE_URL` / `ARK_IMAGE_MODEL` /
`ARK_VIDEO_MODEL`). If no provider secret is configured, the live job is a **green
no-op** (all steps skip), so forks and unconfigured repos never fail. (To run it
automatically on merge to `main` later, uncomment the `push:` trigger in the file.)

## Before you push

```bash
uv run ruff check src tests scripts
uv run pytest -q
uv run actionlint          # only if you touched .github/workflows/
```

All must be green. The suite is fully **offline** — no credentials, no network —
and CI runs the same checks.

`actionlint` is worth the extra command when you edit a workflow: a file GitHub
rejects does not fail a *step*, it fails as a run with **no jobs**, named after the
file path instead of the workflow — and nothing else here catches it, because the
file is still valid YAML. The usual cause is a literal `$`+`{{` appearing anywhere in
the file, comments included: GitHub evaluates its own expressions on the raw text
before the runner ever sees it.

## Releasing

**Actions → “release” → “Run workflow”**, and give it the new version without the
leading `v` (e.g. `0.3.0`). That is the whole procedure. The workflow
([`.github/workflows/release.yml`](../.github/workflows/release.yml)) runs the full
suite, `shellcheck`, and the installer's own tests *before* it touches anything, then
bumps the version (via [`scripts/bump_version.py`](../scripts/bump_version.py)),
commits, tags `v0.3.0`, builds the sdist + wheel, and publishes a GitHub Release with
generated notes and the distributions attached. Tick **dry run** to see everything
except the push.

It refuses to start if the version is malformed, if the tag already exists, or if it
was launched from a branch other than the default one — it tags whatever ref it runs
on, so that last one would otherwise release a feature branch.

A `0.x` version is published as a **pre-release**. `install.sh` lists releases rather
than asking for `/releases/latest` precisely so it still finds them.

### Where the version lives

One place: `__version__` in [`src/media_ai/__init__.py`](../src/media_ai/__init__.py).
`pyproject.toml` declares `dynamic = ["version"]` and reads that attribute, so there
is no second copy to keep in step, and `media-ai --version`, `doctor`, and the install
receipt all report the same string.

The one exception is `DEFAULT_VERSION` in
[`install/install.sh`](../install/install.sh) — the git ref the installer falls back
to when the GitHub releases API is unreachable or rate-limited. It is a *tag name*,
not a package version, so it cannot be derived; the release workflow bumps it, and
[`tests/test_version.py`](../tests/test_version.py) fails in CI if the two ever
disagree. A stale pin silently installs an old CLI for exactly the people whose
network made them fall back to it.

If you ever need to release by hand, bump those two, commit, tag, and push — but
prefer the workflow, which cannot tag a tree whose tests it did not run.

## The lockfile

`uv.lock` is committed so every developer and CI get identical installs. Regenerate
it with `uv lock` after changing dependencies (or let `uv add`/`uv sync` update it).
pip users are unaffected — pip ignores `uv.lock`.

## Fallback without uv

The project is a standard PEP 621 package, so plain pip still works:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" ruff
pytest -q && ruff check src tests
```

## Adding a provider or debugging

- To add a backend, see [EXTENDING.md](EXTENDING.md) (custom providers, RPC/non-HTTP).
- For the big-picture design and diagrams, see [ARCHITECTURE.md](ARCHITECTURE.md).
- Repo conventions and invariants a coding agent should respect are in the
  root [`CLAUDE.md`](../CLAUDE.md).
