"""``upgrade`` — the one command that replaces the thing running it.

Almost everything worth asserting is a refusal. It substitutes nothing (the steps come
from install detection and run as argv, never through a shell), it will not touch a
source checkout, it will not act unattended without being told to, and it will not
install *something* when it cannot name what.

The child process gets the same treatment every spawn in this repo gets — stdin closed,
output captured — for the two reasons `media/ffmpeg.py` documents: a child otherwise
eats the caller's stdin, and stdout here belongs to the one JSON object every command
prints.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

import media_ai
from media_ai.cli import _install
from media_ai.cli import upgrade as upgrade_mod

FEED = {"schema": 1, "latest": {"version": "9.9.9"}, "notices": [], "retired_bindings": []}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "config.toml"))
    for var in ("CI", "MEDIA_UPDATE_CHECK"):
        monkeypatch.delenv(var, raising=False)
    # Unattended by default: the interactive path is exercised explicitly below, and a
    # test that accidentally reached a real prompt would hang the suite.
    monkeypatch.setenv("MEDIA_NO_TTY", "1")
    # Patched on the command module: `upgrade` imports the name at module level, so
    # patching `_install.detect` would rebind something nothing looks at again.
    monkeypatch.setattr(upgrade_mod, "detect", lambda: _install.Install("uv-tool", "/x"))
    return tmp_path


@pytest.fixture
def spawns(monkeypatch):
    """Record every child that would be started, and answer success."""
    calls = []

    class Done:
        returncode = 0
        stdout = "installed"
        stderr = ""

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return Done()

    monkeypatch.setattr(upgrade_mod.subprocess, "run", fake_run)
    return calls


def cache(tmp_path, monkeypatch, feed: dict = FEED) -> None:
    """Publish ``feed`` where this test's CLI will read it.

    Both halves, because `upgrade` learns its target by refreshing (it is an explicit
    request, so waiting is the deal) and falls back to the cache when that fails. The
    served copy is a local file: `conftest` points the feed at a path that does not
    exist precisely so a test cannot reach the real one by omission.
    """
    served = tmp_path / "feed.json"
    served.write_text(json.dumps(feed), encoding="utf-8")
    monkeypatch.setenv("MEDIA_UPDATE_FEED", served.as_uri())
    (tmp_path / "update-cache.json").write_text(
        json.dumps({"checked_at": time.time(), "feed": feed}), encoding="utf-8"
    )


def run(*argv, expect=0, capsys) -> dict:
    old, sys.argv = sys.argv, ["media-ai upgrade", *argv]
    try:
        code = upgrade_mod.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code == expect, f"{argv} -> {code}: {out}"
    return json.loads(out.strip().splitlines()[-1])


# ------------------------------------------------------------------ it upgrades


def test_it_runs_the_step_for_this_installation(tmp_path, spawns, monkeypatch, capsys):
    cache(tmp_path, monkeypatch)
    result = run("--yes", capsys=capsys)
    assert result["upgraded"] is True and result["to"] == "9.9.9"
    (argv, _kw), = spawns
    assert argv[:4] == ["uv", "tool", "install", "--force"]
    assert argv[4].endswith("@v9.9.9")


def test_the_child_cannot_eat_the_callers_stdin(tmp_path, spawns, monkeypatch, capsys):
    """The invariant `media/ffmpeg.py` spells out, applied to the other spawn site."""
    cache(tmp_path, monkeypatch)
    run("--yes", capsys=capsys)
    (_argv, kw), = spawns
    assert kw["stdin"] is subprocess.DEVNULL


def test_the_childs_output_never_reaches_stdout(tmp_path, spawns, monkeypatch, capsys):
    """stdout is one JSON object. A package manager's progress bars are not part of it."""
    cache(tmp_path, monkeypatch)
    run("--yes", capsys=capsys)
    (_argv, kw), = spawns
    assert kw["capture_output"] is True


def test_no_shell_is_involved(tmp_path, spawns, monkeypatch, capsys):
    """argv, not a string: hand-written quoting only fails on the machine with a space
    in its path, which is the machine you do not have."""
    cache(tmp_path, monkeypatch)
    run("--yes", capsys=capsys)
    (argv, kw), = spawns
    assert isinstance(argv, list) and not kw.get("shell")


def test_an_explicit_version_wins_over_the_feed(tmp_path, spawns, monkeypatch, capsys):
    cache(tmp_path, monkeypatch)
    assert run("--yes", "--version", "1.2.3", capsys=capsys)["to"] == "1.2.3"
    assert spawns[0][0][4].endswith("@v1.2.3")


def test_an_explicit_version_may_go_backwards(tmp_path, spawns, monkeypatch, capsys):
    """Pinning to an older release is a legitimate thing to ask for; only the automatic
    path insists on moving forwards."""
    cache(tmp_path, monkeypatch)
    assert run("--yes", "--version", "0.0.1", capsys=capsys)["upgraded"] is True


# ------------------------------------------------------------------ it refuses


def test_already_current_is_not_a_failure(tmp_path, spawns, monkeypatch, capsys):
    """A scheduled `upgrade` on a fleet that is up to date must be quiet, not noisy."""
    cache(tmp_path, monkeypatch, {**FEED, "latest": {"version": media_ai.__version__}})
    result = run(capsys=capsys)
    assert result["upgraded"] is False and result["reason"] == "already_current"
    assert spawns == []


def test_a_source_checkout_is_left_alone(tmp_path, spawns, monkeypatch, capsys):
    """Somebody's work tree and git state are theirs. A stash conflict caused by a CLI
    helpfully pulling is a bad afternoon."""
    cache(tmp_path, monkeypatch)
    monkeypatch.setattr(upgrade_mod, "detect", lambda: _install.Install("editable", "/x"))
    result = run("--yes", expect=2, capsys=capsys)
    assert result["error"]["code"] == "upgrade_not_supported"
    assert "git pull" in result["error"]["hint"]
    assert spawns == []


def test_an_unidentifiable_install_gets_no_invented_command(tmp_path, spawns, monkeypatch, capsys):
    cache(tmp_path, monkeypatch)
    monkeypatch.setattr(upgrade_mod, "detect", lambda: _install.Install("unknown", "/x"))
    result = run("--yes", expect=2, capsys=capsys)
    assert result["error"]["code"] == "upgrade_not_supported"
    assert "however you installed it" in result["error"]["hint"]
    assert spawns == []


def test_it_will_not_install_something_it_cannot_name(tmp_path, spawns, monkeypatch, capsys):
    """No feed, no `--version`. Installing the default branch instead would move
    somebody from a release onto whatever main happens to be, and call it an upgrade."""
    # conftest already points the feed at a file that is not there, and nothing has
    # cached an answer, so there is genuinely nothing to name.
    result = run("--yes", expect=2, capsys=capsys)
    assert result["error"]["code"] == "no_upgrade_target"
    assert spawns == []


def test_it_will_not_replace_the_cli_unattended(tmp_path, spawns, monkeypatch, capsys):
    """`--yes` is the whole difference. Refusing is recoverable with one flag; an
    unattended upgrade nobody asked for is not."""
    cache(tmp_path, monkeypatch)
    result = run(expect=2, capsys=capsys)
    assert result["error"]["code"] == "confirmation_required"
    assert spawns == []


def test_declining_the_prompt_changes_nothing(tmp_path, spawns, monkeypatch, capsys):
    class No:
        def confirm(self, *_a, **_k):
            return False

    cache(tmp_path, monkeypatch)
    monkeypatch.setattr(upgrade_mod, "_nobody_is_watching", lambda: False)
    monkeypatch.setattr(upgrade_mod, "get_prompter", lambda: No())
    result = run(capsys=capsys)
    assert result["upgraded"] is False and result["reason"] == "declined"
    assert spawns == []


def test_a_dry_run_reports_the_command_and_runs_nothing(tmp_path, spawns, monkeypatch, capsys):
    cache(tmp_path, monkeypatch)
    result = run("--dry-run", capsys=capsys)
    assert result["reason"] == "dry_run"
    assert result["would_run"] and "uv tool install" in result["would_run"][0]
    assert spawns == []


# -------------------------------------------------------------- when it breaks


def test_a_failed_upgrade_reports_the_tail_of_the_output(tmp_path, monkeypatch, capsys):
    """The last few lines, not the whole resolver log — the same shape `ffmpeg._run`
    settled on, for the same reason."""
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "\n".join(f"line {i}" for i in range(40))

    cache(tmp_path, monkeypatch)
    monkeypatch.setattr(upgrade_mod.subprocess, "run", lambda argv, **kw: Failed())
    result = run("--yes", expect=1, capsys=capsys)
    assert result["error"]["code"] == "upgrade_failed"
    assert "line 39" in result["error"]["message"]
    assert "line 0" not in result["error"]["message"]


def test_a_missing_package_manager_says_so(tmp_path, monkeypatch, capsys):
    cache(tmp_path, monkeypatch)
    monkeypatch.setattr(upgrade_mod.subprocess, "run", _raise(FileNotFoundError("uv")))
    result = run("--yes", expect=1, capsys=capsys)
    assert result["error"]["code"] == "upgrade_tool_missing"


def test_a_wedged_upgrade_times_out_rather_than_hanging(tmp_path, monkeypatch, capsys):
    cache(tmp_path, monkeypatch)
    monkeypatch.setattr(
        upgrade_mod.subprocess, "run", _raise(subprocess.TimeoutExpired(["uv"], 1))
    )
    result = run("--yes", expect=7, capsys=capsys)
    assert result["error"]["code"] == "upgrade_timed_out"


# ------------------------------------------------------------------- the help


def test_the_help_tells_an_agent_not_to_run_this_by_itself():
    """The same guard `lark-cli` puts on its security-policy command: the text an agent
    reads is the only place this instruction can live."""
    text = upgrade_mod._build_parser().format_help()
    assert "Do NOT run it on" in text


def _raise(exc):
    def boom(*_a, **_k):
        raise exc

    return boom
