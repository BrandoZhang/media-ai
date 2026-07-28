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


def load_script(name: str):
    """Import a file from ``scripts/`` by path.

    ``scripts/`` is a directory of standalone tools, not an importable package — it
    has no ``__init__.py`` on purpose, so that setuptools never mistakes it for one
    to ship.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_bump_script():
    return load_script("bump_version")


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


# --------------------------------------------------------- which way it points

# Releases follow `__version__`, so the number can only go forwards: a version that
# went backwards would either collide with a published tag or ship a release that
# sorts below its predecessor. `scripts/check_version.py` is what CI runs on every
# pull request to say so before the merge rather than after the tag.


@pytest.fixture
def check_version():
    return load_script("check_version")


# semver.org §11, in the order the spec gives them. Each pair is (lower, higher).
ORDERED = [
    ("0.2.0", "0.2.1"),
    ("0.2.1", "0.3.0"),
    ("0.9.0", "0.10.0"),  # not string order
    ("0.9.9", "1.0.0"),
    ("1.0.0-alpha", "1.0.0"),  # a pre-release precedes the release it leads to
    ("1.0.0-alpha", "1.0.0-alpha.1"),  # more identifiers wins the tie
    ("1.0.0-alpha.1", "1.0.0-alpha.beta"),  # numeric sorts below alphanumeric
    ("1.0.0-alpha.beta", "1.0.0-beta"),
    ("1.0.0-rc.2", "1.0.0-rc.10"),  # numeric identifiers compare numerically
    ("1.0.0-rc1", "1.0.0"),
]


@pytest.mark.parametrize("lower,higher", ORDERED)
def test_precedence_follows_semver(check_version, lower, higher):
    assert check_version.precedence(lower) < check_version.precedence(higher)


def test_the_highest_release_is_picked_by_precedence_not_by_name(check_version):
    """`sorted()` on the tag names would answer v0.9.0 here."""
    assert check_version.highest(["v0.2.0", "v0.10.0", "v0.9.0"]) == "0.10.0"


def test_tags_that_are_not_releases_are_ignored(check_version):
    assert check_version.highest(["v0.2.0", "nightly", "v-broken", "vlatest"]) == "0.2.0"


def test_no_tags_at_all_is_the_first_release(check_version):
    assert check_version.highest([]) is None
    assert "first release" in check_version.check("0.1.0", [])


@pytest.mark.parametrize("version", ["0.2.1", "0.3.0", "1.0.0", "0.3.0-rc1"])
def test_a_version_ahead_of_the_latest_tag_is_a_release(check_version, version):
    """The two the question is usually about — a patch and a minor — plus the edges."""
    assert "ahead" in check_version.check(version, ["v0.1.0", "v0.2.0"])


def test_the_version_of_the_current_release_ships_nothing_new(check_version):
    """The ordinary state of the default branch between releases: not an error."""
    assert "nothing new" in check_version.check("0.2.0", ["v0.1.0", "v0.2.0"])


@pytest.mark.parametrize("version", ["0.1.0", "0.1.9", "0.2.0-rc1"])
def test_a_version_behind_a_release_is_refused(check_version, version):
    """Including one that only *looks* new: 0.2.0-rc1 comes before the released 0.2.0."""
    with pytest.raises(SystemExit, match="behind the released v0.2.0"):
        check_version.check(version, ["v0.1.0", "v0.2.0"])


@pytest.mark.parametrize("bad", ["v0.3.0", "0.3", "latest", "", "0.3.0.0.0-"])
def test_the_check_refuses_something_that_cannot_be_a_tag(check_version, bad):
    with pytest.raises(SystemExit):
        check_version.check(bad, ["v0.2.0"])


def test_the_declared_version_is_read_without_importing_the_package(check_version):
    """It runs before anything is installed, in CI's first step."""
    assert check_version.declared_version() == media_ai.__version__


def test_this_checkout_is_not_behind_its_own_releases(check_version):
    """The real check, when the clone has the tags to run it against."""
    tags = check_version.released_tags()
    if not tags:
        pytest.skip("no tags fetched in this checkout")
    check_version.check(media_ai.__version__, tags)
