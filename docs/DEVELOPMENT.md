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

## Everyday commands

| Task | Command |
|---|---|
| Install / refresh the env | `uv sync` |
| Run the full test suite (offline) | `uv run pytest -q` |
| Run a single test | `uv run pytest -q tests/test_gemini_api.py::test_veo_lro_poll_and_download` |
| Run one file | `uv run pytest -q tests/test_volc_errors.py` |
| Lint | `uv run ruff check media_ai tests` |
| Autofix lint | `uv run ruff check media_ai tests --fix` |
| Run the CLI (mock by default) | `uv run media-ai image generate --prompt "a red bike" --output /tmp/x.png` |
| Run a legacy shim | `uv run text2image --prompt "x" --output /tmp/x.png` |
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
uv run ruff check media_ai tests
uv run pytest -q
```

Both must be green. The suite is fully **offline** — no credentials, no network —
and CI runs the same `ruff` + `pytest`.

## The lockfile

`uv.lock` is committed so every developer and CI get identical installs. Regenerate
it with `uv lock` after changing dependencies (or let `uv add`/`uv sync` update it).
pip users are unaffected — pip ignores `uv.lock`.

## Fallback without uv

The project is a standard PEP 621 package, so plain pip still works:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" ruff
pytest -q && ruff check media_ai tests
```

## Adding a provider or debugging

- To add a backend, see [EXTENDING.md](EXTENDING.md) (custom providers, RPC/non-HTTP).
- For the big-picture design and diagrams, see [ARCHITECTURE.md](ARCHITECTURE.md).
- Repo conventions and invariants a coding agent should respect are in the
  root [`CLAUDE.md`](../CLAUDE.md).
