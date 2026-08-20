"""Reading the release feed: is there something newer than what is running here?

The published half is ``release-feed.json`` at the repo root (see
``docs/DEVELOPMENT.md``). This is the client, and it is built around one rule:

**A generation command never waits on the network to answer a question it was not
asked.** So there are three entry points and they are not interchangeable:

- :func:`cached` reads a file and cannot block. Everything on the hot path takes it,
  including the two decisions the feed is allowed to make (see ``below_floor`` and
  ``retirement_for``).
- :func:`refresh` may make one bounded request *in this process*, and is called only
  where waiting is already acceptable — ``init``, ``version check``, ``upgrade``.
- :func:`refresh_detached` makes the same request in **another** process and returns
  immediately. It is what makes the check automatic, and it runs after a command has
  already printed its result, never before.

That third one is the whole reason the first two are not enough. The feed's two
withholding powers are read from the cache, so a cache nobody ever refreshes is a
policy nobody ever receives: a floor published today would reach only the machines
whose owner happened to run ``init`` or ``version check`` afterwards. Refreshing on the
way *out* of an ordinary command costs the caller nothing and closes that gap by one
invocation — the worst case is that this run used yesterday's answer and the next one
does not, which is the right resolution for a policy measured in days.

Why another process rather than a thread or a blocking call at the end: a fetch has a
ten-second ceiling, and a command that has already printed its JSON must not stay alive
to find out. A detached child (``start_new_session=True``, every stream on
``/dev/null``, never waited on) is what ``gh``, ``brew`` and ``npm`` do, and it is the
only shape where "checked automatically" and "noticed by nobody" are both true.

Three more rules, inherited from :mod:`media_ai.cli._announce`, which sketched this
interface before there was anything behind it:

- **A failed fetch is silence, not an error.** Nothing here is load-bearing; a CDN
  having a bad day must not fail an install, a generation, or a diagnosis.
- **The document is display-only.** Nothing in it names a command, a URL to call, or a
  binding to use. The most a feed can ever do is *withhold* — say a version is too old,
  or a binding is gone — which bounds the worst case of a compromised feed at denial of
  service rather than redirection. Fields that could change where a call goes are not
  in the schema and must not be added.
- **Its readers are always older than it is.** A feed whose ``schema`` is beyond this
  build is ignored *whole* rather than partially understood, and unknown keys are
  skipped rather than rejected.

The cache is JSON, unlike every other file this tool writes. It is a verbatim copy of a
JSON document plus one timestamp, and putting it through the TOML writer's deliberately
narrow subset would mean re-encoding something we should never reinterpret. The rule
that falls out: **files a human edits are TOML, machine caches are JSON.**
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from ..brand import cli_name
from . import envvars
from .envflag import env_flag
from .logging import get_logger
from .versioning import VERSION, precedence

if TYPE_CHECKING:
    from .config import UpdateSettings

__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "FEED_URL",
    "IMPORT_PACKAGE",
    "REFRESH_COMMAND",
    "below_floor",
    "cache_path",
    "cached",
    "cached_at",
    "checked_at",
    "due",
    "interval_seconds",
    "is_newer",
    "latest_version",
    "lock_path",
    "mark_checked",
    "minimum_supported",
    "notices_for",
    "parse_feed",
    "refresh",
    "refresh_detached",
    "refresh_now",
    "retirement_for",
    "settings",
    "settings_from",
    "should_check",
]

#: Where this build's code comes from — **not** what the tool is called.
#:
#: :mod:`media_ai.brand` is explicit that the two are different: a white-label build
#: renames the executable and still fetches from here. So this is the one declaration
#: of the repository, and the three places that need it — the feed URL below, the
#: release link in ``scripts/bump_version.py``, and ``REPO`` in ``install/install.sh``
#: — all derive from or are pinned to it, the same arrangement ``__version__`` has.
SOURCE_REPO = "BrandoZhang/media-ai"

#: Where the feed is published. Served from the default branch rather than through the
#: releases API, which allows about 60 anonymous requests an hour per IP — a limit one
#: office behind a shared NAT would exhaust, and ``install.sh`` already carries a
#: fallback pin because of it.
#:
#: **Every client ever shipped fetches whatever this says**, so it is effectively
#: permanent: changing it abandons every install that has not upgraded.
FEED_URL = f"https://raw.githubusercontent.com/{SOURCE_REPO}/main/release-feed.json"

#: The hidden group that runs one refresh and prints nothing — the errand a detached
#: child is spawned to do. Declared here, next to the code that spawns it, and read by
#: ``media_ai.__main__`` when it dispatches: the argv the parent builds and the argv the
#: child is dispatched by are the same fact, and a second copy of it is a rename away
#: from a background process that exits 2 forever without anyone noticing.
#:
#: Two leading underscores because a group is otherwise a word a user is invited to
#: type. This one is not: it takes no arguments, answers nothing, and has ``version
#: check`` as its supported equivalent.
REFRESH_COMMAND = "__refresh-feed"

#: The import package, for ``python -m``. Derived rather than typed —
#: :mod:`media_ai.brand` renames the *command* and never this, and the runtime already
#: knows the one true answer.
IMPORT_PACKAGE = __package__.split(".", 1)[0]

#: The highest feed schema this build understands. A document above it is ignored
#: entirely; see the module docstring.
FEED_SCHEMA = 1

_TIMEOUT_SECONDS = 10.0
_MAX_BYTES = 256 * 1024

#: How long a cached feed is treated as current, unless ``[update] interval`` or
#: ``$MEDIA_AI_UPDATE_INTERVAL`` says otherwise. A day, because the things this document
#: can say — a release is out, a version is unsupported, a binding is gone — are
#: decisions taken over days, and the cost of being one day late is one more invocation
#: of a command that was going to run anyway.
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60

#: When a refresh lock left behind by a process that died is ignored and removed.
#: Comfortably above :data:`_TIMEOUT_SECONDS` so a slow-but-live fetch is never treated
#: as abandoned, and low enough that a machine killed mid-check is not locked out of
#: checking for the rest of the day.
_LOCK_STALE_SECONDS = 120.0


def cache_path() -> Path:
    """Beside ``config.toml``, so ``$MEDIA_AI_CONFIG_FILE`` relocates the whole set.

    Derived from :func:`config_path`, not from :func:`~media_ai.brand.config_dir`: the
    two agree on a default install and diverge the moment anyone points the environment
    variable somewhere else, which is what every test does and what a scratch profile
    is for. Reading the brand directory here would write into the developer's real
    ``~/.config`` from inside the suite — the same trap the install receipt avoids by
    hanging off the same function.
    """
    from .config import config_path

    return config_path().parent / "update-cache.json"


def lock_path() -> Path:
    """The refresh lock, beside the cache it guards.

    Its own file rather than a flock on the cache: the cache is read on the hot path by
    every command, and a reader that has to open a file the writer may hold is a reader
    that can be made to wait. Nothing ever waits on this one — a process that cannot
    take it has nothing to do and exits.
    """
    return cache_path().with_name("update-refresh.lock")


# Environment beats config beats the built-in default. That is the ordinary shape for a
# *preference*, and not a contradiction of the rule that credentials have no precedence
# chain: that rule exists so "where did this key come from?" is answerable without
# reasoning, and here the answer is reported by `settings_from` and printed by
# `version check`, so it stays answerable by looking rather than by knowing the order.


def _configured() -> UpdateSettings:
    """``[update]`` from the config file, or the defaults if it cannot be read.

    Swallowing the error is deliberate and narrow. This is consulted on the way into
    ``init``, which is the command a user runs *because* their configuration is
    broken — and a malformed config file surfacing first as a failure of the update
    check would bury the real message under an unrelated one. Every other reader of
    this file still refuses loudly.
    """
    from .config import UpdateSettings, load_config

    try:
        return load_config().update
    except Exception:  # noqa: BLE001 - a preference is never worth failing a command over
        return UpdateSettings()


def _env_interval() -> int | None:
    """``$MEDIA_AI_UPDATE_INTERVAL`` in seconds, or ``None`` when it says nothing usable.

    Ignored rather than refused when it is not a whole number of seconds, which is the
    opposite of how ``[update] interval`` is read one layer down — deliberately. The
    config file is validated once, at load, by a reader that is allowed to fail the
    command; this is read on the way out of *every* command, and a typo in a variable
    exported by a shell profile would otherwise turn every invocation on that machine
    into an error about a preference nobody was exercising. A debug line says so for
    anyone who goes looking.
    """
    raw = os.getenv(envvars.UPDATE_INTERVAL, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        get_logger().debug("ignoring MEDIA_AI_UPDATE_INTERVAL=%r: not a whole number of seconds", raw)
        return None
    if value < 0:
        get_logger().debug("ignoring MEDIA_AI_UPDATE_INTERVAL=%r: an interval cannot be negative", raw)
        return None
    return value


def settings() -> UpdateSettings:
    """The effective settings, with the environment layered over the config."""
    from .config import UpdateSettings

    base = _configured()
    env_check = env_flag(envvars.UPDATE_CHECK)
    env_interval = _env_interval()
    return UpdateSettings(
        check=base.check if env_check is None else env_check,
        feed=os.getenv(envvars.UPDATE_FEED) or base.feed,
        interval=base.interval if env_interval is None else env_interval,
    )


def interval_seconds() -> int:
    """How long a cached feed stays current on this machine."""
    return settings().interval


def settings_from() -> dict[str, str]:
    """Which layer each effective setting came from: ``env``, ``config`` or ``default``.

    Reported by ``version check`` so a machine that is quietly not checking can be
    asked *why* — the failure mode of a precedence chain is not that it is wrong, it is
    that nobody can see which rung won.
    """
    from .config import UpdateSettings

    base = _configured()
    return {
        "check": "env" if env_flag(envvars.UPDATE_CHECK) is not None else ("config" if not base.check else "default"),
        "feed": "env" if os.getenv(envvars.UPDATE_FEED) else ("config" if base.feed else "default"),
        "interval": (
            "env" if _env_interval() is not None
            else ("config" if base.interval != UpdateSettings().interval else "default")
        ),
    }


def feed_url() -> str:
    """The feed to read: the environment, then the config, then where this build ships.

    An internal distribution points every install at its own mirror once, at setup;
    ``$MEDIA_AI_UPDATE_FEED`` covers the machine that needs one before any config exists
    (an air-gapped box, a test) and wins when both are set.
    """
    return settings().feed or FEED_URL


# ---------------------------------------------------------------- when to ask


def should_check(version: str) -> bool:
    """Whether this machine should make an unsolicited request at all.

    Turned off by ``[update] check = false`` or ``MEDIA_AI_UPDATE_CHECK=0``; see
    :func:`settings`. Those two are the escape hatch that has to exist for a check that
    happens by default, and they turn off the *request* — the cached answer is still
    read and reported, because nothing about that costs anybody anything.

    ``CI`` is the second one, and it matters more now that the check is automatic than
    it did when it only guarded ``init``. A pipeline runs the CLI on a fresh container,
    so every job would fetch, no cached answer would ever survive to be used, and the
    only party who could act on "a newer release is published" is not watching. What it
    would reliably produce is noise on the day the network is flaky. A runner that does
    want the check says so with ``MEDIA_AI_UPDATE_CHECK=1``, which is read three-state and
    therefore wins.

    Deliberately *not* :func:`media_ai.cli._prompt._nobody_is_watching`, which looks
    almost the same and answers a different question. That one asks "will a human
    answer a prompt", and ``MEDIA_AI_NO_TTY`` therefore turns it off — but an agent
    harness with no terminal still wants to be told its CLI is out of date, since the
    agent is the party that can act on it. The overlap is only ``CI``, and coupling
    them would let a change to prompting quietly change network behaviour.

    A version that is not a clean release is skipped outright: an editable checkout or
    a development build has no meaningful "newer" to be told about, and telling someone
    working on the tool to go and install it is noise.
    """
    asked = env_flag(envvars.UPDATE_CHECK)
    if not settings().check:
        return False
    # `CI` decides only where nothing else has. That is the whole point of reading a
    # flag in three states (`core/envflag.py`): "the variable says nothing" and "the
    # variable says no" are different answers, and an override that could only ever
    # force one direction would leave a runner that genuinely wants the check — a
    # nightly that reports its own staleness, an agent harness that sets `CI` because
    # it is one — with no way to say so.
    if asked is None and env_flag("CI"):
        return False
    return bool(VERSION.match(version))


# ------------------------------------------------------------ reading a feed


def parse_feed(data: object) -> dict | None:
    """Validate a decoded feed, or ``None`` if this build should not read it.

    ``None`` is not an error: it is "there is nothing here I can safely use". A feed
    from the future is the important case — understanding half of a document whose
    meaning has moved is worse than ignoring it, and the reader has no way to be told
    otherwise, since it is the part that cannot be upgraded.
    """
    if not isinstance(data, dict):
        return None
    schema = data.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int):
        return None
    if schema > FEED_SCHEMA:
        get_logger().debug("ignoring release feed with schema %s; this build reads %s", schema, FEED_SCHEMA)
        return None
    return data


def latest_version(feed: dict | None) -> str | None:
    """The published version, if the feed names one that is actually a version."""
    if not isinstance(feed, dict):
        return None
    latest = feed.get("latest")
    if not isinstance(latest, dict):
        return None
    version = latest.get("version")
    return version if isinstance(version, str) and VERSION.match(version) else None


def is_newer(candidate: str | None, current: str) -> bool:
    """Whether ``candidate`` is a version worth telling someone about.

    Tolerant on purpose: this compares a number that came off the network against one
    compiled in, and a malformed remote value means "say nothing", never an exception
    in the middle of an unrelated command.
    """
    if not candidate:
        return False
    try:
        return precedence(candidate) > precedence(current)
    except ValueError:
        return False


def notices_for(feed: dict | None, version: str) -> list[dict]:
    """The feed's notices that apply to ``version``, in order.

    Applicability is two plain comparisons against ``min_version`` / ``max_version``,
    because the alternative — a range expression — is a language parsed by the *old*
    clients, and one that misparses does not fail loudly. It shows the wrong people a
    notice.

    An entry missing its title or body is dropped rather than rendered half-empty, and
    one whose bounds do not parse is dropped rather than shown to everybody: an
    unreadable bound is not "no bound".
    """
    out = []
    for entry in (feed or {}).get("notices", []) or []:
        if not isinstance(entry, dict) or not entry.get("title") or not entry.get("body"):
            continue
        low, high = entry.get("min_version"), entry.get("max_version")
        # Checked before comparing, and separately from the parse below, because a bound
        # that is not a string makes `precedence` raise TypeError — which `except
        # ValueError` does not catch. So a feed carrying `min_version = 1` did not
        # mis-target a notice, it crashed `version` and the setup banner outright, on
        # the strength of a field nobody authenticates.
        if any(bound is not None and not isinstance(bound, str) for bound in (low, high)):
            continue
        try:
            # `is not None`, not truthiness: `min_version = ""` is a bound that cannot be
            # read, not an absent one, and falls to the same `continue` as `"latest"`.
            if low is not None and precedence(version) < precedence(low):
                continue
            if high is not None and precedence(version) > precedence(high):
                continue
        except ValueError:
            continue
        out.append(entry)
    return out


# ------------------------------------------------------- what it may withhold

# The only two fields that change behaviour rather than display, and both can only
# *stop* something. That asymmetry is the security model: a feed nobody authenticates
# must not be able to point a call anywhere, so the worst a wrong or hostile one can do
# is refuse. Reading them is deliberately unforgiving in the same direction — anything
# not understood is ignored, because the alternative to a missed block is a fleet
# locked out by a typo.


def minimum_supported(feed: dict | None) -> str | None:
    """The floor below which this build should stop calling providers, if one is set.

    Absent is the ordinary state and means no floor — an absent limit is an absent
    field here as everywhere, and a sentinel would invite a comparison against it. An
    unparseable value is also ``None``: a floor nobody can read is not a floor, and
    guessing at one is how a published typo becomes an outage.
    """
    floor = (feed or {}).get("min_supported")
    if not isinstance(floor, str) or not VERSION.match(floor):
        return None
    return floor


def below_floor(version: str, feed: dict | None) -> str | None:
    """The floor ``version`` falls below, or ``None`` when it does not."""
    floor = minimum_supported(feed)
    if not floor:
        return None
    try:
        return floor if precedence(version) < precedence(floor) else None
    except ValueError:
        return None


def retirement_for(binding_id: str, feed: dict | None) -> dict | None:
    """The retirement that applies to ``binding_id`` *today*, or ``None``.

    ``since`` is the date it starts applying; absent means immediately, and a date that
    is not a date drops the entry. ``severity`` must be one this build knows —
    ``warn`` or ``block`` — and an unrecognised one drops the entry rather than being
    rounded up to the stricter reading. A newer feed inventing a severity is exactly
    the case where an old client should do nothing: it cannot know what the new word
    means, and acting on a guess in the blocking direction turns "this build is old"
    into "this build refuses to work".
    """
    from datetime import date

    for entry in (feed or {}).get("retired_bindings", []) or []:
        if not isinstance(entry, dict) or entry.get("binding") != binding_id:
            continue
        if entry.get("severity") not in {"warn", "block"}:
            continue
        since = entry.get("since")
        if since is not None:
            try:
                if date.fromisoformat(str(since)) > date.today():
                    continue
            except ValueError:
                continue
        return entry
    return None


# ----------------------------------------------------------------- the cache


def cached() -> dict | None:
    """The last feed this machine fetched, or ``None``. Never touches the network.

    This is what every command on the hot path calls. An unreadable or absent cache is
    the ordinary state on a fresh install and answers ``None``.
    """
    return parse_feed(_raw_cache().get("feed"))


def _raw_cache() -> dict:
    """The cache file as it is on disk, or ``{}``. Every reader here goes through it."""
    try:
        raw = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _stamp(raw: dict, *keys: str) -> float | None:
    """The first of ``keys`` that holds a usable timestamp, or ``None``.

    ``is not None`` rather than truthiness at each step: ``0`` is a lie of a timestamp
    but it is a *present* one, and falling through it would answer with a different
    field's value instead of with the epoch. Same rule as `notices_for`'s bounds.
    """
    for key in keys:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


# Two stamps, because the check became automatic and they stopped being the same fact.
#
# While a fetch only happened when somebody asked for one, "when did this machine last
# check" and "how old is what it knows" had one answer: the cache was written when a
# feed arrived and at no other time. `mark_checked` breaks that — it records an
# *attempt*, before the request, precisely so a machine that can never reach the feed
# stops trying twenty times a command.
#
# Keeping one stamp for both would have made every failed attempt look like a
# successful one. `doctor` on a machine that has never reached the feed would report
# "0.9.0 is current as of today" — an answer that is not merely imprecise but exactly
# backwards, since the thing it is reassuring you about is the thing that did not
# happen. So: `checked_at` is when we last tried (the TTL reads it), `fetched_at` is
# when what we hold arrived (every display reads it).


def cached_at() -> float | None:
    """When the cached feed arrived, as a unix timestamp, or ``None``.

    What ``doctor`` and ``version check`` report, because what a reader wants to know
    is the age of the *answer*, not the recency of the last failed attempt to improve
    it. ``None`` whenever there is no feed — a machine that has tried and never
    succeeded has learned nothing, and must keep saying so.

    A cache written before this build had one stamp and only ever wrote it on success,
    so its ``checked_at`` is a ``fetched_at``. Read that way rather than discarded: the
    file is not versioned, and an upgrade that silently reset every install's idea of
    what it knows is a worse answer than reading the old shape correctly.
    """
    raw = _raw_cache()
    if parse_feed(raw.get("feed")) is None:
        return None
    return _stamp(raw, "fetched_at", "checked_at")


def checked_at() -> float | None:
    """When this machine last *tried*, successfully or not. What the TTL reads."""
    raw = _raw_cache()
    return _stamp(raw, "checked_at", "fetched_at")


def _write_cache(payload: dict) -> bool:
    """Replace the cache atomically, best-effort. An unwritable cache is not a failure.

    Temp file in the same directory plus :func:`os.replace`, rather than writing over
    the file in place. The writer is now a *different process* from the readers — a
    background refresh runs while the next command is already starting — and a reader
    that catches the file half-written sees malformed JSON. :func:`cached` treats that
    as "no cache", which is survivable; :func:`below_floor` and :func:`retirement_for`
    read the same document, and there "no cache" means a published block silently does
    not apply. A rename within one directory is the cheap way for that window not to
    exist.
    """
    path = cache_path()
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        # `mkstemp` makes a 0600 file, which is right for a temporary one and wrong for
        # this. The cache holds a public document and no secret, and the install that
        # writes it is routinely not the account that reads it — an image built as root
        # and run as somebody else is the ordinary shape of a container. A cache the
        # reader cannot open is not a slower check, it is a floor that silently stops
        # applying and a notice that silently stops appearing.
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        get_logger().debug("could not write %s: %s", path, exc)
        if tmp:
            with suppress(OSError):
                os.unlink(tmp)
        return False


def _store(feed: dict) -> None:
    """Record a feed that was just fetched, and the moment it was."""
    now = time.time()
    _write_cache({"checked_at": now, "fetched_at": now, "feed": feed})


def mark_checked() -> bool:
    """Stamp "tried just now", without touching what is cached or when it arrived.

    Written **before** the request, not after a successful one, and that order is the
    load-bearing part. A stamp that only moves on success means a machine that cannot
    reach the feed — offline, sandboxed, behind a proxy that swallows it — is due for a
    check on every single command, so every command spawns a refresh that fails. Twenty
    commands in a script become twenty processes and twenty timeouts. Stamping first
    makes a failed check cost exactly as much as a successful one: one attempt per
    interval.

    The trade is that a fetch which fails leaves the next attempt an interval away
    rather than immediately, which is the correct side to err on for a document whose
    contents change over days. Anyone who needs an answer *now* has ``version check``,
    which forces past both the stamp and the interval.

    Returns whether the stamp was written. It is a real answer, not decoration: a caller
    that could not write it must not spawn a refresh, or a read-only config directory
    would reintroduce the storm this prevents from the other end.
    """
    raw = _raw_cache()
    payload: dict = {"checked_at": time.time()}
    # Whatever is already known is carried across untouched, including *when* it
    # arrived. An attempt is not a discovery, and the only field it may move is its own.
    kept = parse_feed(raw.get("feed"))
    if kept is not None:
        payload["feed"] = kept
        # Absent rather than `null` when the file somehow holds a feed and no timestamp
        # — an absent limit is an absent field here as everywhere, and a `null` is one
        # more shape every reader would have to know about.
        arrived = _stamp(raw, "fetched_at", "checked_at")
        if arrived is not None:
            payload["fetched_at"] = arrived
    return _write_cache(payload)


# --------------------------------------------------------------- the network


def due(version: str) -> bool:
    """Whether this machine should go and look, right now.

    One question, asked from two places that must not disagree: the blocking
    :func:`refresh` and the detached :func:`refresh_detached`. It is deliberately cheap
    — a config read and a stat — because the second caller asks it after *every*
    command, and something on the hot path that reaches the network to decide whether
    to reach the network is the defect this whole module is arranged against.

    An interval of ``0`` means "every command", and is the setting a staged rollout or
    a demo wants. It still costs the caller nothing: the fetch happens elsewhere.
    """
    if not should_check(version):
        return False
    stamp = checked_at()
    if stamp is None:
        return True
    # A stamp from the future is a clock that moved, not a check from tomorrow. Left as
    # due rather than trusted, because the alternative is a machine that never checks
    # again until its clock catches up.
    age = time.time() - stamp
    return not (0 <= age < interval_seconds())


def refresh(version: str, *, force: bool = False) -> dict | None:
    """Fetch the feed if it is time to, cache it, and return it.

    Called only where waiting is already part of the deal. Returns ``None`` for every
    reason not to have fetched — checks are off, the cache is still fresh, the network
    failed, the document was unusable — because none of them is a distinction a caller
    should have to handle, and all of them mean the same thing: carry on with what is
    already on disk.
    """
    if not should_check(version):
        return None
    if not force and not due(version):
        return None
    raw = _fetch(feed_url())
    if raw is None:
        return None
    feed = parse_feed(raw)
    if feed is None:
        return None
    _store(feed)
    return feed


# ------------------------------------------------- checking without being noticed


@contextmanager
def _refresh_lock():
    """Yield whether this process may be the one that fetches. Never waits.

    ``O_CREAT | O_EXCL`` is the whole mechanism: it is one atomic syscall on every
    filesystem this runs on, and it needs no daemon, no cleanup on the happy path
    beyond an unlink, and no agreement between two processes that never talk.

    A lock is not enough on its own and is not asked to be. :func:`mark_checked` is what
    stops twenty commands in a script from *deciding* to refresh; this is what stops the
    handful that still race — two shells starting in the same millisecond, a stamp that
    could not be written — from making twenty requests. The failure it prevents is not
    correctness (two fetches would both write a valid document) but a stampede aimed at
    one host by a tool that is supposed to be invisible.

    A lock file left by a killed process is removed once it is old enough to be
    certainly abandoned; "certainly" is doing real work there, which is why the window
    is minutes rather than seconds and why a clock that moved in either direction
    counts as stale rather than as a live holder.
    """
    path = lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        get_logger().debug("could not prepare %s: %s", path.parent, exc)
        yield False
        return
    fd = _take(path)
    if fd is None and _clear_stale(path):
        fd = _take(path)
    if fd is None:
        get_logger().debug("another process is refreshing the release feed; nothing to do")
        yield False
        return
    try:
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        yield True
    finally:
        with suppress(OSError):
            path.unlink()


def _take(path: Path) -> int | None:
    """The lock's file descriptor, or ``None`` if somebody else holds it."""
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return None


def _clear_stale(path: Path) -> bool:
    """Remove a lock old enough to be abandoned; whether it is worth trying again."""
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return True  # it went away underneath us, which is the outcome we wanted
    if -_LOCK_STALE_SECONDS < age < _LOCK_STALE_SECONDS:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    get_logger().debug("removed a stale release-feed lock at %s", path)
    return True


def refresh_now(version: str) -> dict | None:
    """Fetch and cache the feed, once, quietly. This is what the detached child runs.

    Not the same function as :func:`refresh`, and the difference is the interval: by the
    time a child exists, the parent has already decided it is time *and* moved the stamp
    forward, so a child that re-read the interval would find it fresh and do nothing.
    The interval is a decision the parent makes; this is the errand.

    The lock is taken here rather than in the parent because it has to be held across
    the request, and the parent is a process that intends to exit immediately.
    """
    if not should_check(version):
        return None
    with _refresh_lock() as held:
        if not held:
            return None
        # Again here, and not only in the parent: this is also what a hand-run
        # `__refresh-feed` executes, and an attempt that reached the network without
        # recording that it happened is the storm this file keeps arranging against.
        mark_checked()
        raw = _fetch(feed_url())
        if raw is None:
            return None
        feed = parse_feed(raw)
        if feed is None:
            return None
        _store(feed)
        return feed


def refresh_detached(version: str) -> bool:
    """Start a background refresh if one is due, and return without waiting for it.

    Returns whether a child was started — for the tests and for a debug line, never for
    a caller to act on. Everything about this is best-effort by construction: there is
    no answer it could give that the calling command should change its behaviour for,
    and the command has already printed its result by the time this runs.

    The stamp moves *before* the spawn, so the next command in a script sees a machine
    that has just checked and does nothing. Without it, twenty commands would each find
    a stale cache and fork twenty children, nineteen of which would take the lock, fail,
    and exit — correct, and still twenty processes nobody asked for.
    """
    if not due(version):
        return False
    if not mark_checked():
        # Nowhere to record that a check happened means every command would spawn one.
        # A machine with a read-only config directory gets no automatic check, which is
        # the same answer it already gets for the cache the check would have written.
        return False
    return _spawn()


def _spawn() -> bool:
    """Launch ``<cli> __refresh-feed`` detached, or answer ``False``.

    ``start_new_session=True`` puts the child in its own process group, so it survives
    the parent exiting and — the part that matters at a terminal — a Ctrl-C aimed at the
    foreground group does not reach it half way through writing the cache.

    Every stream goes to ``/dev/null``. ``stdout`` because this project's contract is
    that a command emits exactly one JSON object and a child inheriting fd 1 could
    append a second; ``stderr`` because a log line from a process the user did not start
    is indistinguishable from one belonging to the command they did; ``stdin`` for the
    reason `media/ffmpeg.py` closes it — an inherited fd 0 is a child that can eat the
    rest of a ``while read`` loop.

    Which argv: a standalone bundle *is* its own executable, and a package install is
    reached through the interpreter already running this code rather than through
    whatever ``PATH`` resolves the brand name to — a virtualenv two directories away
    would otherwise refresh a cache belonging to a different install.
    """
    import subprocess

    from .packaging import is_standalone

    if not sys.executable:
        return False
    argv = (
        [sys.executable, REFRESH_COMMAND] if is_standalone()
        else [sys.executable, "-m", IMPORT_PACKAGE, REFRESH_COMMAND]
    )
    try:
        subprocess.Popen(  # noqa: S603 - argv is this interpreter and a fixed literal, never a shell
            argv,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    except Exception as exc:  # noqa: BLE001 - every way a spawn can fail is the same failure
        # Broader than `OSError` on purpose. `subprocess` raises different things on
        # different platforms for arguments this call hard-codes, and there is no
        # failure here worth more than a debug line: the command has printed its result,
        # and the only cost of not checking is checking next time instead.
        get_logger().debug("could not start a background release-feed refresh: %s", exc)
        return False
    get_logger().debug("started a background release-feed refresh")
    return True


def _fetch(url: str) -> object | None:
    """One bounded request. Any failure at all answers ``None``.

    Bounded twice: a timeout, so a slow CDN cannot hold up an install, and a read
    ceiling, so a wrong URL cannot stream a DVD image into memory. ``read(N + 1)``
    rather than ``read(N)`` because a response exactly at the limit and one over it
    have to be distinguishable — silently truncating would hand malformed JSON to the
    parser and look like a malformed feed.
    """
    import urllib.error
    import urllib.request

    from .. import __version__

    try:
        request = urllib.request.Request(url, headers={"User-Agent": f"{cli_name()}/{__version__}"})
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310 - our own URL
            body = response.read(_MAX_BYTES + 1)
        if len(body) > _MAX_BYTES:
            get_logger().debug("release feed at %s is larger than %d bytes; ignoring it", url, _MAX_BYTES)
            return None
        return json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - every failure here is the same failure
        get_logger().debug("could not read the release feed at %s: %s", url, exc)
        return None
