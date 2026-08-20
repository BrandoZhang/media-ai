"""Every environment variable this project invented, declared once.

Two rules decide what belongs here, and the second one is why the file exists at all.

**The prefix is ``MEDIA_AI_``.** It used to be ``MEDIA_``, on the reasoning that the
variables name a *modality* rather than a brand and so should survive a white-label
rebuild (see :mod:`media_ai.brand`). The property was right and the prefix was the
wrong way to get it: ``MEDIA_CONFIG_FILE``, ``MEDIA_LOG_LEVEL`` and ``MEDIA_USAGE_LOG``
read like any application's, in a namespace shared with everything else on the machine
— ``MEDIA_ROOT`` and ``MEDIA_URL`` are Django settings people routinely wire from the
environment — and a collision here is not cosmetic, since ``MEDIA_CONFIG_FILE`` decides
which config the CLI reads *and writes*.

``MEDIA_AI_`` keeps the property and drops the collision: it derives from the **import
package** ``media_ai``, which is on the list of things a rebrand explicitly does not
rename (the resource root, the ``media_ai.bindings`` entry-point group). A build renamed
to ``foo`` still imports ``media_ai``, so its variables still spell ``MEDIA_AI_``. It
also ends a disagreement inside this repository: ``install/install.sh`` and
``packaging/build.sh`` have always used ``MEDIA_AI_REPO``, ``MEDIA_AI_HOME``,
``MEDIA_AI_BUNDLE_EXTRAS`` — and the installer's own comment calls that "not branded".
The CLI simply never followed, so anyone who learned one prefix guessed the other wrong.

**Only variables this project owns.** ``CI``, ``TERM`` and ``NO_COLOR`` are other
people's conventions and are read where they are used; so are ``OTEL_*`` (a
specification) and every provider key (``ARK_API_KEY``, ``GEMINI_API_KEY``, …), which
users have already exported and which a manifest names by that spelling. Renaming any of
those would not be tidying, it would be breaking a contract with somebody else.

The names are constants rather than literals at each call site for one concrete reason:
:data:`RENAMED` is *derived* from them. A hand-written old-to-new table would be a
second copy of every name in the file, and the next rename would move one and not the
other — leaving a warning that names a variable nothing reads.
"""

from __future__ import annotations

import os

__all__ = [
    "ASCII",
    "CONFIG_FILE",
    "CREDENTIALS_FILE",
    "CRED_BROKER",
    "CRED_BROKER_TOKEN",
    "ESC_DELAY",
    "LIVE_TESTS",
    "LIVE_VIDEO",
    "LOG_FORMAT",
    "LOG_LEVEL",
    "NAMES",
    "NO_TTY",
    "PREFIX",
    "RENAMED",
    "TELEMETRY",
    "TELEMETRY_ENDPOINT",
    "TELEMETRY_EXPORTER",
    "TELEMETRY_TIMEOUT",
    "UPDATE_CHECK",
    "UPDATE_FEED",
    "UPDATE_INTERVAL",
    "USAGE_LOG",
    "legacy_in_use",
]

#: The one string this file is about. `install/install.sh` and `packaging/build.sh`
#: repeat it because they are shell and cannot import; `tests/test_envvars.py` holds
#: them together, the same arrangement `CLI_NAME` has.
PREFIX = "MEDIA_AI_"

# -- where the files are ---------------------------------------------------
CONFIG_FILE = f"{PREFIX}CONFIG_FILE"
CREDENTIALS_FILE = f"{PREFIX}CREDENTIALS_FILE"
USAGE_LOG = f"{PREFIX}USAGE_LOG"

# -- credentials -----------------------------------------------------------
CRED_BROKER = f"{PREFIX}CRED_BROKER"
CRED_BROKER_TOKEN = f"{PREFIX}CRED_BROKER_TOKEN"

# -- the release feed ------------------------------------------------------
UPDATE_CHECK = f"{PREFIX}UPDATE_CHECK"
UPDATE_FEED = f"{PREFIX}UPDATE_FEED"
UPDATE_INTERVAL = f"{PREFIX}UPDATE_INTERVAL"

# -- observability ---------------------------------------------------------
TELEMETRY = f"{PREFIX}TELEMETRY"
TELEMETRY_ENDPOINT = f"{PREFIX}TELEMETRY_ENDPOINT"
TELEMETRY_EXPORTER = f"{PREFIX}TELEMETRY_EXPORTER"
TELEMETRY_TIMEOUT = f"{PREFIX}TELEMETRY_TIMEOUT"
LOG_FORMAT = f"{PREFIX}LOG_FORMAT"
LOG_LEVEL = f"{PREFIX}LOG_LEVEL"

# -- the terminal ----------------------------------------------------------
NO_TTY = f"{PREFIX}NO_TTY"
ASCII = f"{PREFIX}ASCII"
ESC_DELAY = f"{PREFIX}ESC_DELAY"

# -- the test suite --------------------------------------------------------
LIVE_TESTS = f"{PREFIX}LIVE_TESTS"
LIVE_VIDEO = f"{PREFIX}LIVE_VIDEO"

#: Every name above. A test asserts this is exactly the set of module constants, so a
#: variable added without being listed here is caught rather than left out of `RENAMED`
#: and the `doctor` check — which is how a rename ends up half-done.
NAMES: frozenset[str] = frozenset({
    CONFIG_FILE, CREDENTIALS_FILE, USAGE_LOG,
    CRED_BROKER, CRED_BROKER_TOKEN,
    UPDATE_CHECK, UPDATE_FEED, UPDATE_INTERVAL,
    TELEMETRY, TELEMETRY_ENDPOINT, TELEMETRY_EXPORTER, TELEMETRY_TIMEOUT,
    LOG_FORMAT, LOG_LEVEL,
    NO_TTY, ASCII, ESC_DELAY,
    LIVE_TESTS, LIVE_VIDEO,
})

#: What each of these used to be called, derived rather than typed. Every one of them
#: was ``MEDIA_`` plus the same tail.
RENAMED: dict[str, str] = {f"MEDIA_{name[len(PREFIX):]}": name for name in sorted(NAMES)}


def legacy_in_use() -> dict[str, str]:
    """Old names that are set while their replacement is not: ``{old: new}``.

    **Nothing here reads the old value.** A compatibility layer is the historical
    baggage this rename exists to remove, and one that silently honours both spellings
    would have to be maintained forever by everyone who adds a variable.

    But a break that says nothing is a different thing from a break. Three of these fail
    *dangerously* when ignored rather than merely inconveniently: an unread
    ``MEDIA_CONFIG_FILE`` sends a script pointed at a scratch profile to the user's real
    configuration — and to their real ``credentials.toml`` — while an unread
    ``MEDIA_TELEMETRY=0`` turns telemetry back on and starts shipping prompts to a
    collector, which is the exact failure the telemetry design is arranged to prevent.
    Every one of those looks, from the outside, like the tool having decided something on
    its own.

    Only when the new name is *unset*: somebody mid-migration who has set both has
    already answered, and a warning then would be nagging about a variable that is being
    correctly ignored.

    This is a diagnostic with an expiry date. Delete the table, this function, and the
    two callers a release or two after the rename ships.
    """
    return {
        old: new for old, new in RENAMED.items()
        if os.environ.get(old, "").strip() and not os.environ.get(new, "").strip()
    }
