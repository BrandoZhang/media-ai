#!/usr/bin/env python3
"""The Compatibility section of a release's notes, from the numbers that actually moved.

    python scripts/compat_notes.py             # this tree vs the highest released tag
    python scripts/compat_notes.py v0.2.0      # …vs a specific one

Five separately-versioned things ship in one wheel, and only the first is semver:

    the release              0.6.0        what a user upgrades
    config.toml              schema = 2   what a user's machine holds
    credentials.toml         schema = 1   likewise, and secret
    the result JSON          schema_version = 2   what a program parses
    the release feed         schema = 1   what an older build can still read

A release note generated from commit subjects reports the first and says nothing about
the rest — which is backwards, because the other four are the ones that can break
something a user already has. "Adds an animation flag" is interesting; "your config
file is now read by a build that will convert it, and your other machine's older build
will refuse it" is the thing they needed to know before upgrading.

So this reads the constants out of the previous release's tree, compares them with
this one's, and writes only what moved. **Silence when nothing moved** — a section
that appears every time saying "nothing changed" is read once and skipped forever,
which costs exactly the attention the one that matters needs.

It is deliberately not clever. It does not diff the code, guess at severity, or
classify a change as breaking; it reports which numbers moved and what each one means
for a file already on someone's disk. Everything it prints is checkable against two
git trees.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Same reason as check_version.py: the version *shape* and the ordering come from the
# package, so this ranks tags by the rule the CLI uses rather than a second copy of it.
sys.path.insert(0, str(ROOT / "src"))
from media_ai.core.versioning import VERSION, precedence  # noqa: E402


@dataclass(frozen=True)
class Watched:
    """One number, where it lives, and what a change to it costs a user."""

    label: str
    path: str
    pattern: str
    consequence: str


#: Everything with a version of its own. A thing belongs here when it can outlive the
#: wheel that wrote it — a file on disk, a document another build reads, a payload a
#: program parses. Anything whose only reader is the same build does not need a number
#: and does not belong in a compatibility note.
WATCHED = (
    Watched(
        label="Result JSON (`schema_version`)",
        path="src/media_ai/core/result.py",
        pattern=r"^SCHEMA_VERSION = (\d+)$",
        consequence=(
            "an existing field changed meaning — re-check anything parsing stdout. "
            "Added fields never bump this"
        ),
    ),
    Watched(
        label="`config.toml` (`schema`)",
        path="src/media_ai/core/config.py",
        pattern=r"^SCHEMA = (\d+)$",
        consequence=(
            "this build reads the older file; an older build refuses the file this one "
            "writes. Upgrade every machine sharing a config"
        ),
    ),
    Watched(
        label="`credentials.toml` (`schema`)",
        path="src/media_ai/credentials/stores.py",
        pattern=r"^SCHEMA = (\d+)$",
        consequence="same one-way door as the config, on the file holding the keys",
    ),
    Watched(
        label="Release feed (`schema`)",
        path="src/media_ai/core/update.py",
        pattern=r"^FEED_SCHEMA = (\d+)$",
        consequence=(
            "builds reading an older feed schema ignore the published document whole, "
            "so they stop learning about updates rather than misreading one"
        ),
    ),
)


@dataclass(frozen=True)
class Change:
    label: str
    before: str
    after: str
    consequence: str


#: What a number that did not exist before means. Deliberately not the ``Watched``
#: consequence, which describes two builds disagreeing about a document: before the
#: number existed there was nothing to disagree with, and asserting how an older build
#: reads a key it has never heard of would be a claim about code nobody checked.
INTRODUCED = "first published in this release; no earlier build wrote this number"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def at(ref: str, path: str) -> str | None:
    """A file's contents at ``ref``, or ``None`` if it was not there.

    Absent is a real answer, not an error: every one of these constants was added at
    some point, and a release that introduces one is exactly a release worth a note.
    """
    done = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def value(text: str | None, pattern: str) -> str | None:
    if text is None:
        return None
    found = re.search(pattern, text, re.M)
    return found.group(1) if found else None


def previous_tag() -> str | None:
    """The highest released tag by semver precedence, not by date.

    By precedence because tags are created by a workflow and a re-run, a hotfix or a
    hand-made tag can land out of order; "the newest thing that exists" is a question
    about versions, and dates only usually agree with it.
    """
    tags = [t.strip() for t in git("tag", "--list", "v*").splitlines() if t.strip()]
    versions = [t for t in tags if VERSION.match(t[1:])]
    if not versions:
        return None
    return max(versions, key=lambda t: precedence(t[1:]))


def changes(base: str) -> list[Change]:
    out = []
    for w in WATCHED:
        before = value(at(base, w.path), w.pattern)
        after = value((ROOT / w.path).read_text(encoding="utf-8"), w.pattern)
        if after is None or before == after:
            # `after is None` means the constant is gone, or moved to a file this table
            # does not name. That is a change to this script's own assumptions rather
            # than a fact about the release, and inventing a row for it would put a
            # guess in a document whose whole value is that it holds none.
            continue
        out.append(Change(w.label, before or "—", after, w.consequence if before else INTRODUCED))
    return out


def floor_change(base: str) -> tuple[str | None, str | None]:
    """``min_supported`` in the published feed, before and after.

    The heaviest thing a release can carry: below the floor, every command that would
    reach a provider refuses. It lives in the feed rather than in the build because the
    build that has to be *told* is the one already installed.
    """
    def read(text: str | None) -> str | None:
        if not text:
            return None
        try:
            return (json.loads(text) or {}).get("min_supported")
        except json.JSONDecodeError:
            return None

    path = "release-feed.json"
    here = (ROOT / path)
    return read(at(base, path)), read(here.read_text(encoding="utf-8") if here.is_file() else None)


def render(base: str, rows: list[Change], floor: tuple[str | None, str | None]) -> str:
    before_floor, after_floor = floor
    if not rows and before_floor == after_floor:
        return ""

    out = ["## Compatibility", "", f"What moved since {base}, beyond the code itself.", ""]
    if rows:
        out += ["| what | before | now | what it means |", "|---|---|---|---|"]
        out += [f"| {r.label} | `{r.before}` | `{r.after}` | {r.consequence} |" for r in rows]
        out += [""]
    if after_floor and after_floor != before_floor:
        out += [
            f"**Minimum supported version is now `{after_floor}`.** Builds below it refuse "
            "any command that would reach a provider, and say so with `version_unsupported`. "
            "Everything local — `doctor`, `version`, `upgrade`, `uninstall` — keeps working.",
            "",
        ]
    elif before_floor and not after_floor:
        out += [f"**The minimum supported version (`{before_floor}`) has been lifted.**", ""]
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", nargs="?", default=None,
                    help="the tag to compare against (default: the highest released one)")
    args = ap.parse_args()

    base = args.base or previous_tag()
    if base is None:
        # A first release has nothing to be compatible with, and saying so on stderr
        # keeps stdout to "the section, or nothing" — which is what the caller redirects.
        print("no released tag to compare against; no compatibility section", file=sys.stderr)
        return 0
    sys.stdout.write(render(base, changes(base), floor_change(base)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
