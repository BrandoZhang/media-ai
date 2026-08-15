"""Reading the release feed.

The client half. Its job is bounded in an unusual direction: almost every test here is
about something it must *not* do — not block a generation, not raise, not believe a
document it cannot safely read, not let a remote file change anything but what is
displayed.

The last one is the important one. A feed is fetched over the network from a host this
project does not control at the moment of the call, so the ceiling on a compromised or
mistaken feed has to be *denial of service*: it can withhold, never redirect. There is a
regression test that says so directly, because that ceiling is a property of the schema
and schemas grow.
"""

from __future__ import annotations

import json
import time

import pytest

import media_ai
from media_ai.core import update

FEED = {
    "schema": 1,
    "latest": {"version": "9.9.9", "url": "https://example.invalid/v9.9.9", "prerelease": False},
    "notices": [],
    "retired_bindings": [],
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A config directory this test owns, and no ambient CI variable."""
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "config.toml"))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MEDIA_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("MEDIA_UPDATE_FEED", raising=False)
    return tmp_path


def published(tmp_path, monkeypatch, feed: dict | str) -> None:
    """Serve ``feed`` from a local file, the way an internal mirror would."""
    path = tmp_path / "feed.json"
    path.write_text(feed if isinstance(feed, str) else json.dumps(feed), encoding="utf-8")
    monkeypatch.setenv("MEDIA_UPDATE_FEED", path.as_uri())


def cache(tmp_path, feed: dict, *, age_seconds: float = 0.0) -> None:
    (tmp_path / "update-cache.json").write_text(
        json.dumps({"checked_at": time.time() - age_seconds, "feed": feed}), encoding="utf-8"
    )


# ------------------------------------------------------------- what it will not do


def test_a_feed_can_only_ever_withhold(tmp_path, monkeypatch):
    """The ceiling on a compromised feed is denial of service, never redirection.

    Nothing it carries names an endpoint, a credential or a command; the reader exposes
    no way to get at a field that is not in the schema. This is asserted directly
    because it is a property of the schema, and schemas grow — a `base_url` added here
    one day would hand a remote document the ability to point every generation
    somewhere else, which is a different product.
    """
    hostile = {
        **FEED,
        "base_url": "https://attacker.invalid",
        "command": "rm -rf /",
        "credential": "env://STOLEN",
        "bindings": {"evil/model": {"base_url": "https://attacker.invalid"}},
    }
    published(tmp_path, monkeypatch, hostile)
    feed = update.refresh("0.6.0")

    # The extra keys survive parsing — unknown fields are ignored, not rejected — but
    # nothing reads them, and everything the reader exposes is a version or prose.
    assert update.latest_version(feed) == "9.9.9"
    assert update.notices_for(feed, "0.6.0") == []
    assert {"latest_version", "notices_for", "is_newer", "cached", "refresh"} <= set(update.__all__)
    assert not any(name in update.__all__ for name in ("base_url", "command", "credential", "bindings"))


def test_a_feed_from_a_newer_schema_is_ignored_whole(tmp_path, monkeypatch):
    """Understanding half a document whose meaning has moved is worse than ignoring it.

    The reader is the part that cannot be upgraded — it is already installed — so it
    has no way to be told which half is still safe.
    """
    published(tmp_path, monkeypatch, {**FEED, "schema": update.FEED_SCHEMA + 1})
    assert update.refresh("0.6.0") is None
    assert update.cached() is None


def test_unknown_fields_are_ignored_rather_than_rejected(tmp_path, monkeypatch):
    """The other direction of the same rule: the shape only ever grows."""
    published(tmp_path, monkeypatch, {**FEED, "something_added_later": {"deeply": ["nested"]}})
    assert update.latest_version(update.refresh("0.6.0")) == "9.9.9"


@pytest.mark.parametrize("body", ['{"schema": "1"}', '{"schema": true}', "[]", "null", "not json at all", ""])
def test_a_malformed_feed_is_silence(tmp_path, monkeypatch, body):
    published(tmp_path, monkeypatch, body)
    assert update.refresh("0.6.0") is None


def test_an_unreachable_feed_is_silence_and_writes_nothing(tmp_path, monkeypatch):
    """A CDN having a bad day must not fail an install, a generation or a diagnosis."""
    monkeypatch.setenv("MEDIA_UPDATE_FEED", (tmp_path / "does-not-exist.json").as_uri())
    assert update.refresh("0.6.0") is None
    assert not update.cache_path().exists()


def test_a_feed_larger_than_the_ceiling_is_ignored(tmp_path, monkeypatch):
    """A wrong URL should not stream something enormous into memory.

    Read as `limit + 1` so a body exactly at the limit and one over it stay
    distinguishable — truncating instead would hand malformed JSON to the parser and
    be indistinguishable from a malformed feed.
    """
    published(tmp_path, monkeypatch, {**FEED, "padding": "x" * (update._MAX_BYTES + 1)})
    assert update.refresh("0.6.0") is None


# ------------------------------------------------------------------ when it asks


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_the_env_switch_turns_checking_off(monkeypatch, value):
    monkeypatch.setenv("MEDIA_UPDATE_CHECK", value)
    assert update.should_check("0.6.0") is False


def test_ci_does_not_make_unsolicited_requests(monkeypatch):
    monkeypatch.setenv("CI", "1")
    assert update.should_check("0.6.0") is False


def test_no_tty_does_not_turn_checking_off(monkeypatch):
    """Deliberately unlike `_nobody_is_watching`, which this is not reusing.

    That one asks whether a human will answer a prompt. An agent harness with no
    terminal still wants to be told its CLI is out of date — the agent is the party
    that can act on it.
    """
    monkeypatch.setenv("MEDIA_NO_TTY", "1")
    assert update.should_check("0.6.0") is True


@pytest.mark.parametrize("version", ["0.6.0+g9b933f1", "", "dev", "0.6", "v0.6.0"])
def test_a_build_that_is_not_a_release_never_checks(version):
    """An editable checkout has no meaningful "newer", and telling somebody working on
    the tool to go and install it is noise."""
    assert update.should_check(version) is False


def test_a_fresh_cache_is_not_refetched(tmp_path, monkeypatch):
    cache(tmp_path, FEED, age_seconds=60)
    monkeypatch.setenv("MEDIA_UPDATE_FEED", (tmp_path / "does-not-exist.json").as_uri())
    assert update.refresh("0.6.0") is None  # would have failed loudly if it had fetched
    assert update.latest_version(update.cached()) == "9.9.9"


def test_a_stale_cache_is_refetched(tmp_path, monkeypatch):
    cache(tmp_path, {**FEED, "latest": {"version": "0.0.1"}}, age_seconds=99 * 3600)
    published(tmp_path, monkeypatch, FEED)
    assert update.latest_version(update.refresh("0.6.0")) == "9.9.9"


def test_force_ignores_the_ttl(tmp_path, monkeypatch):
    cache(tmp_path, {**FEED, "latest": {"version": "0.0.1"}}, age_seconds=1)
    published(tmp_path, monkeypatch, FEED)
    assert update.latest_version(update.refresh("0.6.0", force=True)) == "9.9.9"


# ---------------------------------------------------------------- reading it back


def test_the_cache_is_the_only_thing_the_hot_path_touches(tmp_path, monkeypatch):
    """`cached` cannot block: point the feed at a URL that would fail and read anyway."""
    cache(tmp_path, FEED)
    monkeypatch.setenv("MEDIA_UPDATE_FEED", "https://definitely.invalid/feed.json")
    assert update.latest_version(update.cached()) == "9.9.9"


def test_no_cache_at_all_is_not_an_error():
    assert update.cached() is None
    assert update.cached_at() is None


def test_a_corrupt_cache_is_not_an_error(tmp_path):
    update.cache_path().parent.mkdir(parents=True, exist_ok=True)
    update.cache_path().write_text("{ this is not json", encoding="utf-8")
    assert update.cached() is None


@pytest.mark.parametrize(
    "latest,current,expected",
    [("9.9.9", "0.6.0", True), ("0.6.0", "0.6.0", False), ("0.5.0", "0.6.0", False),
     ("0.10.0", "0.9.0", True), (None, "0.6.0", False), ("not-a-version", "0.6.0", False)],
)
def test_is_newer_is_tolerant_of_what_comes_off_the_network(latest, current, expected):
    """A malformed remote value means "say nothing", never an exception mid-command."""
    assert update.is_newer(latest, current) is expected


# ------------------------------------------------------------------- the notices


NOTICE = {"id": "n1", "severity": "info", "title": "Heads up", "body": "something"}


@pytest.mark.parametrize(
    "bounds,version,shown",
    [
        ({}, "0.6.0", True),
        ({"max_version": "0.5.0"}, "0.6.0", False),
        ({"max_version": "0.9.0"}, "0.6.0", True),
        ({"min_version": "0.7.0"}, "0.6.0", False),
        ({"min_version": "0.5.0", "max_version": "0.7.0"}, "0.6.0", True),
        ({"min_version": "nonsense"}, "0.6.0", False),
    ],
)
def test_a_notice_applies_by_two_plain_comparisons(bounds, version, shown):
    """Not an expression language — that would be parsed by the *old* clients, and one
    that misparses shows the wrong people a notice rather than failing.

    An unreadable bound drops the notice: it is not the same as "no bound".
    """
    feed = {**FEED, "notices": [{**NOTICE, **bounds}]}
    assert bool(update.notices_for(feed, version)) is shown


def test_a_notice_with_nothing_to_show_is_dropped():
    feed = {**FEED, "notices": [{"id": "x", "severity": "info"}, "not even a table", NOTICE]}
    assert update.notices_for(feed, "0.6.0") == [NOTICE]


@pytest.mark.parametrize("bound", ["", 1, [], {"version": "0.7.0"}, True])
def test_a_notice_with_a_non_string_bound_is_dropped(bound):
    """Remote notices are display-only, so malformed bounds cannot break a command."""
    feed = {**FEED, "notices": [{**NOTICE, "min_version": bound}]}
    assert update.notices_for(feed, "0.6.0") == []


def test_setup_shows_what_the_feed_has_to_say(tmp_path, monkeypatch):
    """`_announce` sketched this interface before there was anything behind it."""
    from media_ai.cli._announce import announcements

    cache(tmp_path, {**FEED, "notices": [NOTICE]})
    shown = announcements()
    assert shown[0][0] == "Heads up" or len(shown) > 1  # the compiled-in warning stays first
    assert ("Heads up", "something") in shown


def test_setup_shows_only_the_compiled_in_warning_with_no_cache():
    from media_ai.cli._announce import announcements

    assert len(announcements()) == 1


# --------------------------------------------------------------------- doctor


def test_doctor_reports_from_the_cache_and_never_the_network(tmp_path, monkeypatch):
    """`doctor` is defined as strictly offline, and this does not bend that."""
    from media_ai.cli import doctor

    cache(tmp_path, FEED)
    monkeypatch.setenv("MEDIA_UPDATE_FEED", "https://definitely.invalid/feed.json")
    (found,) = doctor._check_update()
    assert found["status"] == "warn"
    assert "9.9.9" in found["detail"]


def test_doctor_says_nothing_is_wrong_when_up_to_date(tmp_path):
    from media_ai.cli import doctor

    cache(tmp_path, {**FEED, "latest": {"version": media_ai.__version__}})
    (found,) = doctor._check_update()
    assert found["status"] == "ok"


def test_a_machine_that_has_never_checked_is_not_a_broken_machine():
    """An absent cache is `ok`: the fix for it is running setup, which a diagnosis
    should not nag about."""
    from media_ai.cli import doctor

    (found,) = doctor._check_update()
    assert found["status"] == "ok"


# ------------------------------------------------------------------ uninstall


def test_uninstall_takes_the_cache_with_it(tmp_path, monkeypatch):
    """Derived state, never asked about and never kept — "leaves nothing behind"."""
    import sys

    from media_ai.cli import uninstall

    cache(tmp_path, FEED)
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
    argv = ["media-ai uninstall", "--yes", "--skills-dest", str(tmp_path / "none")]
    old, sys.argv = sys.argv, argv
    try:
        uninstall.main()
    finally:
        sys.argv = old
    assert not update.cache_path().exists()
