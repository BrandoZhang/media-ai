"""Reading the release feed: is there something newer than what is running here?

The published half is ``release-feed.json`` at the repo root (see
``docs/DEVELOPMENT.md``). This is the client, and it is built around one rule:

**A generation command never waits on the network to answer a question it was not
asked.** So there are two entry points and they are not interchangeable —
:func:`cached` reads a file and cannot block, :func:`refresh` may make one bounded
request and is called only where waiting is already acceptable (``init``, and later an
explicit check). Everything on the hot path takes the first one.

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
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..brand import cli_name
from .envflag import env_flag
from .logging import get_logger
from .versioning import VERSION, precedence

if TYPE_CHECKING:
    from .config import UpdateSettings

__all__ = [
    "FEED_URL",
    "cache_path",
    "cached",
    "is_newer",
    "latest_version",
    "notices_for",
    "parse_feed",
    "below_floor",
    "minimum_supported",
    "refresh",
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

#: The highest feed schema this build understands. A document above it is ignored
#: entirely; see the module docstring.
FEED_SCHEMA = 1

_TIMEOUT_SECONDS = 10.0
_MAX_BYTES = 256 * 1024
_TTL_SECONDS = 24 * 60 * 60


def cache_path() -> Path:
    """Beside ``config.toml``, so ``$MEDIA_CONFIG_FILE`` relocates the whole set.

    Derived from :func:`config_path`, not from :func:`~media_ai.brand.config_dir`: the
    two agree on a default install and diverge the moment anyone points the environment
    variable somewhere else, which is what every test does and what a scratch profile
    is for. Reading the brand directory here would write into the developer's real
    ``~/.config`` from inside the suite — the same trap the install receipt avoids by
    hanging off the same function.
    """
    from .config import config_path

    return config_path().parent / "update-cache.json"


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


def settings() -> UpdateSettings:
    """The effective settings, with the environment layered over the config."""
    from .config import UpdateSettings

    base = _configured()
    env_check = env_flag("MEDIA_UPDATE_CHECK")
    return UpdateSettings(
        check=base.check if env_check is None else env_check,
        feed=os.getenv("MEDIA_UPDATE_FEED") or base.feed,
    )


def settings_from() -> dict[str, str]:
    """Which layer each effective setting came from: ``env``, ``config`` or ``default``.

    Reported by ``version check`` so a machine that is quietly not checking can be
    asked *why* — the failure mode of a precedence chain is not that it is wrong, it is
    that nobody can see which rung won.
    """
    base = _configured()
    return {
        "check": "env" if env_flag("MEDIA_UPDATE_CHECK") is not None else ("config" if not base.check else "default"),
        "feed": "env" if os.getenv("MEDIA_UPDATE_FEED") else ("config" if base.feed else "default"),
    }


def feed_url() -> str:
    """The feed to read: the environment, then the config, then where this build ships.

    An internal distribution points every install at its own mirror once, at setup;
    ``$MEDIA_UPDATE_FEED`` covers the machine that needs one before any config exists
    (an air-gapped box, a test) and wins when both are set.
    """
    return settings().feed or FEED_URL


# ---------------------------------------------------------------- when to ask


def should_check(version: str) -> bool:
    """Whether this machine should make an unsolicited request at all.

    Turned off by ``[update] check = false`` or ``MEDIA_UPDATE_CHECK=0``; see
    :func:`settings`.

    Deliberately *not* :func:`media_ai.cli._prompt._nobody_is_watching`, which looks
    almost the same and answers a different question. That one asks "will a human
    answer a prompt", and ``MEDIA_NO_TTY`` therefore turns it off — but an agent
    harness with no terminal still wants to be told its CLI is out of date, since the
    agent is the party that can act on it. The overlap is only ``CI``, and coupling
    them would let a change to prompting quietly change network behaviour.

    A version that is not a clean release is skipped outright: an editable checkout or
    a development build has no meaningful "newer" to be told about, and telling someone
    working on the tool to go and install it is noise.
    """
    if not settings().check:
        return False
    if env_flag("CI"):
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
    try:
        raw = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return parse_feed(raw.get("feed")) if isinstance(raw, dict) else None


def cached_at() -> float | None:
    """When the cache was last written, as a unix timestamp, or ``None``."""
    try:
        raw = json.loads(cache_path().read_text(encoding="utf-8"))
        stamp = raw.get("checked_at")
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        return None
    return float(stamp) if isinstance(stamp, (int, float)) else None


def _store(feed: dict) -> None:
    """Write the cache, best-effort. An unwritable cache is not a failed command."""
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"checked_at": time.time(), "feed": feed}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        get_logger().debug("could not write %s: %s", path, exc)


# --------------------------------------------------------------- the network


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
    if not force:
        stamp = cached_at()
        if stamp is not None and (time.time() - stamp) < _TTL_SECONDS:
            return None
    raw = _fetch(feed_url())
    if raw is None:
        return None
    feed = parse_feed(raw)
    if feed is None:
        return None
    _store(feed)
    return feed


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
