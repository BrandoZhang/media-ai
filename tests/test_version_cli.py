"""``version show`` / ``version check``, and the settings behind them.

The group exists because `--version` prints prose and an agent parsing prose eventually
parses it wrong. So the questions worth asking are: does it report the *same* numbers
the code holds, is the offline/online split real, and can a machine that is quietly not
checking be asked why.

One rule underpins the last of those. Environment beats config beats default is an
ordinary precedence chain, and this project deleted one of those for credentials —
because "where did this key come from?" needed reasoning to answer. The difference here
is that the answer is *printed*: `settings_from` is what keeps it answerable by looking.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

import media_ai
from media_ai.cli import version as version_mod
from media_ai.cli._install import Install, detect
from media_ai.core import update

FEED = {
    "schema": 1,
    "latest": {"version": "9.9.9", "url": "https://example.invalid/v9.9.9", "prerelease": False},
    "notices": [],
    "retired_bindings": [],
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "config.toml"))
    for var in ("CI", "MEDIA_UPDATE_CHECK", "MEDIA_UPDATE_FEED"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def run(*argv, expect=0, capsys) -> dict:
    old, sys.argv = sys.argv, ["media-ai version", *argv]
    try:
        code = version_mod.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code == expect, f"{argv} -> {code}: {out}"
    return json.loads(out.strip().splitlines()[-1])


def cache(tmp_path, feed: dict) -> None:
    (tmp_path / "update-cache.json").write_text(
        json.dumps({"checked_at": time.time(), "feed": feed}), encoding="utf-8"
    )


def published(tmp_path, monkeypatch, feed: dict) -> None:
    path = tmp_path / "feed.json"
    path.write_text(json.dumps(feed), encoding="utf-8")
    monkeypatch.setenv("MEDIA_UPDATE_FEED", path.as_uri())


# ---------------------------------------------------------------------- show


def test_show_reports_the_version_the_package_holds(capsys):
    assert run("show", capsys=capsys)["version"] == media_ai.__version__


def test_show_reports_every_schema_this_build_reads(capsys):
    """Four numbers, and they are four because they move independently.

    Pinned to the constants rather than to literals: this output is what a bug report
    quotes, so a hand-maintained copy that drifts would make it worse than useless.
    """
    from media_ai.core.config import SCHEMA as CONFIG
    from media_ai.core.result import SCHEMA_VERSION as RESULT
    from media_ai.credentials.stores import SCHEMA as CREDENTIALS

    assert run("show", capsys=capsys)["schemas"] == {
        "result": RESULT, "config": CONFIG, "credentials": CREDENTIALS, "feed": update.FEED_SCHEMA,
    }


def test_show_never_touches_the_network(monkeypatch, capsys):
    """It reports what this build *is*; nothing about that is remote."""
    monkeypatch.setenv("MEDIA_UPDATE_FEED", "https://definitely.invalid/feed.json")
    assert run("show", capsys=capsys)["ok"] is True


# --------------------------------------------------------------------- check


def test_check_finds_a_newer_release_and_says_how_to_get_it(tmp_path, capsys):
    cache(tmp_path, FEED)
    result = run("check", "--offline", capsys=capsys)
    assert result["update_available"] is True
    assert result["latest"] == "9.9.9"
    assert result["upgrade_command"]


def test_being_out_of_date_is_not_a_failed_command(tmp_path, capsys):
    """Exit codes are failure *categories*. "There is a newer release" is a finding.

    `doctor` settled this already: a diagnosis that ran is not a CLI failure however
    grim its findings, and the field is what a script branches on.
    """
    cache(tmp_path, FEED)
    assert run("check", "--offline", expect=0, capsys=capsys)["update_available"] is True


def test_up_to_date_says_so_without_an_upgrade_command(tmp_path, capsys):
    cache(tmp_path, {**FEED, "latest": {"version": media_ai.__version__}})
    result = run("check", "--offline", capsys=capsys)
    assert result["update_available"] is False
    assert "upgrade_command" not in result


def test_not_knowing_is_a_different_answer_from_being_current(capsys):
    """A machine behind a proxy that has never fetched must not read as "up to date".

    `source` is what tells them apart; `latest: null` alone would be ambiguous with a
    feed that simply had nothing to say.
    """
    result = run("check", "--offline", capsys=capsys)
    assert result["source"] == "none"
    assert result["latest"] is None
    assert result["update_available"] is False


def test_offline_reads_the_cache_and_online_fetches(tmp_path, monkeypatch, capsys):
    cache(tmp_path, {**FEED, "latest": {"version": "0.0.1"}})
    published(tmp_path, monkeypatch, FEED)

    stale = run("check", "--offline", capsys=capsys)
    assert stale["source"] == "cache" and stale["latest"] == "0.0.1"

    fresh = run("check", capsys=capsys)
    assert fresh["source"] == "network" and fresh["latest"] == "9.9.9"


def test_an_explicit_check_ignores_the_freshness_window(tmp_path, monkeypatch, capsys):
    """Asking is asking. A TTL exists to keep *unsolicited* checks rare."""
    cache(tmp_path, {**FEED, "latest": {"version": "0.0.1"}})
    published(tmp_path, monkeypatch, FEED)
    assert run("check", capsys=capsys)["latest"] == "9.9.9"


def test_the_feeds_notices_do_not_collide_with_the_envelope(tmp_path, capsys):
    """`notices` belongs to every command's envelope, which would overwrite this key.

    Two different things wear the word: `notices[]` is about *this installation*, and
    these are the feed's announcements for this version. Naming both `notices` would
    have silently dropped one — `_with_notices` rebuilds the payload.
    """
    notice = {"id": "n1", "severity": "info", "title": "t", "body": "b"}
    cache(tmp_path, {**FEED, "notices": [notice]})
    result = run("check", "--offline", capsys=capsys)
    assert result["feed_notices"] == [notice]


# ------------------------------------------------------------------ settings


def test_checking_can_be_turned_off_in_the_config(tmp_path, capsys):
    (tmp_path / "config.toml").write_text("schema = 2\n\n[update]\ncheck = false\n", encoding="utf-8")
    assert update.should_check(media_ai.__version__) is False
    result = run("check", "--offline", capsys=capsys)
    assert result["settings"]["check"] is False
    assert result["settings_from"]["check"] == "config"


def test_the_environment_wins_and_says_that_it_did(tmp_path, monkeypatch, capsys):
    (tmp_path / "config.toml").write_text("schema = 2\n\n[update]\ncheck = false\n", encoding="utf-8")
    monkeypatch.setenv("MEDIA_UPDATE_CHECK", "1")
    result = run("check", "--offline", capsys=capsys)
    assert result["settings"]["check"] is True
    assert result["settings_from"]["check"] == "env"


def test_an_internal_mirror_can_be_configured_once(tmp_path, capsys):
    """The reason `feed` is a config key and not only a variable: an internal build
    points every install at its own copy at setup, rather than at every shell."""
    (tmp_path / "config.toml").write_text(
        'schema = 2\n\n[update]\nfeed = "https://mirror.internal/feed.json"\n', encoding="utf-8"
    )
    result = run("check", "--offline", capsys=capsys)
    assert result["settings"]["feed"] == "https://mirror.internal/feed.json"
    assert result["settings_from"]["feed"] == "config"


def test_a_default_config_writes_no_update_table(tmp_path):
    """A table of defaults in every config file invites editing settings nobody chose."""
    from media_ai.core.config import Config, render_config

    assert "[update]" not in render_config(Config())


def test_a_configured_setting_survives_a_rewrite(tmp_path, monkeypatch):
    from media_ai.core.config import load_config, save_config

    path = tmp_path / "config.toml"
    path.write_text("schema = 2\n\n[update]\ncheck = false\n", encoding="utf-8")
    save_config(load_config(path))
    assert load_config(path).update.check is False


@pytest.mark.parametrize("body", ['check = "no"', "check = 1", 'feed = ""', "feed = 3"])
def test_a_malformed_update_table_is_refused_by_name(tmp_path, body):
    """`check = "no"` is the sharp one: a non-empty string is truthy, so an unchecked
    read would turn an attempt to *disable* checking into leaving it on."""
    from media_ai.core.config import load_config
    from media_ai.core.errors import MediaError

    path = tmp_path / "config.toml"
    path.write_text(f"schema = 2\n\n[update]\n{body}\n", encoding="utf-8")
    with pytest.raises(MediaError, match=r"\[update\]"):
        load_config(path)


def test_a_broken_config_does_not_break_the_update_check(tmp_path):
    """`init` is the command people run *because* their config is broken.

    A malformed file surfacing first as a failure of the update check would bury the
    real message under an unrelated one. Every other reader still refuses loudly —
    the test above proves that.
    """
    (tmp_path / "config.toml").write_text("this is not toml at all {{", encoding="utf-8")
    assert update.settings().check is True
    assert update.should_check(media_ai.__version__) is True


# ------------------------------------------------------------------- install


def test_detection_answers_from_a_closed_set():
    assert detect().method in {"uv-tool", "pip", "editable", "unknown"}


@pytest.mark.parametrize(
    "method,expected",
    [("uv-tool", "uv tool install --force"), ("pip", "pip install --upgrade"), ("editable", "git pull")],
)
def test_each_install_gets_the_command_that_works_for_it(method, expected):
    assert expected in Install(method, "/x").upgrade_command("acme/tool", "1.2.3")


def test_an_undetectable_install_offers_no_command_rather_than_a_wrong_one():
    """This project documents hints as usually runnable and agents run them verbatim,
    so a command that does not work is worse than no command."""
    assert Install("unknown", "/x").upgrade_command("acme/tool", "1.2.3") is None


def test_removal_and_upgrade_disagree_about_an_editable_install():
    """Only looks like the same question. `pip uninstall` on an editable install drops
    the link and leaves the work tree; `pip install --upgrade` would install a release
    on top of the code somebody is editing."""
    editable = Install("editable", "/x")
    assert "pip uninstall" in editable.remove_command()
    assert "git pull" in editable.upgrade_command("acme/tool")


# ---------------------------------------------------------------- dispatching


@pytest.mark.parametrize("argv", [["version"], ["--version"], ["-V"]])
def test_a_bare_version_query_still_answers_a_human(argv, capsys, monkeypatch):
    """Prose and exit 0, the same exemption `--help` gets. Adding the group must not
    change what somebody typing the obvious thing has always got."""
    from media_ai.__main__ import main

    monkeypatch.setattr(sys, "argv", ["media-ai", *argv])
    assert main() == 0
    assert capsys.readouterr().out.strip() == f"media-ai {media_ai.__version__}"


def test_the_subcommands_reach_the_group(capsys, monkeypatch):
    from media_ai.__main__ import main

    monkeypatch.setattr(sys, "argv", ["media-ai", "version", "show"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out.strip())["op"] == "show"
