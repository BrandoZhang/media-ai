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
├── install/                # install.sh (curl | bash) + its offline unit tests
├── packaging/              # the standalone bundle: PyInstaller spec + build.sh
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
| Installer unit tests (offline) | `bash install/test_installer.sh` |
| Lint the shell scripts | `uv run shellcheck install/*.sh packaging/build.sh` |
| Build the standalone bundle for this machine | `bash packaging/build.sh` |

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
push/PR, on **every Python `requires-python` admits** — 3.11 through 3.14 — plus one
job on macOS. It is two jobs split by one question: does the answer depend on which
interpreter is running? The suite does; `ruff` (whose target comes from
`pyproject.toml`), `shellcheck`, `actionlint` and the version check do not, so they run
once. `fail-fast: false`, because "3.14 only" and "everything past 3.11" are different
problems and the matrix is the only thing that can tell them apart.

The same question decides the operating system and gives a different answer. What
differs on macOS is the platform, not the interpreter — `termios` and `/dev/tty` for
the wizard, file modes, and which `ffmpeg` binary `imageio-ffmpeg` unpacks — none of
which varies by Python version. So macOS is **one** job on the floor version, not a
second column: a cross-product would buy three more answers to a question already
answered. Windows is not covered; `_prompt` degrades to the numbered menu there
(`termios` is absent) but `install.sh` is bash, so the installed path is POSIX.

`.github/workflows/live.yml` runs `-m live` **manually only** (Actions → *live* →
*Run workflow*), reading keys from repo **Secrets**.

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
uv run shellcheck install/*.sh packaging/build.sh   # only if you touched a shell script
bash install/test_installer.sh                      # …and these, which run it for real
```

All must be green. The suite is fully **offline** — no credentials, no network —
and CI runs the same checks.

`shellcheck` comes from the dev group rather than from your system, and that is worth
one line of explanation: its diagnostic codes and their severities move between
releases — an overridden function is `SC2317` in 0.10 and `SC2329` in 0.11 — so a
system binary and CI's disagreeing is a build that fails only on the runner. Pinning it
through the same lockfile as everything else is what makes `uv run shellcheck` the same
command in both places.

`actionlint` is worth the extra command when you edit a workflow: a file GitHub
rejects does not fail a *step*, it fails as a run with **no jobs**, named after the
file path instead of the workflow — and nothing else here catches it, because the
file is still valid YAML. The usual cause is a literal `$`+`{{` appearing anywhere in
the file, comments included: GitHub evaluates its own expressions on the raw text
before the runner ever sees it.

## The standalone bundle

The way most people get this CLI is a tarball that needs no Python:

```bash
bash packaging/build.sh                    # -> dist/media-ai-<version>-<os>-<arch>.tar.gz (+ .sha256)
bash packaging/build.sh --python 3.12      # pick the interpreter that goes inside it
bash install/install.sh --from-file dist/media-ai-*.tar.gz --no-init   # install what you just built
```

`build.sh` makes a throwaway virtualenv, installs the project plus a pinned PyInstaller
into it, freezes it with [`packaging/standalone.spec`](../packaging/standalone.spec),
then **runs the result** — an image through Pillow, a clip and an animation through the
bundled ffmpeg, and `doctor` — before it packs anything. It builds for `uname` and takes
no target: a bundle carries a compiled interpreter, Pillow's extension modules and an
ffmpeg binary, and none of those cross-compile. CI builds one on Linux for every pull
request; the release workflow builds all four (linux/macOS × x86_64/arm64) and attaches
them to the release before it is published.

Three things are worth knowing before touching the spec:

- **Nothing about the bundle is discovered.** Binding manifests and skills are data
  files, and adapters are imported from strings in those manifests, so both are named
  explicitly (`collect_data_files`, `collect_submodules`). Get either wrong and you get
  a bundle that starts, answers `--version`, and fails on the first real command.
- **Which extras go in is a decision, made in `BUNDLE_EXTRAS`.** An extra exists because
  not every install should carry it — which assumes a later moment to add one, and a
  freeze has none. Today `otel` is in and `keychain` is out, with the reasoning written
  beside the variable. The build turns telemetry on with the console exporter and fails
  unless a span reaches stderr, because a bundle that stopped collecting OpenTelemetry
  degrades *politely* to a no-op and would pass every other check.
- **The installer ships inside it.** A standalone install has no package manager, so
  `upgrade` and `uninstall` run `bash <bundle>/_internal/install.sh`
  ([`cli/_install.py`](../src/media_ai/cli/_install.py)). That is also why an upgrade
  writes a new `versions/<version>` directory and moves a symlink instead of overwriting
  anything: the installer is running from inside the bundle it is replacing.
- **The asset name is a contract.** `packaging/build.sh` and `install/install.sh` each
  carry a byte-identical copy of the `asset naming` block, because one is fetched alone
  by curl and the other runs from a checkout — there is no third file to share.
  `tests/test_packaging.py` compares them; edit both.

What a bundle deliberately cannot do — the `keychain` extra, third-party binding
plugins, musl — is in [LIMITATIONS.md](LIMITATIONS.md#the-standalone-bundle), and
`--from-source` is the answer to all of it.

## Releasing

**Bump the version in a pull request. Merging it releases.**

```bash
python scripts/bump_version.py 0.3.0     # no leading v
```

That is the whole procedure. On every push to the default branch,
[`.github/workflows/release.yml`](../.github/workflows/release.yml) reads
`__version__`, and if no tag matches it yet, builds the four standalone bundles, runs
the full suite, `shellcheck` and the installer's own tests, then tags `v0.3.0`, builds
the sdist + wheel, and publishes a GitHub Release with generated notes, the
distributions **and the bundles** attached. Merge anything that does *not* change the
version and the workflow exits green having done nothing.

The bundles are built before the release exists rather than attached to it afterwards,
which is why the workflow is three jobs (`plan` → `bundle` → `release`) rather than one.
A release that appeared while four runners were still building would answer 404 to
everybody whose `install.sh` reached it in that window — and the release feed would
already be naming it as the latest version.

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

### Compatibility notes

The release notes lead with a **Compatibility** section, above the generated changelog,
written by [`scripts/compat_notes.py`](../scripts/compat_notes.py):

```bash
python scripts/compat_notes.py           # what this tree would add to the notes
python scripts/compat_notes.py v0.5.2    # …compared against a specific release
```

A changelog built from commit subjects reports the release version and says nothing
about the other four numbers — the config schema, the credentials schema, the result
`schema_version`, the feed schema — which are the ones that can break something a user
already has. So the script reads those constants out of the previous release's tree,
compares them with this one's, and writes a row for each that moved, plus the feed's
`min_supported` if a floor was set or lifted.

**It prints nothing when nothing moved.** A section that appears every release saying
"no change" is read once and skipped forever, which costs exactly the attention the one
that matters needs.

It reports; it does not judge. No diffing of code, no guessing at severity, no
"breaking" label — every line is checkable against two git trees.
`tests/test_compat_notes.py` keeps each pattern matching the constant it claims to
watch, because the failure mode of a renamed constant is that the section silently
stops appearing.

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

### The release feed

[`release-feed.json`](../release-feed.json) at the repo root is the one document this
project publishes *at* its users, served from the default branch:

```
https://raw.githubusercontent.com/BrandoZhang/media-ai/main/release-feed.json
```

Raw rather than the releases API: that API allows about 60 anonymous requests per hour
per IP (the reason `install.sh` has a `DEFAULT_VERSION` to fall back to), and one office
behind a shared NAT would exhaust it. The feed also carries things a version number
cannot — an announcement, a binding that has been retired upstream.

**Its readers are always older than it is**, and can never be upgraded to understand a
change: they are already installed on other people's machines. So the shape only ever
*grows*. A new key is invisible to an old reader; a renamed or re-meaninged one is a lie
to every reader already out there. `schema` exists so a client can stop reading rather
than misread, and bumping it abandons everyone who has not upgraded — treat it as fixed
at 1 and add fields beside the old ones instead.

`latest` is generated: `scripts/bump_version.py` writes it, so the feed moves in the
same reviewed pull request as `__version__` rather than being committed afterwards by
the release job — the automatic release path never writes to the default branch, and
that is what keeps it clear of branch protection.

Everything else is policy, hand-edited in a pull request like any other change:

| field | meaning |
|---|---|
| `min_supported` | versions below this should refuse to run. **Absent means no floor** — the ordinary state |
| `notices[]` | `{id, severity, title, body}`, optionally narrowed by `min_version`/`max_version`. Display-only |
| `retired_bindings[]` | `{binding, since, severity, reason}` plus `alternatives`/`fixed_in`. `severity` is `warn` or `block` |

Applicability is always **two explicit version fields, never an expression** like
`"<0.6.0"`. A range language would be parsed by the *old* clients, and one that
misparses does not fail loudly — it applies a notice, or a block, to the wrong people.

`tests/test_release_feed.py` enforces all of the above, including that the file is
stored exactly as the writer produces it, so a bump never lands a formatting diff on top
of the version it came for.

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

## Renaming the CLI (white-label / side-by-side installs)

The command name is a **build-time** constant, declared once in
[`src/media_ai/brand.py`](../src/media_ai/brand.py):

```python
CLI_NAME = "media-ai"
```

Change it, change the two lines that repeat it because they are static files, rebuild,
and the whole visible surface follows — the executable, every `prog=` in `--help`,
every `error.hint`, the `poll` command on an async job, the config directory, the
Agent Skill directory names, and the text inside the skills themselves:

```bash
# 1. src/media_ai/brand.py       CLI_NAME = "studio-media"
# 2. pyproject.toml              name = "studio-media"  and  [project.scripts] key
# 3. install/install.sh          CLI_NAME="studio-media"
uv run pytest -q tests/test_brand.py     # fails until all three agree
uv tool install --force .
studio-media doctor
```

`tests/test_brand.py` is what makes that list exhaustive **for anything a user or an
agent can see**: it pins the static files to the constant, forbids the literal name in
any non-docstring string in `src/`, forbids it entirely in the packaged skills, and
then renames the build and asserts the visible surface moved with it. Adding a fourth
place for the name to hide in the shipped product fails CI.

Repo infrastructure is deliberately outside that guarantee and stays as-is until you
choose otherwise: the prose in `README.md` / `docs/` / `*.toml.example`, the
`media-ai-*` patterns in `.gitignore` (they keep locally-installed skills out of this
checkout's diffs), `REPO` in the installer, and the docstrings in `src/` — all of which
describe *this* reference build rather than instructing a machine.

**Why not a setting in `config.toml` or an environment variable.** The executable's
name is fixed when the wheel is built, so a runtime knob could only ever disagree with
it: the binary would be `studio-media` while `error.hint` — documented as *usually
runnable* — still said `media-ai`, and the packaged skills, whose commands an agent
executes verbatim, would tell it to run a command that does not exist. One source of
truth, resolved before anything ships, is the only version of this that cannot lie.
Deriving it from `sys.argv[0]` has the same flaw in slower motion: skill text is
rendered to disk once at `init`, while hints are rendered per call, so an alias or a
wrapper script makes the two disagree.

### What a rename does and does not cover

| Follows the brand | Stays fixed |
|---|---|
| the executable, and the distribution `uv tool` keys by | `media_ai`, the **import package** — the resource root for `skills/`+`bindings/` and the `media_ai.bindings` entry-point group third-party manifests register under |
| `error.hint`, `--help`, `meta.poll`, wizard and `doctor` output | `MEDIA_CONFIG_FILE`, `MEDIA_CREDENTIALS_FILE`, `MEDIA_USAGE_LOG` … — these name a *modality*, not a brand, and each is a per-invocation override rather than a namespace, so renaming them would break callers' CI for no isolation gained |
| `~/.config/<brand>/` — config, credentials, install receipt | `REPO` in `install/install.sh`: where the code comes from is not what the tool is called |
| installed skill directories (`<brand>-image`), their cross-references, and their `needs:` edges | the packaged directories, which are named for the command group (`skills/image/`) |
| the default keychain service name | the scene ids, binding ids, and the result schema |

Two consequences worth knowing before you rely on them:

- **Two brands can share an agent's skills directory**, and each `uninstall` removes
  only its own prefix. That is the point — but it also means a `studio-media uninstall`
  leaves any `media-ai-*` skills sitting beside it, and neither build reports the
  other's: each one scans for its own prefix and can see nothing else. Remove them by
  running that build's own `uninstall`. (Within one brand, `doctor` still names
  installed skills the running version does not ship.)
- **The packaged skills are templates.** They say `{{cli}}` and `{{skill}}`, rendered
  by `copy_skill` on the way to disk. Symlinking a packaged directory into an agent's
  skills folder — which older versions of `skills/README.md` suggested — now puts a
  literal `{{cli}}` in front of the agent, so `skill_is_current` reports such a link as
  drifted and `init` replaces it with a rendered copy.

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
