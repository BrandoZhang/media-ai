"""The environment variables this project owns, and the ones it must not touch.

The prefix moved from `MEDIA_` to `MEDIA_AI_` (`core/envvars.py` says why). This file is
what keeps that from being half-done, which is the ordinary fate of a rename: the code
moves, one docstring does not, a shell script that cannot import the constant keeps the
old spelling, and a year later nobody can tell which is authoritative.

Three properties, and each has failed somewhere in some project:

1. every name this project reads carries the prefix, and no call site spells one out;
2. the names we do *not* own are untouched — provider keys, `CI`/`TERM`/`NO_COLOR`,
   `OTEL_*`. Renaming one of those is not tidying, it is breaking somebody else's
   contract;
3. an old name that is set is *reported*, and its value is *not* read. Both halves
   matter: the second is the point of the rename, the first is what stops the change
   from failing silently.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from media_ai.core import envvars

SRC = Path(__file__).resolve().parents[1] / "src" / "media_ai"
ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ one prefix


def test_every_name_carries_the_prefix():
    for name in envvars.NAMES:
        assert name.startswith(envvars.PREFIX), name


def test_names_is_exactly_what_the_module_declares():
    """A variable added as a constant but left out of `NAMES` is left out of `RENAMED`
    and out of the `doctor` check — a rename that is 90% done and reports itself as
    finished."""
    declared = {
        value for key, value in vars(envvars).items()
        if key.isupper() and isinstance(value, str) and value.startswith(envvars.PREFIX)
    }
    assert declared - {envvars.PREFIX} == set(envvars.NAMES)


def test_no_call_site_spells_a_variable_out():
    """`core/envvars.py` is the only place a name is written. Read off the syntax tree
    rather than by grepping text, so prose in a docstring — which is how the *old*
    names are still discussed — stays exempt."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "envvars.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.startswith(envvars.PREFIX) and node not in docstrings):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno} {node.value}")
    assert offenders == [], "use the constant from core.envvars instead:\n" + "\n".join(offenders)


def test_the_shell_halves_agree():
    """`install/install.sh` and `packaging/build.sh` are fetched and run without the
    package, so they cannot import the constant — the same position `CLI_NAME` is in,
    and pinned the same way. They were the prefix's first users; the CLI is what
    followed."""
    for script in ("install/install.sh", "packaging/build.sh"):
        text = (ROOT / script).read_text(encoding="utf-8")
        found = set(re.findall(r"\bMEDIA_[A-Z_]+", text))
        stale = {n for n in found if not n.startswith(envvars.PREFIX)}
        assert not stale, f"{script} still spells {sorted(stale)}"


def test_no_stale_prefix_anywhere_a_user_reads():
    """Docs, examples and workflows. A stale name in a README is worse than one in code:
    somebody copies it, it does nothing, and the tool looks broken rather than the doc."""
    # Files that discuss the old names rather than instructing anyone to set one.
    exempt_files = {
        "src/media_ai/core/envvars.py",  # states what the prefix used to be, and why
        "src/media_ai/cli/common.py",    # ditto, in the notice's docstring
        "tests/test_envvars.py",         # this file names old spellings to assert on them
        "AGENTS.md", "CLAUDE.md",        # the entry explaining why the prefix moved
        "docs/history",                  # a record of what was, not instructions
    }
    # Variables that never existed under the new prefix because they no longer exist at
    # all. `$MEDIA_PROVIDER` was an implicit default that resolution deliberately
    # dropped; two docstrings explain the removal, and renaming a name in that sentence
    # would invent a variable this CLI has never had.
    exempt_names = {"MEDIA_PROVIDER", "MEDIA_PROFILE", "MEDIA_TEST_KEY", "MEDIA_TEST_FLAG"}
    stale = []
    for path in ROOT.rglob("*"):
        rel = str(path.relative_to(ROOT))
        if (not path.is_file() or path.suffix not in {".md", ".toml", ".yml", ".example", ".sh", ".py"}
                or rel.startswith((".git/", ".venv/", "dist/"))
                or any(rel.startswith(x) for x in exempt_files)):
            continue
        for name in re.findall(r"\bMEDIA_[A-Z_]+", path.read_text(encoding="utf-8", errors="ignore")):
            if not name.startswith(envvars.PREFIX) and name not in exempt_names:
                stale.append(f"{rel}: {name}")
    assert stale == [], "\n".join(stale)


# -------------------------------------------------------- what we do not own


@pytest.mark.parametrize("name", [
    "CI", "TERM", "NO_COLOR",                       # other people's conventions
    "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_SERVICE_NAME",  # a specification
    "ARK_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY",  # provider keys
])
def test_names_belonging_to_somebody_else_are_untouched(name):
    """A user has these exported already, and a manifest names a key by its provider's
    spelling. Prefixing them would not be consistency, it would be a rename of somebody
    else's variable."""
    assert name not in envvars.NAMES
    assert not name.startswith(envvars.PREFIX)
    # Manifests too: a provider key is declared as `env://ARK_API_KEY` in a `.toml`,
    # which is exactly the point — the spelling is the provider's, not ours.
    read_somewhere = any(
        name in path.read_text(encoding="utf-8")
        for suffix in ("*.py", "*.toml") for path in SRC.rglob(suffix)
    )
    assert read_somewhere, \
        f"{name} is no longer read anywhere; drop it from this list rather than leaving it asserted"


# ------------------------------------------------------- the old names, once


def test_every_new_name_has_an_old_one_and_they_derive():
    assert set(envvars.RENAMED.values()) == set(envvars.NAMES)
    for old, new in envvars.RENAMED.items():
        assert old == "MEDIA_" + new[len(envvars.PREFIX):]


def test_an_old_name_is_reported(monkeypatch):
    monkeypatch.setenv("MEDIA_CONFIG_FILE", "/somewhere")
    monkeypatch.delenv(envvars.CONFIG_FILE, raising=False)
    assert envvars.legacy_in_use() == {"MEDIA_CONFIG_FILE": envvars.CONFIG_FILE}


def test_an_old_name_is_not_honoured(tmp_path, monkeypatch):
    """The point of the rename. Reading it "just this once" is the compatibility layer
    this was done to avoid — and the value here would send the CLI to a config file it
    was told to stop reading."""
    from media_ai.core.config import config_path

    monkeypatch.delenv(envvars.CONFIG_FILE, raising=False)
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "legacy.toml"))
    assert config_path() != tmp_path / "legacy.toml"


def test_setting_both_is_not_worth_a_warning(monkeypatch):
    """Somebody mid-migration has already answered. Nagging about a variable that is
    being correctly ignored is how a warning teaches people to ignore warnings."""
    monkeypatch.setenv("MEDIA_CONFIG_FILE", "/old")
    monkeypatch.setenv(envvars.CONFIG_FILE, "/new")
    assert envvars.legacy_in_use() == {}


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_old_name_says_nothing(monkeypatch, value):
    """`FOO=""` is what unsetting looks like in a shell that cannot unset — the same
    reading `core/envflag.py` gives it."""
    monkeypatch.setenv("MEDIA_CONFIG_FILE", value)
    monkeypatch.delenv(envvars.CONFIG_FILE, raising=False)
    assert envvars.legacy_in_use() == {}


def test_the_warning_reaches_both_surfaces(tmp_path, monkeypatch, capsys):
    """The notice rides whatever command they happened to run; `doctor` is where
    somebody goes *because* something is behaving oddly. A scroll-back can swallow the
    first, and "my config setting is being ignored" is the symptom that produces the
    second."""
    import sys

    from media_ai.cli import doctor
    from media_ai.core import notices

    monkeypatch.setenv("MEDIA_TELEMETRY", "0")
    monkeypatch.delenv(envvars.TELEMETRY, raising=False)
    notices.clear()
    old, sys.argv = sys.argv, ["media-ai doctor"]
    try:
        doctor.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    import json

    payload = json.loads(out.strip().splitlines()[-1])
    assert any(c["check"] == "environment" and c["status"] == "warn" for c in payload["checks"])
    assert any(n["kind"] == "env_renamed" for n in payload.get("notices", []))
    notices.clear()
