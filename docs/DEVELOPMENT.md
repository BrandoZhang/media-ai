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
│       ├── bindings/      # binding manifests (.toml) — what each can do
│       ├── cli/            # per-group CLI front ends
│       ├── core/           # provider-agnostic core (never imports providers/)
│       ├── credentials/    # secret resolution + redaction
│       ├── media/          # local ffmpeg / Pillow helpers
│       ├── providers/      # per-provider adapters — the wire, and nothing else
│       └── skills/         # agent skills (shipped as package data)
├── tests/                  # offline test suite
└── docs/                   # this guide + architecture/credentials/…
```

Because it's a src layout, the package must be **installed** to import it (there is
no `media_ai/` at the repo root to pick up implicitly). `uv sync` / `uv run` handle
that editable install automatically, so nothing changes in day-to-day use. Point
path-taking tools at `src` rather than at the package name. Lint is the one
exception: it runs as `ruff check .` over the whole repo, so a Python file added
outside `src/` (a `scripts/` helper, a `.github/scripts/` CI shim) is covered the day
it lands instead of the day someone remembers to extend a path list.

## Everyday commands

| Task | Command |
|---|---|
| Install / refresh the env | `uv sync` |
| Run the full test suite (offline) | `uv run pytest -q` |
| Run a single test | `uv run pytest -q tests/test_gemini_api.py::test_veo_lro_poll_and_download` |
| Run one file | `uv run pytest -q tests/test_volc_errors.py` |
| Lint | `uv run ruff check .` |
| Autofix lint | `uv run ruff check . --fix` |
| Run the CLI offline | `uv run media-ai image generate --binding mock/mock --prompt "a red bike" --output /tmp/x.png` |
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

## Running against a real backend

`mock/mock` and `local/ffmpeg` need no credentials or network and are always
available. A real backend needs a **binding**, which is one command:

```bash
media-ai bindings available          # what could be added, and the command for each
media-ai bindings add <provider>/<model> --credential env://SOME_API_KEY
media-ai config set-default image.text_to_image <provider>/<model>

export SOME_API_KEY=…                # or use cred:// / keychain:// and skip the env
media-ai image generate --prompt "a fox" --output fox.png
```

A default is set **per scene**, not per modality — `image.text_to_image` and
`image.image_to_image` can point at different bindings, and a request with no binding
flags uses whichever one its inputs imply.

**There is no `.env` support and no env-var fallback.** A binding names exactly one
credential source and nothing else is consulted, so `uv run --env-file .env …` works
only in the sense that it populates the variable an `env://` reference points at.

To keep a dev config out of your real one, point `$MEDIA_CONFIG_FILE` (and
`$MEDIA_CREDENTIALS_FILE`) at a scratch directory:

```bash
export MEDIA_CONFIG_FILE=/tmp/dev/config.toml
export MEDIA_CREDENTIALS_FILE=/tmp/dev/credentials.toml
```

Templates: [`config.toml.example`](../config.toml.example) and
[`credentials.toml.example`](../credentials.toml.example). Reference schemes and the
trust boundary are in [CREDENTIALS.md](CREDENTIALS.md); binding setup is in
[BINDINGS.md](BINDINGS.md).

## Live / regression tests (real APIs)

The default suite is fully offline. The `-m live` tests hit real provider APIs and
are **double-gated** so they never run by accident or fail without creds:

```bash
# opt in AND provide the key(s); anything unset simply skips
MEDIA_LIVE_TESTS=1 OPENAI_API_KEY=… uv run pytest -m live -v
```

Each test skips unless `MEDIA_LIVE_TESTS=1` **and** the binding it exercises resolves
its credential (the video smoke needs `MEDIA_LIVE_VIDEO=1` too). Those variables gate
the *tests*; they are not read by the CLI, which takes everything it knows about a
call from the binding. Keep them cheap — one small artifact per binding.

**CI:** `.github/workflows/ci.yml` runs the offline suite (`-m "not live"`) on every
push/PR. `.github/workflows/live.yml` runs `-m live` **manually only** (Actions →
*live* → *Run workflow*), reading keys from repo **Secrets**.

That job configures itself: it runs `media-ai bindings available`, which reports per
binding the conventional variables its own manifest declares, and adds every binding
whose variable actually holds a value ([`.github/scripts/configure_live_bindings.py`](../.github/scripts/configure_live_bindings.py)).
So a new binding is picked up with no workflow edit, and one whose key is absent is
never configured — its tests skip. With no secrets at all the job is a **green no-op**,
so forks never fail. (To run it on merge to `main` later, uncomment the `push:` trigger.)

## Before you push

```bash
uv run ruff check .
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

**Bump the version in a pull request. Merging it releases.**

```bash
python scripts/bump_version.py 0.3.0     # no leading v
```

That is the whole procedure. On every push to the default branch,
[`.github/workflows/release.yml`](../.github/workflows/release.yml) reads
`__version__`, and if no tag matches it yet, runs the full suite, `shellcheck` and the
installer's own tests, then tags `v0.3.0`, builds the sdist + wheel, and publishes a
GitHub Release with generated notes and the distributions attached. Merge anything that
does *not* change the version and the workflow exits green having done nothing.

The trigger is “this version has no tag yet”, not “the version line changed in this
push”. That is idempotent — a re-run, a failed run, a squash merge and a force-push all
reach the same answer — so a release that failed halfway can simply be re-run.

What stays a human decision is the **number**: nothing can infer from a diff whether a
change is `0.2.1` or `0.3.0`. The *direction* is not a decision, and CI does not leave
it to one — `ci.yml` runs
[`scripts/check_version.py`](../scripts/check_version.py) on every pull request and
fails if the version is behind a released tag, so a bad merge or a bump written against
a stale branch is caught while it is still a pull request. Ordering is semver
precedence, so both `0.2.1` and `0.3.0` are acceptable successors to `0.2.0`, and
`0.3.0-rc1` sorts below `0.3.0`. Leaving the version alone is always fine; that just
means the change ships in the next release.

```bash
python scripts/check_version.py          # what CI will say about this tree
```

A `0.x` version is published as a **pre-release**. `install.sh` lists releases rather
than asking for `/releases/latest` precisely so it still finds them.

### Releasing without a pull request

**Actions → “release” → “Run workflow”** takes an optional version. Give it one and the
job does the bump itself and commits it to the default branch; leave it blank and it
releases whatever version is already in the repo — the way to retry a release whose job
failed after the tests. Tick **dry run** to see everything except the tag and the
publish.

It refuses to start if the version is malformed, if it is behind a release, if the tag
already exists, or if it was launched from a branch other than the default one — it
tags whatever ref it runs on, so that last one would otherwise release a feature
branch.

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
pytest -q && ruff check .
```

## Adding a provider or debugging

- To add a backend, see [EXTENDING.md](EXTENDING.md) (custom providers, RPC/non-HTTP).
- For the big-picture design and diagrams, see [ARCHITECTURE.md](ARCHITECTURE.md).
- Repo conventions and invariants a coding agent should respect are in the
  root [`CLAUDE.md`](../CLAUDE.md).
