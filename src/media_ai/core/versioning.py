"""Comparing version numbers, in one place.

Two things live here: the shape of a version this project publishes, and the order
semver puts two of them in. Both were written out four times — once in
``scripts/bump_version.py``, once in ``scripts/check_version.py``, once in
``tests/test_version.py``, each carrying a comment saying it was kept in step with
the others by hand.

They move into the package rather than into ``scripts/`` because comparing versions
stops being release plumbing the moment the CLI itself has to ask whether a published
version is newer than the one running. Two comparators would agree on ``0.2.0`` and
``0.3.0`` and disagree on exactly the cases §11 exists for — ``0.9.0`` against
``0.10.0``, ``1.0.0-rc.2`` against ``1.0.0-rc.10``, a pre-release against the release
it leads to — so the second one is only ever discovered by a release that sorted
wrong.

Nothing here reads a file, runs git, or imports anything outside the standard library.
That is load-bearing rather than incidental: ``scripts/check_version.py`` runs in CI's
first step and in the release workflow's planning step, both under a bare interpreter
with nothing installed yet, and it reaches this module by putting ``src`` on the path.
``tests/test_version.py`` pins the property so the release cannot be the thing that
discovers a new import.
"""

from __future__ import annotations

import re

__all__ = ["VERSION", "precedence"]

#: The subset of semver this project publishes: ``MAJOR.MINOR.PATCH`` with an optional
#: pre-release suffix. Deliberately strict — the installer resolves releases by tag
#: name, so a version that cannot be a tag is a broken release.
VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?$")

_PARTS = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-.](.+))?$")


def precedence(version: str) -> tuple:
    """A sort key ordering versions the way semver §11 says to.

    Two rules do the work that a plain tuple of numbers cannot. A version *with* a
    pre-release sorts below the same version without one, so ``0.3.0-rc1`` is a step
    towards ``0.3.0`` rather than past it; and within a pre-release, a numeric
    identifier compares numerically and sorts below an alphanumeric one, so ``rc.9``
    precedes ``rc.10`` rather than following it in string order. The "more identifiers
    wins ties" rule falls out of tuple comparison, a shorter tuple being a prefix of
    the longer one.

    Raises :class:`ValueError` on anything that is not a version. Callers decide what
    that means: the release scripts exit, and a runtime reading a published feed
    ignores the entry rather than dying over a field it only wanted for a hint.
    """
    parsed = _PARTS.match(version)
    if not parsed:
        raise ValueError(f"{version!r} is not MAJOR.MINOR.PATCH — pass 0.3.0, not v0.3.0")
    major, minor, patch, pre = parsed.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), 1, ())
    ids = tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in pre.split("."))
    return (int(major), int(minor), int(patch), 0, ids)
