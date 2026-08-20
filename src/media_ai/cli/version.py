"""``<cli> version show|check`` — what is installed here, and is there something newer.

``<cli> --version`` already prints one line for a human, and that is left alone. This
group is the machine-readable half, for the same reason every other command has one:
the caller is usually an agent, and an agent that has to parse ``media-ai 0.6.0`` out
of prose will eventually parse it wrong.

The two operations are split by whether they may touch the network, which is the same
seam :mod:`media_ai.core.update` is built around:

- **``show`` is offline, always.** It reports what this build *is* — the release, and
  every schema number it reads or writes. Nothing about it depends on anything remote.
- **``check`` may fetch**, because asking it is asking for a fetch. ``--offline`` reads
  the cache instead, which is what a script in a pipeline wants: the same answer,
  without the latency or the dependency.

Neither ever exits non-zero for being out of date. The exit code is a failure
*category* (``core/errors.py``), and "there is a newer release" is not a failed
command — it is a finding, the way ``doctor``'s findings are. ``update_available`` is
the field to branch on.
"""

from __future__ import annotations

import argparse
import sys

from .. import __version__
from ..core import update
from ..core.config import SCHEMA as CONFIG_SCHEMA
from ..core.result import SCHEMA_VERSION
from ..credentials.stores import SCHEMA as CREDENTIALS_SCHEMA
from . import common
from ._install import detect

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    from ..brand import cli_name

    ap = argparse.ArgumentParser(
        prog=f"{cli_name()} version",
        description="Report this installation's version, and whether a newer one is published.",
    )
    sub = ap.add_subparsers(dest="op", required=True)
    common.add_global_args(sub.add_parser("show", help="what is installed here (offline)"))
    check = sub.add_parser("check", help="whether a newer release is published")
    common.add_toggle(
        check, "--offline", default=False,
        help="read the cached feed instead of fetching (never touches the network)",
    )
    common.add_global_args(check)
    return ap


def _schemas() -> dict:
    """Every version number this build reads or writes, in one place.

    Four separate numbers, deliberately: a release is semver and the rest are monotonic
    integers on documents that either parse or do not. Reporting them together is what
    makes "which of these moved?" answerable from a bug report instead of from a
    changelog — and `tests/test_version_cli.py` pins each to its constant, so this
    cannot drift into a hand-maintained list.
    """
    return {
        "result": SCHEMA_VERSION,
        "config": CONFIG_SCHEMA,
        "credentials": CREDENTIALS_SCHEMA,
        "feed": update.FEED_SCHEMA,
    }


def _show(args) -> dict:
    install = detect()
    return {
        "ok": True, "schema_version": SCHEMA_VERSION, "command": "version", "op": "show",
        "version": __version__,
        "python": sys.version.split()[0],
        "schemas": _schemas(),
        "install": {"method": install.method, "prefix": install.prefix},
    }


def _check(args) -> dict:
    """Compare this build against the published feed.

    ``source`` says where the answer came from — a fetch, the cache, or nothing at all —
    because "no newer version" and "could not find out" are different answers that would
    otherwise look identical, and the second one is the interesting one on a machine
    behind a proxy.
    """
    feed = None if args.offline else update.refresh(__version__, force=True)
    source = "network"
    if feed is None:
        feed, source = update.cached(), "cache"
    if feed is None:
        source = "none"

    latest = update.latest_version(feed)
    install = detect()
    out = {
        "ok": True, "schema_version": SCHEMA_VERSION, "command": "version", "op": "check",
        "current": __version__,
        "latest": latest,
        "update_available": update.is_newer(latest, __version__),
        "source": source,
        "checked_at": update.cached_at(),
        "settings": {
            "check": update.settings().check,
            "feed": update.feed_url(),
            # Reported for the same reason `check` is: the check now happens on its own,
            # so "why did this machine not notice for a week?" is a real question, and
            # the interval is half its answer. The other half is `settings_from`.
            "interval": update.interval_seconds(),
        },
        "settings_from": update.settings_from(),
        # Not `notices`: that key belongs to the envelope every command shares
        # (`cli/common._with_notices`), which would overwrite whatever were put here.
        # These are the feed's own announcements for this version, which are a different
        # thing from a notice about this installation.
        "feed_notices": update.notices_for(feed, __version__),
    }
    if out["update_available"]:
        out["upgrade_command"] = install.upgrade_command(update.SOURCE_REPO, latest)
    return out


def _do(args) -> dict:
    return {"show": _show, "check": _check}[args.op](args)


def main() -> int:
    args = common.parse_args(_build_parser())
    # No background refresh on the way out. This group is the one that already decides
    # when to fetch: `check` does it in the foreground because that is what it was
    # asked for, and `show` and `check --offline` promise not to. A detached child would
    # make `--offline` a request that goes to the network in a different process, which
    # is the distinction nobody using that flag is drawing.
    return common.run(_do, args, refresh_feed=False)


if __name__ == "__main__":
    raise SystemExit(main())
