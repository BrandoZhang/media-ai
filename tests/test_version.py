"""The version number, and the two places it has to agree with itself.

Releasing means bumping a number, and a number that lives in several files is a
number that will eventually disagree with itself — with the failure showing up as a
wheel labelled one thing and a CLI reporting another. So there is exactly one
declaration (``media_ai.__version__``), ``pyproject.toml`` reads it, and the one
place that legitimately holds a *different* kind of string — the installer's fallback
git ref — is pinned to it here.

These run offline in the normal suite, so a mismatch is caught by CI on the commit
that introduces it rather than by whoever cuts the next release.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

import media_ai

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INSTALLER = ROOT / "install" / "install.sh"
INIT_PATH = ROOT / "src" / "media_ai" / "__init__.py"

# The subset of semver this project actually publishes: MAJOR.MINOR.PATCH, plus an
# optional pre-release suffix. Deliberately strict — `resolve_version` in the
# installer matches tags by name, so a version that cannot be a tag is a broken release.
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?$")

pytestmark = pytest.mark.skipif(
    not PYPROJECT.is_file(), reason="running against an installed package, not a checkout"
)


def pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def installer_default_version() -> str:
    """The ``DEFAULT_VERSION=…`` fallback ref from install.sh."""
    match = re.search(r'^DEFAULT_VERSION="\$\{MEDIA_AI_DEFAULT_VERSION:-([^}]+)\}"', INSTALLER.read_text(), re.M)
    assert match, "install.sh no longer declares DEFAULT_VERSION the way this test reads it"
    return match.group(1)


def test_the_version_is_a_release_number():
    assert _VERSION.match(media_ai.__version__), f"{media_ai.__version__!r} could not be a git tag"


def test_pyproject_takes_the_version_from_the_package():
    """One declaration, read by the build — not a second copy to keep in step."""
    data = pyproject()
    assert "version" in data["project"].get("dynamic", []), "pyproject declares its own version again"
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "media_ai.__version__"}
    assert "version" not in data["project"], "a static version here would shadow the dynamic one"


def test_the_installer_fallback_pin_tracks_the_release():
    """`install.sh` pins a version to fall back to when the releases API is unreachable.

    It is a git *ref*, not a package version, so it cannot be derived — but a stale
    pin silently installs an old CLI for everyone whose network hits that path, which
    is exactly when they are least able to tell.
    """
    assert installer_default_version() == f"v{media_ai.__version__}"


def test_the_version_is_reported_by_the_cli():
    """`media-ai --version` and `doctor` both read the same attribute."""
    from media_ai.cli import doctor

    assert any(media_ai.__version__ in check["detail"] for check in doctor._check_cli())


# ------------------------------------------------------------------- the bump


def load_bump_script():
    """Import ``scripts/bump_version.py`` by path.

    ``scripts/`` is a directory of standalone tools, not an importable package — it
    has no ``__init__.py`` on purpose, so that setuptools never mistakes it for one
    to ship.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("bump_version", ROOT / "scripts" / "bump_version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A copy of the two files the bump rewrites, with the script pointed at them."""
    bump_version = load_bump_script()

    (tmp_path / "src" / "media_ai").mkdir(parents=True)
    (tmp_path / "install").mkdir()
    init, installer = tmp_path / "src" / "media_ai" / "__init__.py", tmp_path / "install" / "install.sh"
    init.write_text(INIT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    installer.write_text(INSTALLER.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(bump_version, "INIT", init)
    monkeypatch.setattr(bump_version, "INSTALLER", installer)
    return bump_version, init, installer


def test_the_bump_rewrites_both_files(sandbox):
    bump_version, init, installer = sandbox
    bump_version.bump("9.8.7")
    assert '__version__ = "9.8.7"' in init.read_text()
    assert 'DEFAULT_VERSION="${MEDIA_AI_DEFAULT_VERSION:-v9.8.7}"' in installer.read_text()


def test_the_bumped_installer_is_still_valid_shell(sandbox):
    """The replacement writes shell parameter-expansion syntax by hand."""
    import subprocess

    bump_version, _init, installer = sandbox
    bump_version.bump("9.8.7")
    assert subprocess.run(["bash", "-n", str(installer)]).returncode == 0


def test_the_bump_leaves_the_rest_of_the_file_alone(sandbox):
    bump_version, init, _installer = sandbox
    before = init.read_text().splitlines()
    bump_version.bump("9.8.7")
    after = init.read_text().splitlines()
    assert len(before) == len(after)
    assert sum(a != b for a, b in zip(before, after)) == 1


def test_the_bump_is_idempotent(sandbox):
    bump_version, _init, _installer = sandbox
    assert bump_version.bump("9.8.7"), "the first bump should report a change"
    assert bump_version.bump("9.8.7") == [], "re-running should report nothing changed"


@pytest.mark.parametrize("bad", ["v0.3.0", "0.3", "latest", "", "0.3.0.0.0-"])
def test_the_bump_refuses_something_that_cannot_be_a_tag(sandbox, bad):
    bump_version, _init, _installer = sandbox
    with pytest.raises(SystemExit):
        bump_version.bump(bad)


def test_the_bump_refuses_a_file_it_cannot_find_the_line_in(sandbox):
    """A pattern that silently matched nothing would leave the release believing it
    had bumped a file it never touched."""
    bump_version, init, _installer = sandbox
    init.write_text("# someone reorganised this\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="exactly one line"):
        bump_version.bump("9.8.7")
