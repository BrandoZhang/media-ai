""""There is a newer release" as a notice, on whatever command was already running.

`version check` answers this on request. The point of the notice is the caller who
never thinks to ask — an agent runs `image generate`, and the fact arrives in the object
it is already parsing. That is the whole reason `notices[]` exists.

Two properties carry the design, and both are about restraint. It reads the **cache**,
so a generation still touches nothing but a file; and it is **stateless**, so it stops
when the condition does rather than when somebody decides it has been seen enough.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

import media_ai
from media_ai.cli import common
from media_ai.core import notices, update

FEED = {"schema": 1, "latest": {"version": "9.9.9"}, "notices": [], "retired_bindings": []}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_AI_CONFIG_FILE", str(tmp_path / "config.toml"))
    for var in ("CI", "MEDIA_AI_UPDATE_CHECK", "MEDIA_AI_UPDATE_FEED"):
        monkeypatch.delenv(var, raising=False)
    notices.reset()
    yield
    notices.reset()


def cache(tmp_path, feed: dict) -> None:
    (tmp_path / "update-cache.json").write_text(
        json.dumps({"checked_at": time.time(), "feed": feed}), encoding="utf-8"
    )


def found() -> list:
    return list(common._a_newer_release_is_published())


# ------------------------------------------------------------- when it speaks


def test_a_newer_release_becomes_a_notice(tmp_path):
    cache(tmp_path, FEED)
    (notice,) = found()
    assert notice.kind == "update_available"
    assert "9.9.9" in notice.message and media_ai.__version__ in notice.message


def test_it_is_information_not_a_warning(tmp_path):
    """Being a release behind is the ordinary state of a working installation.

    Stale skills warn because the instructions being followed describe a different
    build. This is not that, and flattening the two would make the severity useless.
    """
    cache(tmp_path, FEED)
    assert found()[0].severity == "info"


def test_it_carries_the_command_for_this_installation(tmp_path, monkeypatch):
    """Which command that is depends on how this build was installed — an editable
    checkout is told to pull, and correctly names no version at all."""
    from media_ai.cli import _install

    cache(tmp_path, FEED)
    monkeypatch.setattr(_install, "detect", lambda: _install.Install("uv-tool", "/x"))
    assert "9.9.9" in found()[0].action

    monkeypatch.setattr(_install, "detect", lambda: _install.Install("editable", "/x"))
    assert found()[0].action == "git pull && uv sync"


def test_an_unidentifiable_install_still_gets_the_notice(tmp_path, monkeypatch):
    """Without an action. Knowing there is a newer release is useful on its own, and a
    command that does not work is worse than no command — an agent runs it verbatim."""
    from media_ai.cli import _install

    cache(tmp_path, FEED)
    monkeypatch.setattr(_install, "detect", lambda: _install.Install("unknown", "/x"))
    (notice,) = found()
    assert notice.action is None
    assert "9.9.9" in notice.message


def test_it_reaches_the_envelope_of_an_ordinary_command(tmp_path, capsys):
    """The point of the whole thing: an agent that never asks still finds out."""
    from media_ai.cli import bindings as bindings_mod

    cache(tmp_path, FEED)
    old, sys.argv = sys.argv, ["media-ai bindings", "list"]
    try:
        bindings_mod.main()
    finally:
        sys.argv = old
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert [n["kind"] for n in payload["notices"]] == ["update_available"]


# ------------------------------------------------------------ when it does not


def test_nothing_cached_says_nothing(tmp_path):
    """A machine that has never fetched is not a machine that is out of date."""
    assert found() == []


def test_being_current_says_nothing(tmp_path):
    cache(tmp_path, {**FEED, "latest": {"version": media_ai.__version__}})
    assert found() == []


def test_an_older_published_version_says_nothing(tmp_path):
    """A cache from before a downgrade, or a feed that went backwards."""
    cache(tmp_path, {**FEED, "latest": {"version": "0.0.1"}})
    assert found() == []


def test_turning_the_check_off_turns_the_notice_off(tmp_path):
    """The setting means "do not tell me about updates", not "do not fetch"."""
    (tmp_path / "config.toml").write_text("schema = 2\n\n[update]\ncheck = false\n", encoding="utf-8")
    cache(tmp_path, FEED)
    assert found() == []


def test_ci_does_not_turn_the_notice_off(tmp_path, monkeypatch):
    """`CI` gates *unsolicited network*. Reporting something already on disk costs
    nothing, and an agent in CI can act on it as well as anywhere else."""
    monkeypatch.setenv("CI", "1")
    cache(tmp_path, FEED)
    assert len(found()) == 1


def test_it_never_reaches_for_the_network(tmp_path, monkeypatch):
    """A generation command must not wait on a feed. Point it somewhere that would
    fail loudly and read anyway."""
    monkeypatch.setenv("MEDIA_AI_UPDATE_FEED", "https://definitely.invalid/feed.json")
    cache(tmp_path, FEED)
    assert len(found()) == 1


def test_a_broken_cache_is_not_a_broken_command(tmp_path):
    update.cache_path().parent.mkdir(parents=True, exist_ok=True)
    update.cache_path().write_text("{ not json", encoding="utf-8")
    assert found() == []


# ---------------------------------------------------------------- statelessly


def test_it_repeats_rather_than_remembering(tmp_path):
    """A "shown once" flag would suppress it for every later session — and the agent in
    *that* session is the one who would have acted.

    The notice describes a condition that is still true, not an event that happened. It
    stops when the condition stops, which is the rule `skills_stale` already follows.
    """
    cache(tmp_path, FEED)
    for _ in range(3):
        notices.reset()  # a fresh process each time
        assert len(found()) == 1


def test_nothing_is_written_by_reading_it(tmp_path):
    """A notice that touched the cache would make every command a writer."""
    cache(tmp_path, FEED)
    before = update.cache_path().read_bytes(), update.cache_path().stat().st_mtime_ns
    found()
    assert (update.cache_path().read_bytes(), update.cache_path().stat().st_mtime_ns) == before


# ------------------------------------------------------- alongside the other one


def test_both_notices_can_be_pending_at_once(tmp_path):
    """They are independent conditions; one must not hide the other."""
    cache(tmp_path, FEED)
    (tmp_path / "installed-skills.toml").write_text(
        f'[dests."{tmp_path}/skills"]\nskills = ["x"]\nversion = "0.0.1"\n', encoding="utf-8"
    )
    kinds = {n["kind"] for n in notices.pending()}
    assert kinds == {"update_available", "skills_stale"}


def test_every_kind_the_sources_emit_is_declared(tmp_path):
    """`kind` is the closed set a consumer branches on, so nothing may invent one."""
    cache(tmp_path, FEED)
    assert {n["kind"] for n in notices.pending()} <= notices.KINDS
