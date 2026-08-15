"""The Compatibility section: it has to keep finding the numbers it claims to watch.

The failure mode this file exists for is silence. `compat_notes.py` locates each
versioned thing with a regex against a path, so a constant that is renamed, moved to
another module or reformatted stops matching — and the script goes on succeeding,
printing nothing, on every release from then on. Nobody notices a section that was
never there, which is the same shape as `_verify`'s probe swallowing its exceptions
and the reason `test_init.py` drives it against a fake transport.

So the load-bearing test is `test_every_watched_number_is_actually_found`: it reads
each pattern against the file it names and checks the answer equals what the package
itself exposes. Everything else here is the rendering, which is pure.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from media_ai.core.config import SCHEMA as CONFIG_SCHEMA
from media_ai.core.result import SCHEMA_VERSION
from media_ai.core.update import FEED_SCHEMA
from media_ai.credentials.stores import SCHEMA as CREDENTIALS_SCHEMA

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def compat():
    """Import the script by path — `scripts/` is deliberately not a package.

    Registered in ``sys.modules`` before it executes: the module defines dataclasses
    under ``from __future__ import annotations``, and ``@dataclass`` resolves the
    annotations through ``sys.modules[cls.__module__]``, which is not there yet
    otherwise.
    """
    import sys

    spec = importlib.util.spec_from_file_location("compat_notes", ROOT / "scripts" / "compat_notes.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[spec.name]
        raise
    yield module
    del sys.modules[spec.name]


# ------------------------------------------------------------- it finds the numbers


def test_every_watched_number_is_actually_found(compat):
    """A pattern that no longer matches makes the whole section vanish, quietly."""
    for watched in compat.WATCHED:
        text = (ROOT / watched.path).read_text(encoding="utf-8")
        found = compat.value(text, watched.pattern)
        assert found is not None, f"{watched.label}: {watched.pattern!r} no longer matches {watched.path}"


def test_the_numbers_found_are_the_ones_the_package_uses(compat):
    """Matching *something* is not enough — it has to be the constant in effect."""
    actual = {
        "src/media_ai/core/result.py": str(SCHEMA_VERSION),
        "src/media_ai/core/config.py": str(CONFIG_SCHEMA),
        "src/media_ai/credentials/stores.py": str(CREDENTIALS_SCHEMA),
        "src/media_ai/core/update.py": str(FEED_SCHEMA),
    }
    for watched in compat.WATCHED:
        text = (ROOT / watched.path).read_text(encoding="utf-8")
        assert compat.value(text, watched.pattern) == actual[watched.path], watched.label


def test_every_versioned_thing_in_the_package_is_watched(compat):
    """The list is the point of the script; a fifth number nobody added is a hole."""
    assert {w.path for w in compat.WATCHED} == {
        "src/media_ai/core/result.py",
        "src/media_ai/core/config.py",
        "src/media_ai/credentials/stores.py",
        "src/media_ai/core/update.py",
    }


def test_an_absent_file_reads_as_absent_not_as_an_error(compat):
    """Every constant was introduced at some point; the release that does it is a
    release worth a note, not a crash."""
    assert compat.value(None, r"^SCHEMA = (\d+)$") is None


# --------------------------------------------------------------------- rendering


def change(compat, label="`config.toml` (`schema`)", before="2", after="3", why="…"):
    return compat.Change(label, before, after, why)


def test_nothing_moved_prints_nothing(compat):
    """Silence is the design. A section that appears every release saying "no change"
    is read once and skipped forever, which costs the attention the real one needs.
    """
    assert compat.render("v1.0.0", [], (None, None)) == ""


def test_a_moved_number_becomes_a_row(compat):
    out = compat.render("v1.0.0", [change(compat)], (None, None))
    assert "## Compatibility" in out
    assert "| `config.toml` (`schema`) | `2` | `3` |" in out
    assert "v1.0.0" in out


def test_a_new_floor_is_stated_in_full(compat):
    """The heaviest thing a release can carry, so it gets prose rather than a cell."""
    out = compat.render("v1.0.0", [], (None, "0.7.0"))
    assert "Minimum supported version is now `0.7.0`" in out
    assert "version_unsupported" in out
    # Which commands keep working matters as much as which stop: a floor that locked a
    # user out of `doctor` and `upgrade` is one they answer by deleting the install.
    assert "upgrade" in out and "doctor" in out


def test_an_unchanged_floor_is_not_restated(compat):
    assert compat.render("v1.0.0", [], ("0.7.0", "0.7.0")) == ""


def test_a_lifted_floor_is_worth_saying(compat):
    out = compat.render("v1.0.0", [], ("0.7.0", None))
    assert "lifted" in out


def test_a_number_that_did_not_exist_before_is_labelled_as_new(compat):
    """Not "an older build refuses this file" — before the number existed there was
    nothing to disagree with, and how an older build reads a key it never heard of is
    a claim about code nobody checked.
    """
    row = change(compat, before="—", after="1", why=compat.INTRODUCED)
    out = compat.render("v1.0.0", [row], (None, None))
    assert compat.INTRODUCED in out
    assert "refuses" not in out


# ---------------------------------------------------------------- the tag it picks


def test_the_base_tag_is_picked_by_precedence(compat, monkeypatch):
    """`sorted()` on the names answers v0.9.0 here. Tags are made by a workflow, so a
    re-run or a hotfix can create them out of date order.
    """
    monkeypatch.setattr(compat, "git", lambda *a: "v0.2.0\nv0.10.0\nv0.9.0\nnot-a-tag\n")
    assert compat.previous_tag() == "v0.10.0"


def test_no_tags_at_all_is_not_a_failure(compat, monkeypatch):
    """A first release has nothing to be compatible with."""
    monkeypatch.setattr(compat, "git", lambda *a: "\n")
    assert compat.previous_tag() is None


def test_the_version_being_released_is_not_its_own_base(compat, monkeypatch):
    """The regression that cost v0.7.0 its Compatibility section.

    The workflow pushes the tag before it generates the notes, so by the time this runs
    the highest tag *is* the release being described. Comparing the tree against itself
    finds nothing moved, and "nothing moved" is spelled as an empty document — so the
    section vanished from a release that introduced two schema numbers, and every
    later release would have gone the same way.
    """
    monkeypatch.setattr(compat, "git", lambda *a: "v0.6.0\nv0.7.0\n")
    assert compat.previous_tag("0.7.0") == "v0.6.0"


def test_a_tag_ahead_of_this_tree_is_not_a_base_either(compat, monkeypatch):
    """Strictly below, not merely "not equal".

    Releases only ever go forwards, so a higher tag means a checkout that is behind —
    and the honest answer for what *this* version changed is still the release under it,
    not a diff against work it does not contain.
    """
    monkeypatch.setattr(compat, "git", lambda *a: "v0.6.0\nv0.7.0\nv0.8.0\n")
    assert compat.previous_tag("0.7.0") == "v0.6.0"


def test_this_version_being_the_only_tag_leaves_nothing_to_compare(compat, monkeypatch):
    """Not a crash, and not a base of itself: there is genuinely no earlier release."""
    monkeypatch.setattr(compat, "git", lambda *a: "v0.7.0\n")
    assert compat.previous_tag("0.7.0") is None


def test_a_pre_release_still_compares_against_the_release_below_it(compat, monkeypatch):
    """`0.8.0-rc1` sorts under `0.8.0` and over `0.7.0`, which is the point of §11."""
    monkeypatch.setattr(compat, "git", lambda *a: "v0.7.0\nv0.8.0-rc1\n")
    assert compat.previous_tag("0.8.0-rc1") == "v0.7.0"
    assert compat.previous_tag("0.8.0") == "v0.8.0-rc1"


def test_the_declared_version_is_the_one_the_package_reports(compat):
    """Same failure shape as the watched patterns: this locates `__version__` with a
    regex, so moving or reformatting the declaration would send `previous_tag` back to
    "the highest tag that exists" — silently, and only visible in a shipped release.
    """
    from media_ai import __version__

    assert compat.declared_version() == __version__
