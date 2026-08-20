"""The check that happens without being asked for.

`test_update.py` covers reading a feed. This file covers the part that makes a machine
*have* one: every command starts a detached refresh on its way out, so the two things
the feed is allowed to stop are read from a cache somebody keeps topping up rather than
from whatever `init` fetched once, months ago.

Every test here is about a cost. The feature is defensible only while all four of these
hold, and each of them is one line of code away from not holding:

- **it never delays a command** — the request happens in another process, after the
  result has been printed, and nothing waits for it;
- **it never multiplies** — twenty commands in a script make at most one request, and
  a machine that cannot reach the feed at all still makes at most one attempt per
  interval;
- **it can always be turned off** — a config key, an environment variable, and CI by
  default;
- **it reports nothing** — an anonymous GET of a static file, with the version in the
  User-Agent and nowhere else.
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

import media_ai
from media_ai.core import update

FEED = {
    "schema": 1,
    "latest": {"version": "9.9.9", "url": "https://example.invalid/v9.9.9"},
    "notices": [],
    "retired_bindings": [],
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "config.toml"))
    for name in ("CI", "MEDIA_UPDATE_CHECK", "MEDIA_UPDATE_FEED", "MEDIA_UPDATE_INTERVAL"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def cache(tmp_path, feed: dict | None = FEED, *, age_seconds: float = 0.0) -> None:
    payload: dict = {"checked_at": time.time() - age_seconds}
    if feed is not None:
        payload["feed"] = feed
    (tmp_path / "update-cache.json").write_text(json.dumps(payload), encoding="utf-8")


def published(tmp_path, monkeypatch, feed: dict) -> None:
    path = tmp_path / "feed.json"
    path.write_text(json.dumps(feed), encoding="utf-8")
    monkeypatch.setenv("MEDIA_UPDATE_FEED", path.as_uri())


class Spawns:
    """A stand-in for `subprocess.Popen` that records the argv and the keywords."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        return object()

    @property
    def refreshes(self) -> list[list[str]]:
        """Just the update checks. `doctor` legitimately spawns ffmpeg to probe it, and
        a test about the update check must not be an assertion about that."""
        return [argv for argv, _kw in self.calls if update.REFRESH_COMMAND in argv]


#: `conftest._no_background_refresh` stubs `_spawn` out for the whole suite, so the one
#: file that is *about* the spawn puts the real one back and intercepts one level lower,
#: at `Popen`. Nothing here reaches a real fork either — the recorder returns without
#: starting anything — but everything between `refresh_detached` and the syscall runs
#: for real, which is the part worth asserting on.
_REAL_SPAWN = update._spawn


@pytest.fixture
def spawns(monkeypatch) -> Spawns:
    import subprocess

    monkeypatch.setattr(update, "_spawn", _REAL_SPAWN)
    recorder = Spawns()
    monkeypatch.setattr(subprocess, "Popen", recorder)
    return recorder


# ------------------------------------------------------------------ when it is due


def test_a_machine_with_no_cache_is_due():
    assert update.due(media_ai.__version__) is True


def test_a_fresh_cache_is_not_due(tmp_path):
    cache(tmp_path, age_seconds=60)
    assert update.due(media_ai.__version__) is False


def test_a_cache_older_than_the_interval_is_due(tmp_path):
    cache(tmp_path, age_seconds=update.DEFAULT_INTERVAL_SECONDS + 1)
    assert update.due(media_ai.__version__) is True


def test_a_stamp_from_the_future_is_a_clock_that_moved_not_a_check_from_tomorrow(tmp_path):
    """Trusting it would leave the machine never checking again until the clock caught
    up — which, for a stamp a year out, is a year of never hearing a published floor."""
    cache(tmp_path, age_seconds=-365 * 86400)
    assert update.due(media_ai.__version__) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_the_off_switch_stops_it_being_due_at_all(tmp_path, monkeypatch, value):
    monkeypatch.setenv("MEDIA_UPDATE_CHECK", value)
    assert update.due(media_ai.__version__) is False


def test_ci_is_not_due_by_default(monkeypatch):
    """A fresh container fetches a document nobody reads, and makes noise the day the
    network wobbles."""
    monkeypatch.setenv("CI", "true")
    assert update.due(media_ai.__version__) is False


def test_a_runner_that_wants_the_check_can_say_so(monkeypatch):
    """`CI` decides only where nothing else has — the point of reading a flag in three
    states. An override that could only force one direction leaves a nightly that
    reports its own staleness with no way to ask."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("MEDIA_UPDATE_CHECK", "1")
    assert update.due(media_ai.__version__) is True


def test_a_development_build_is_never_due():
    assert update.due("0.9.0.dev1+g1234567") is False


# -------------------------------------------------------------------- the interval


def test_the_interval_defaults_to_a_day():
    assert update.interval_seconds() == 24 * 60 * 60 == update.DEFAULT_INTERVAL_SECONDS


def test_the_config_sets_it(tmp_path):
    (tmp_path / "config.toml").write_text("[update]\ninterval = 600\n", encoding="utf-8")
    assert update.interval_seconds() == 600
    cache(tmp_path, age_seconds=700)
    assert update.due(media_ai.__version__) is True


def test_a_configured_interval_is_reported_as_configured(tmp_path):
    """The failure mode of a precedence chain is not that it is wrong, it is that nobody
    can see which rung won — so `version check` prints the layer beside the value."""
    (tmp_path / "config.toml").write_text("[update]\ninterval = 600\n", encoding="utf-8")
    assert update.settings_from()["interval"] == "config"


def test_the_environment_wins_over_the_config(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text("[update]\ninterval = 600\n", encoding="utf-8")
    monkeypatch.setenv("MEDIA_UPDATE_INTERVAL", "10")
    assert update.interval_seconds() == 10
    assert update.settings_from()["interval"] == "env"


def test_zero_means_after_every_command(tmp_path, monkeypatch):
    """What a staged rollout or a demo wants. It still costs the caller nothing — the
    fetch is in another process either way."""
    monkeypatch.setenv("MEDIA_UPDATE_INTERVAL", "0")
    cache(tmp_path, age_seconds=0)
    assert update.due(media_ai.__version__) is True


@pytest.mark.parametrize("value", ["soon", "1.5", "-1", "24h", ""])
def test_an_unusable_environment_interval_is_ignored_not_refused(monkeypatch, value):
    """The opposite of how the config file is read, deliberately: this is consulted on
    the way out of *every* command, and a typo in a shell profile must not turn each of
    them into an error about a preference nobody was exercising."""
    monkeypatch.setenv("MEDIA_UPDATE_INTERVAL", value)
    assert update.interval_seconds() == update.DEFAULT_INTERVAL_SECONDS
    assert update.settings_from()["interval"] == "default"


@pytest.mark.parametrize("raw", ["-1", '"600"', "true", "1.5"])
def test_an_unusable_config_interval_is_refused(tmp_path, raw):
    """The config file is validated once, by a reader that is allowed to fail: a
    setting that was meant to be exercised and silently was not is the failure mode
    every other table here is strict about."""
    from media_ai.core.config import load_config
    from media_ai.core.errors import MediaError

    (tmp_path / "config.toml").write_text(f"[update]\ninterval = {raw}\n", encoding="utf-8")
    with pytest.raises(MediaError) as ei:
        load_config()
    assert "interval" in str(ei.value)


def test_the_interval_survives_a_round_trip(tmp_path):
    from media_ai.core.config import Config, UpdateSettings, load_config, render_config

    (tmp_path / "config.toml").write_text(
        render_config(Config(update=UpdateSettings(interval=900))), encoding="utf-8")
    assert load_config().update.interval == 900


def test_a_default_interval_is_not_written_out():
    """Same rule the rest of `[update]` follows: a table of defaults in every config
    file is noise that invites editing settings nobody chose."""
    from media_ai.core.config import Config, UpdateSettings, render_config

    assert "interval" not in render_config(Config(update=UpdateSettings(check=False)))


# ------------------------------------------------------------- the stamp comes first


def test_the_stamp_is_written_before_the_request(tmp_path, monkeypatch):
    """A stamp that only moved on success would make a machine that cannot reach the
    feed due on every single command — twenty commands, twenty processes, twenty
    timeouts. Stamping first makes a failed check cost what a successful one costs."""
    monkeypatch.setattr(update, "_fetch", lambda url: None)  # a machine that cannot reach it
    assert update.refresh_now(media_ai.__version__) is None
    assert update.checked_at() is not None, "it tried, and said so"
    assert update.cached_at() is None, "and it learned nothing, and says that too"
    assert update.due(media_ai.__version__) is False


def test_the_stamp_does_not_disturb_what_is_cached(tmp_path):
    """It records that a check happened, not what it found."""
    cache(tmp_path, FEED, age_seconds=99 * 3600)
    assert update.mark_checked() is True
    assert update.latest_version(update.cached()) == "9.9.9"
    assert update.due(media_ai.__version__) is False


def test_a_machine_that_cannot_record_a_check_does_not_spawn_one(tmp_path, monkeypatch, spawns):
    """A read-only config directory would otherwise reintroduce the storm from the
    other end: nothing to write the stamp into means every command is due."""
    monkeypatch.setattr(update, "_write_cache", lambda payload: False)
    assert update.refresh_detached(media_ai.__version__) is False
    assert spawns.calls == []


# --------------------------------------------------------------------- the spawn


def test_a_due_machine_spawns_exactly_one_refresh(tmp_path, spawns):
    assert update.refresh_detached(media_ai.__version__) is True
    assert len(spawns.calls) == 1


def test_twenty_commands_in_a_row_spawn_one_refresh(tmp_path, spawns):
    """The scenario this is all arranged around: a script that calls the CLI in a loop.
    The stamp written before the first spawn is what the other nineteen see."""
    for _ in range(20):
        update.refresh_detached(media_ai.__version__)
    assert len(spawns.calls) == 1


def test_a_machine_that_cannot_reach_the_feed_still_spawns_only_one(tmp_path, monkeypatch):
    """The offline case, end to end and for real — no fake Popen. Twenty invocations,
    an unreachable feed, one attempt."""
    attempts = []
    monkeypatch.setattr(update, "_fetch", lambda url: attempts.append(url))
    for _ in range(20):
        if update.due(media_ai.__version__):
            update.refresh_now(media_ai.__version__)
    assert len(attempts) == 1


def test_the_child_is_detached_and_speaks_to_nobody(tmp_path, spawns):
    """`start_new_session` so a Ctrl-C aimed at the foreground group does not reach it
    half way through writing the cache; every stream on /dev/null so it cannot append a
    second document to stdout, log over the command the user did start, or eat the rest
    of a `while read` loop from an inherited fd 0."""
    import subprocess

    update.refresh_detached(media_ai.__version__)
    (argv, kw) = spawns.calls[0]
    assert kw["start_new_session"] is True
    assert kw["stdin"] is subprocess.DEVNULL
    assert kw["stdout"] is subprocess.DEVNULL
    assert kw["stderr"] is subprocess.DEVNULL
    assert argv[0] == sys.executable, "the interpreter running this code, not whatever PATH resolves"
    assert argv[-1] == update.REFRESH_COMMAND


def test_a_bundle_launches_itself(tmp_path, monkeypatch, spawns):
    """The default install is a standalone bundle, and there is no interpreter in it to
    hand a `-m` to: `sys.executable` *is* the CLI. Worth its own test because the bundle
    is the shape most installs have and the one a developer never runs."""
    from media_ai.core import packaging

    monkeypatch.setattr(packaging, "is_standalone", lambda: True)
    update.refresh_detached(media_ai.__version__)
    (argv, _kw) = spawns.calls[0]
    assert argv == [sys.executable, update.REFRESH_COMMAND]


def test_a_package_install_uses_the_interpreter_already_running(tmp_path, monkeypatch, spawns):
    """Not whatever `PATH` resolves the brand name to: a virtualenv two directories away
    would refresh a cache belonging to a different install."""
    from media_ai.core import packaging

    monkeypatch.setattr(packaging, "is_standalone", lambda: False)
    update.refresh_detached(media_ai.__version__)
    (argv, _kw) = spawns.calls[0]
    assert argv == [sys.executable, "-m", "media_ai", update.REFRESH_COMMAND]


def test_a_spawn_that_fails_is_silence(tmp_path, monkeypatch):
    import subprocess

    def boom(argv, **kw):
        raise OSError("no fork for you")

    monkeypatch.setattr(update, "_spawn", _REAL_SPAWN)
    monkeypatch.setattr(subprocess, "Popen", boom)
    assert update.refresh_detached(media_ai.__version__) is False


@pytest.mark.parametrize("blowup", [OSError("no fork"), ValueError("not on this platform")])
def test_any_way_a_spawn_can_fail_is_the_same_failure(tmp_path, monkeypatch, blowup):
    """`subprocess` raises different things on different platforms for arguments this
    call hard-codes. None of them is worth more than a debug line."""
    import subprocess

    def boom(argv, **kw):
        raise blowup

    monkeypatch.setattr(update, "_spawn", _REAL_SPAWN)
    monkeypatch.setattr(subprocess, "Popen", boom)
    assert update.refresh_detached(media_ai.__version__) is False


# ----------------------------------------------------------------------- the lock


def test_only_one_process_fetches_at_a_time(tmp_path, monkeypatch):
    """Two shells starting in the same millisecond is the case the stamp cannot cover.
    The loser does not wait — it has nothing to do."""
    published(tmp_path, monkeypatch, FEED)
    inner = []

    def reentrant(url):
        inner.append(update.refresh_now(media_ai.__version__))
        return dict(FEED)

    monkeypatch.setattr(update, "_fetch", reentrant)
    assert update.latest_version(update.refresh_now(media_ai.__version__)) == "9.9.9"
    assert inner == [None], "the second attempt found the lock held and gave up"


def test_the_lock_is_released_afterwards(tmp_path, monkeypatch):
    published(tmp_path, monkeypatch, FEED)
    update.refresh_now(media_ai.__version__)
    assert not update.lock_path().exists()


def test_the_lock_is_released_even_when_the_fetch_explodes(tmp_path, monkeypatch):
    def boom(url):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(update, "_fetch", boom)
    with pytest.raises(RuntimeError):
        update.refresh_now(media_ai.__version__)
    assert not update.lock_path().exists()


def test_a_lock_left_by_a_killed_process_does_not_lock_the_machine_out(tmp_path, monkeypatch):
    """The window is minutes rather than seconds because "certainly abandoned" is doing
    real work: a slow-but-live fetch must never be mistaken for a dead one."""
    published(tmp_path, monkeypatch, FEED)
    update.lock_path().parent.mkdir(parents=True, exist_ok=True)
    update.lock_path().write_text("4242", encoding="utf-8")
    stale = time.time() - (update._LOCK_STALE_SECONDS + 60)
    os.utime(update.lock_path(), (stale, stale))
    assert update.latest_version(update.refresh_now(media_ai.__version__)) == "9.9.9"


def test_a_lock_younger_than_the_window_is_left_alone(tmp_path, monkeypatch):
    published(tmp_path, monkeypatch, FEED)
    update.lock_path().parent.mkdir(parents=True, exist_ok=True)
    update.lock_path().write_text("4242", encoding="utf-8")
    assert update.refresh_now(media_ai.__version__) is None
    assert update.lock_path().exists(), "somebody else's live lock, still theirs"


# ------------------------------------------------------------------ the cache write


def test_the_cache_is_replaced_atomically_never_written_through(tmp_path, monkeypatch):
    """A reader that catches the file half-written sees malformed JSON, and for
    `below_floor` and `retirement_for` that reads as "no policy" rather than as an
    error. The writer is a different process from the readers now, so the window has to
    not exist rather than be small."""
    seen = []
    real = os.replace
    monkeypatch.setattr(update.os, "replace", lambda src, dst: (seen.append((src, dst)), real(src, dst))[1])
    update._store(dict(FEED))
    (src, dst) = seen[0]
    assert str(dst) == str(update.cache_path())
    assert os.path.dirname(src) == str(update.cache_path().parent), "same filesystem, so rename is atomic"


def test_a_failed_write_leaves_no_rubble(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(update.os, "replace", boom)
    update._store(dict(FEED))
    assert list(update.cache_path().parent.glob("*.tmp")) == []


def test_the_cache_is_readable_by_whoever_runs_the_next_command(tmp_path):
    """`mkstemp` makes a 0600 file. An image built as root and run as somebody else is
    the ordinary shape of a container, and a cache the reader cannot open is a floor
    that silently stops applying."""
    update._store(dict(FEED))
    assert update.cache_path().stat().st_mode & 0o044 == 0o044


# ------------------------------------------------------- what the request may carry


def test_the_request_identifies_nobody(tmp_path, monkeypatch):
    """An anonymous GET of a static file. A `?version=` or a `?id=` on the URL would
    make this a telemetry endpoint wearing an update check's clothes — a different
    thing to build, to document, and to be asked about. `[telemetry]` is where anything
    that reports lives."""
    import urllib.request

    seen = []

    def capture(request, timeout=None):
        seen.append(request)
        raise OSError("that is far enough")

    monkeypatch.setattr(urllib.request, "urlopen", capture)
    update.refresh_now(media_ai.__version__)
    (request,) = seen
    assert request.full_url == update.FEED_URL
    assert "?" not in request.full_url
    assert request.get_method() == "GET"
    assert [k.lower() for k in request.headers] == ["user-agent"]
    assert media_ai.__version__ in request.headers["User-agent"]


# --------------------------------------------------------------- the hidden command


def test_the_errand_has_a_command_to_run(tmp_path, monkeypatch, capsys):
    """The argv the parent builds and the argv the child is dispatched by are one
    declaration — a rename cannot leave a background process exiting 2 forever."""
    from media_ai.__main__ import main

    published(tmp_path, monkeypatch, FEED)
    old, sys.argv = sys.argv, ["media-ai", update.REFRESH_COMMAND]
    try:
        assert main() == 0
    finally:
        sys.argv = old
    assert capsys.readouterr().out == "", "it answers nobody; its stdout is /dev/null"
    assert update.latest_version(update.cached()) == "9.9.9"


def test_the_errand_cannot_fail(tmp_path, monkeypatch, capsys):
    """Nothing waits on it, so an exit status is a fact with no reader — and a traceback
    from a process the user did not start is a bug report about a feature whose entire
    promise is that it costs nothing."""
    from media_ai.cli import _refresh

    monkeypatch.setattr(update, "refresh_now", lambda version: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _refresh.main() == 0
    assert capsys.readouterr().out == ""


def test_the_errand_is_not_a_command_group():
    """`_GROUPS` is a contract — one entry per scene group, a help line each, printed in
    `--help`. An internal errand is none of those, and listing it would be an invitation
    to run it."""
    from media_ai.__main__ import _GROUPS, _usage

    assert update.REFRESH_COMMAND not in _GROUPS
    import io

    buf = io.StringIO()
    _usage(buf)
    assert update.REFRESH_COMMAND not in buf.getvalue()


def test_a_mistyped_internal_name_is_still_an_unknown_group(tmp_path, capsys):
    from media_ai.__main__ import main

    old, sys.argv = sys.argv, ["media-ai", "__refresh-fed"]
    try:
        assert main() == 2
    finally:
        sys.argv = old
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "cli"


# --------------------------------------------------------- hooked into every command


def _ordinary_command(capsys) -> dict:
    """`bindings list` — offline, needs no config, and promises nothing about the
    network. Deliberately not `version show` or `doctor`: those two opt out, which is
    what `test_a_command_that_promised_not_to_touch_the_network_does_not` is about."""
    from media_ai.cli import bindings as bindings_mod

    old, sys.argv = sys.argv, ["media-ai bindings", "list"]
    try:
        assert bindings_mod.main() == 0
    finally:
        sys.argv = old
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_an_ordinary_command_leaves_a_refresh_behind(tmp_path, spawns, capsys):
    assert _ordinary_command(capsys)["ok"] is True
    assert len(spawns.calls) == 1


def test_the_result_is_printed_before_anything_is_spawned(tmp_path, monkeypatch, capsys):
    """Before the command means every call pays for a request it did not ask for; after
    means this run used what was on disk and the next one gets the newer answer. For a
    document measured in days that is not a compromise."""
    import subprocess

    order = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: order.append("spawned"))
    monkeypatch.setattr(update, "mark_checked", lambda: True)
    from media_ai.cli import common

    real_emit = common.emit
    monkeypatch.setattr(common, "emit", lambda obj, args: (order.append("printed"), real_emit(obj, args))[1])
    monkeypatch.setattr(update, "_spawn", _REAL_SPAWN)
    _ordinary_command(capsys)
    assert order == ["printed", "spawned"]


def test_a_failing_command_still_leaves_one_behind(tmp_path, spawns, capsys):
    """Being out of date is a plausible reason for the failure, and the notice riding
    the next invocation is how anyone finds out."""
    from media_ai.cli import common
    from media_ai.core.errors import ErrorCategory, MediaError

    def boom(args):
        raise MediaError("nope", category=ErrorCategory.PROVIDER)

    class Args:
        pretty = False
        _command = "test.boom"

    assert common.run(boom, Args()) == 6
    capsys.readouterr()
    assert len(spawns.calls) == 1


def test_an_interrupted_command_leaves_nothing_running(tmp_path, spawns, capsys):
    """Ctrl-C means stop. A process that outlives the one just killed is the opposite."""
    from media_ai.cli import common

    def interrupted(args):
        raise KeyboardInterrupt

    class Args:
        pretty = False
        _command = "test.interrupted"

    assert common.run(interrupted, Args()) == 7
    capsys.readouterr()
    assert spawns.calls == []


def test_uninstall_does_not_put_back_what_it_just_removed(tmp_path, monkeypatch, spawns, capsys):
    """"Uninstalling leaves nothing behind" — and a file recreated a millisecond after
    removal is the most confusing possible way to break it: the command reports the path
    as removed, and the path is there."""
    from media_ai.cli import uninstall

    cache(tmp_path)
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
    old, sys.argv = sys.argv, ["media-ai uninstall", "--yes", "--skills-dest", str(tmp_path / "none")]
    try:
        uninstall.main()
    finally:
        sys.argv = old
    capsys.readouterr()
    assert spawns.calls == []
    assert not update.cache_path().exists()


@pytest.mark.parametrize("group,argv", [
    ("doctor", []),
    ("version", ["show"]),
    ("version", ["check", "--offline"]),
])
def test_a_command_that_promised_not_to_touch_the_network_does_not(tmp_path, spawns, capsys, group, argv):
    """A detached child is still *this* command reaching the network. It does not become
    somebody else's request by leaving in another process, and an air-gapped machine
    running a command documented as offline would see a connection attempt either way."""
    from importlib import import_module

    main = import_module(f"media_ai.cli.{group}").main
    old, sys.argv = sys.argv, [f"media-ai {group}", *argv]
    try:
        main()
    finally:
        sys.argv = old
    capsys.readouterr()
    assert spawns.refreshes == []


def test_a_trapped_signal_counts_as_an_interruption(tmp_path, spawns, capsys):
    """`volc_ark._poll` traps SIGTERM/SIGINT so it can cancel the billed task, and hands
    back a `MediaError` instead of letting the interrupt through — so Ctrl-C on an Ark
    video wait would otherwise be the one interruption that still forks."""
    from media_ai.cli import common
    from media_ai.core.errors import ErrorCategory, MediaError

    def killed(args):
        raise MediaError("interrupted (signal 2); task cancelled",
                         category=ErrorCategory.TIMEOUT, code=common.INTERRUPTED)

    class Args:
        pretty = False
        _command = "video.generate"

    assert common.run(killed, Args()) == 7
    capsys.readouterr()
    assert spawns.calls == []


def test_the_provider_that_traps_signals_still_says_so():
    """The code above is a contract between two files. A rename here is a background
    process that outlives a Ctrl-C, which nothing else would notice."""
    import ast
    from pathlib import Path

    from media_ai.cli.common import INTERRUPTED
    from media_ai.providers import volc_ark

    tree = ast.parse(Path(volc_ark.__file__).read_text(encoding="utf-8"))
    codes = {
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "MediaError"
        for kw in node.keywords
        if kw.arg == "code" and isinstance(kw.value, ast.Constant)
    }
    assert INTERRUPTED in codes


def test_an_ordinary_failure_is_not_an_interruption(tmp_path, spawns, capsys):
    """The command ran and did not work — being out of date is a plausible reason, and
    the notice riding the next invocation is how anyone finds out."""
    from media_ai.cli import common
    from media_ai.core.errors import ErrorCategory, MediaError

    def failed(args):
        raise MediaError("the provider said no", category=ErrorCategory.TIMEOUT)

    class Args:
        pretty = False
        _command = "video.generate"

    assert common.run(failed, Args()) == 7
    capsys.readouterr()
    assert len(spawns.calls) == 1


# ------------------------------------------------- a stamp is not a discovery


def test_a_machine_that_never_reached_the_feed_does_not_claim_to_be_current(tmp_path, monkeypatch):
    """The stamp records an attempt. Reported as an arrival, `doctor` answers "0.9.0 is
    current as of today" on a machine that has never read the feed at all — not merely
    imprecise but exactly backwards, since what it reassures you about is the thing that
    did not happen."""
    from media_ai.cli import doctor

    monkeypatch.setattr(update, "_fetch", lambda url: None)
    update.refresh_now(media_ai.__version__)
    (found,) = doctor._check_update()
    assert found["status"] == "ok"
    assert "no release feed cached yet" in found["detail"]


def test_a_failed_attempt_does_not_age_what_is_already_known(tmp_path):
    """The other direction: a machine that fetched yesterday and could not reach the
    feed today still holds yesterday's answer, and reports its real age."""
    yesterday = time.time() - 86400
    (tmp_path / "update-cache.json").write_text(
        json.dumps({"checked_at": yesterday, "fetched_at": yesterday, "feed": FEED}), encoding="utf-8")
    assert update.mark_checked() is True
    assert update.checked_at() > yesterday
    assert update.cached_at() == pytest.approx(yesterday)
    assert update.latest_version(update.cached()) == "9.9.9"


def test_a_cache_from_before_the_second_stamp_is_read_as_an_arrival(tmp_path):
    """The file is not versioned, and every install has one. An upgrade that reset
    everybody's idea of what they knew is worse than reading the old shape correctly:
    it only ever wrote on success, so its `checked_at` is a `fetched_at`."""
    when = time.time() - 3600
    (tmp_path / "update-cache.json").write_text(
        json.dumps({"checked_at": when, "feed": FEED}), encoding="utf-8")
    assert update.cached_at() == pytest.approx(when)
    assert update.checked_at() == pytest.approx(when)


def test_version_check_does_not_report_a_time_it_learned_nothing_at(tmp_path, monkeypatch, capsys):
    """`source: "none"` beside a fresh `checked_at` is a self-contradicting answer, and
    the interesting case — a machine behind a proxy — is exactly where it appears."""
    from media_ai.cli import version as version_mod

    monkeypatch.setattr(update, "_fetch", lambda url: None)
    update.refresh_now(media_ai.__version__)
    old, sys.argv = sys.argv, ["media-ai version", "check"]
    try:
        version_mod.main()
    finally:
        sys.argv = old
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["source"] == "none"
    assert out["checked_at"] is None


# --------------------------------------------------------- nothing left behind


def test_uninstall_takes_every_file_the_check_writes(tmp_path, monkeypatch, capsys):
    """A stray lock or a temp file left by an interrupted write is enough for
    `prune_empty` to find the directory non-empty and leave the whole of it behind."""
    from media_ai.cli import uninstall

    cache(tmp_path)
    update.lock_path().write_text("4242", encoding="utf-8")
    (tmp_path / "update-cache.json.abcdef.tmp").write_text("{half a docum", encoding="utf-8")
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
    old, sys.argv = sys.argv, ["media-ai uninstall", "--yes", "--skills-dest", str(tmp_path / "none")]
    try:
        uninstall.main()
    finally:
        sys.argv = old
    capsys.readouterr()
    assert not update.cache_path().exists()
    assert not update.lock_path().exists()
    assert list(tmp_path.glob("update-cache.json.*.tmp")) == []


def test_the_suite_itself_forks_nothing(tmp_path, capsys):
    """`conftest._no_background_refresh` is what keeps a `pytest -q` run from starting
    seventy interpreters that write into `tmp_path` after teardown. Asserted here rather
    than trusted, since it is invisible when it works."""
    import subprocess

    started = []
    real = subprocess.Popen
    try:
        subprocess.Popen = lambda argv, **kw: started.append(argv)
        _ordinary_command(capsys)
    finally:
        subprocess.Popen = real
    assert started == []


def test_a_command_never_waits_for_the_answer(tmp_path, monkeypatch, capsys):
    """The invariant the whole arrangement exists for, asserted the blunt way: a feed
    that takes forever to serve does not make a command take forever to finish."""
    import subprocess

    monkeypatch.setattr(update, "_fetch", lambda url: pytest.fail("the hot path fetched"))
    monkeypatch.setattr(update, "_spawn", _REAL_SPAWN)
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: time.sleep(0))
    started = time.monotonic()
    _ordinary_command(capsys)
    assert time.monotonic() - started < 5
