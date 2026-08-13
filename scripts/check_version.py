#!/usr/bin/env python3
"""Refuse a version that is behind something already released.

    python scripts/check_version.py            # the version in the tree
    python scripts/check_version.py 0.3.0      # a version about to be bumped to

Releases follow ``media_ai.__version__``: merge a pull request that bumps it and the
release workflow tags that number. So a version that moves *backwards* — a bad merge,
a hand-edit, a bump written against a branch that was already stale — either collides
with a published tag or publishes a release that sorts below the one before it. Both
are only visible afterwards, which is why this runs on every pull request.

The rule is one comparison against the highest released tag:

- **greater** — a release; merging it tags that version.
- **equal**   — the ordinary state between releases; this commit ships nothing new.
- **less**    — rejected.

Ordering is semver precedence (semver.org §11), so 0.2.1 and 0.3.0 are both acceptable
successors to 0.2.0, and a pre-release sorts below the release it leads to
(0.3.0-rc1 < 0.3.0). The comparison itself lives in ``media_ai.core.versioning``.

A backport is deliberately not expressible: releases are cut from the default branch
only, so "older than the newest tag" is always a mistake here rather than a branch.

The one thing it cannot see is a branch that bumps to a version some *other* branch
already released while this one was open — that lands on "equal", so it merges without
releasing rather than being rejected. Telling that apart from an ordinary unbumped
branch would mean diffing against the base, and the outcome is the mild one: the change
ships in the next release instead of its own.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "src" / "media_ai" / "__init__.py"

# The version *shape* and the semver ordering come from the package, so the rule this
# enforces is the same one the CLI applies at runtime rather than a second copy that
# agrees on the easy cases. Reached by path because this runs before anything is
# installed — see media_ai/core/versioning.py, which imports nothing but `re`.
sys.path.insert(0, str(ROOT / "src"))
from media_ai.core.versioning import VERSION, precedence  # noqa: E402


def declared_version() -> str:
    """``__version__`` as written in the tree.

    Read rather than imported: this runs before anything is installed, both in CI's
    first step and in the release workflow's planning step.
    """
    match = re.search(r'^__version__ = "(.*)"$', INIT.read_text(encoding="utf-8"), re.M)
    if not match:
        raise SystemExit(f"{INIT}: no __version__ line — has the declaration moved?")
    return match.group(1)


def released_tags() -> list[str]:
    """Every ``v*`` tag this checkout knows about."""
    done = subprocess.run(["git", "tag", "--list", "v*"], cwd=ROOT, capture_output=True, text=True, check=True)
    return done.stdout.split()


def highest(tags: Iterable[str]) -> str | None:
    """The greatest release among ``tags``, without its ``v``. ``None`` if there is none."""
    versions = [tag[1:] for tag in tags if tag.startswith("v") and VERSION.match(tag[1:])]
    return max(versions, key=precedence, default=None)


def check(version: str, tags: Iterable[str]) -> str:
    """Compare ``version`` against the released ``tags``.

    Returns the line to print. Raises ``SystemExit`` — the failure the workflows read —
    if the version is behind a release.
    """
    if not VERSION.match(version):
        raise SystemExit(f"{version!r} is not MAJOR.MINOR.PATCH — pass 0.3.0, not v0.3.0")
    top = highest(tags)
    if top is None:
        return f"{version} would be the first release; there is nothing to compare it against"
    if precedence(version) == precedence(top):
        return f"{version} is the current release (v{top}); this commit releases nothing new"
    if precedence(version) < precedence(top):
        raise SystemExit(
            f"{version} is behind the released v{top}, and releases follow this number, "
            f"so it can only go forwards.\n"
            f"Either leave __version__ at {top} — this change then ships in the next release — "
            f"or bump it past v{top}:\n"
            f"    python scripts/bump_version.py <version greater than {top}>"
        )
    return f"{version} is ahead of the latest release (v{top}); merging it will tag v{version}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", nargs="?", help="the version to check; default is the one in src/media_ai/__init__.py")
    args = ap.parse_args()

    tags = released_tags()
    if not tags:
        # Indistinguishable from a repo that has never released, so it cannot be an
        # error — but a checkout that skipped tags makes this check vacuous, and
        # silence is exactly how that would go unnoticed.
        print("no v* tags in this checkout; did it fetch them?", file=sys.stderr)
    print(check(args.version or declared_version(), tags))
    return 0


if __name__ == "__main__":
    sys.exit(main())
