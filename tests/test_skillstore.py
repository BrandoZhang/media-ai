"""Installed skills on disk: syncing them out, and deciding when they are current.

``skill_is_current`` is load-bearing twice over — it is what makes a re-run quiet, and
what ``doctor`` reports drift from — so the cases where it can get stuck saying "not
current" forever are the ones worth pinning.
"""

from __future__ import annotations

import pytest

from media_ai.cli._discovery import skill_root
from media_ai.cli._skillstore import copy_skill, installed_skills, skill_is_current


@pytest.fixture
def dest(tmp_path):
    return tmp_path / "skills"


def test_a_fresh_copy_is_current(dest):
    copy_skill("media-ai-image", dest)
    assert skill_is_current(dest, "media-ai-image")


def test_an_edited_copy_is_not_current(dest):
    copy_skill("media-ai-image", dest)
    (dest / "media-ai-image" / "SKILL.md").write_text("mine", encoding="utf-8")
    assert not skill_is_current(dest, "media-ai-image")


def test_a_reference_file_edit_is_noticed(dest):
    """The whole tree is compared, not just SKILL.md — the references are instructions
    too, and the installer's decision to rewrite has to see them."""
    copy_skill("media-ai-image", dest)
    (dest / "media-ai-image" / "references" / "generate.md").write_text("mine", encoding="utf-8")
    assert not skill_is_current(dest, "media-ai-image")


def test_installing_over_an_edited_copy_makes_it_current_again(dest):
    copy_skill("media-ai-image", dest)
    (dest / "media-ai-image" / "SKILL.md").write_text("mine", encoding="utf-8")
    copy_skill("media-ai-image", dest)
    assert skill_is_current(dest, "media-ai-image")


class TestStaleFilesAreRemoved:
    """A file the packaged skill no longer ships must not survive an update.

    Only ever adding would leave it there forever, and since "current" compares whole
    trees the skill would look modified on every future run — so the installer would
    keep asking about a collision that answering cannot resolve.
    """

    def test_a_stale_file_is_dropped(self, dest):
        copy_skill("media-ai-image", dest)
        (dest / "media-ai-image" / "references" / "gone.md").write_text("from an older release", encoding="utf-8")
        copy_skill("media-ai-image", dest)
        assert not (dest / "media-ai-image" / "references" / "gone.md").exists()

    def test_and_the_skill_converges(self, dest):
        copy_skill("media-ai-image", dest)
        (dest / "media-ai-image" / "stale.md").write_text("x", encoding="utf-8")
        assert not skill_is_current(dest, "media-ai-image")
        copy_skill("media-ai-image", dest)
        assert skill_is_current(dest, "media-ai-image"), "update left the copy permanently 'modified'"

    def test_a_stale_directory_is_dropped_too(self, dest):
        copy_skill("media-ai-image", dest)
        (dest / "media-ai-image" / "old").mkdir()
        (dest / "media-ai-image" / "old" / "a.md").write_text("x", encoding="utf-8")
        copy_skill("media-ai-image", dest)
        assert not (dest / "media-ai-image" / "old").exists()


class TestSymlinkedInstalls:
    """`skills/README.md` documents symlinking the packaged directories."""

    def test_a_resolving_symlink_is_current(self, dest):
        dest.mkdir(parents=True)
        (dest / "media-ai-image").symlink_to(str(skill_root("media-ai-image")))
        assert skill_is_current(dest, "media-ai-image")

    def test_a_dangling_symlink_is_not_current(self, dest, tmp_path):
        """It is broken, not up to date — blessing it would have `doctor` report a
        skill the agent cannot read as fine."""
        dest.mkdir(parents=True)
        (dest / "media-ai-image").symlink_to(tmp_path / "nowhere")
        assert not skill_is_current(dest, "media-ai-image")

    def test_installing_over_a_dangling_symlink_works(self, dest, tmp_path):
        """`mkdir(exist_ok=True)` raises on a broken link, which used to blow up in
        the middle of the apply phase — after earlier skills were already written."""
        dest.mkdir(parents=True)
        (dest / "media-ai-image").symlink_to(tmp_path / "nowhere")
        copy_skill("media-ai-image", dest)
        assert (dest / "media-ai-image" / "SKILL.md").is_file()
        assert not (dest / "media-ai-image").is_symlink()

    def test_a_dangling_symlink_is_still_listed_as_installed(self, dest, tmp_path):
        dest.mkdir(parents=True)
        (dest / "media-ai-image").symlink_to(tmp_path / "nowhere")
        assert installed_skills(dest) == ["media-ai-image"]


def test_the_packaged_tree_is_read_once_per_skill(dest, monkeypatch):
    """Every (destination, skill) pair compares against the same packaged files."""
    import media_ai.cli._skillstore as store

    store._packaged_tree.cache_clear()
    reads = []
    real = store._tree
    monkeypatch.setattr(store, "_tree", lambda root: (reads.append(str(root)), real(root))[1])
    copy_skill("media-ai-image", dest)
    copy_skill("media-ai-image", dest / "other")
    for target in (dest, dest / "other"):
        skill_is_current(target, "media-ai-image")
    packaged = str(skill_root("media-ai-image"))
    assert [r for r in reads if r == packaged] == [packaged], "packaged tree re-read per destination"
