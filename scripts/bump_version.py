#!/usr/bin/env python3
"""Set the release version everywhere it is written down.

    python scripts/bump_version.py 0.3.0

Two files, for the two different things a version *is* here:

- ``src/media_ai/__init__.py`` holds ``__version__``, the one declaration.
  ``pyproject.toml`` reads it (``dynamic = ["version"]``), so there is no third
  copy, and the CLI, ``doctor`` and the install receipt all report it.
- ``install/install.sh`` holds ``DEFAULT_VERSION``, the git *ref* the installer
  falls back to when the GitHub releases API is unreachable or rate-limited. It is
  a tag name (``v0.3.0``), not a package version, so it cannot be derived — and a
  stale pin quietly installs an old CLI for exactly the people whose network made
  them fall back to it.

``tests/test_version.py`` asserts the two agree, so a hand-edit that touches one
and not the other fails in CI rather than at the next release.

This lives in a file rather than inline in the release workflow on purpose. Inlined,
it has to be written as a heredoc inside a YAML block scalar inside a shell step —
three levels of quoting — and the version that *was* inlined broke the whole workflow
because an f-string's escaped brace produced a literal ``${{`` in the YAML, which
GitHub parses as one of its own expressions before the runner ever sees the file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "src" / "media_ai" / "__init__.py"
INSTALLER = ROOT / "install" / "install.sh"

# The one definition of what a release number looks like. Reached by path because this
# runs before anything is installed — see media_ai/core/versioning.py.
sys.path.insert(0, str(ROOT / "src"))
from media_ai.core.versioning import VERSION  # noqa: E402

_INIT_LINE = re.compile(r'^__version__ = ".*"$', re.M)
_INSTALLER_LINE = re.compile(r'^DEFAULT_VERSION="\$\{MEDIA_AI_DEFAULT_VERSION:-[^}]+\}"$', re.M)


def _replace(path: Path, pattern: re.Pattern[str], replacement: str) -> bool:
    """Rewrite the one line ``pattern`` matches. Returns whether the file changed.

    Substituting exactly once and checking the count is the point: a pattern that
    silently matches nothing would leave the release believing it had bumped a file
    it never touched.
    """
    before = path.read_text(encoding="utf-8")
    after, count = pattern.subn(replacement.replace("\\", "\\\\"), before, count=1)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one line matching {pattern.pattern!r}, found {count}")
    if after == before:
        return False
    path.write_text(after, encoding="utf-8")
    return True


def bump(version: str) -> list[Path]:
    """Write ``version`` to both files. Returns the ones that actually changed."""
    if not VERSION.match(version):
        raise SystemExit(f"{version!r} is not MAJOR.MINOR.PATCH — pass 0.3.0, not v0.3.0")
    changed = [
        path
        for path, pattern, line in (
            (INIT, _INIT_LINE, f'__version__ = "{version}"'),
            (INSTALLER, _INSTALLER_LINE, 'DEFAULT_VERSION="${MEDIA_AI_DEFAULT_VERSION:-v' + version + '}"'),
        )
        if _replace(path, pattern, line)
    ]
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", help="the new version, without the leading v (e.g. 0.3.0)")
    args = ap.parse_args()
    changed = bump(args.version)
    for path in changed:
        print(f"bumped {path.relative_to(ROOT)}")
    if not changed:
        print(f"already at {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
