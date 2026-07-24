"""Locate the packaged Agent Skills that teach an agent to drive the CLI.

The skills (one folder per CLI functionality, each with a ``SKILL.md``) live in
the repo-root ``skills/`` directory. An embedding agent framework (e.g.
uni-agent's creation agent) points its skill loader at the directory this helper
returns, so ``media-ai`` remains the single source of truth for *how to use the
CLI* and consumers never vendor a stale copy.

Resolution order (first hit wins):

1. ``$MEDIA_AI_SKILLS_DIR`` — an explicit override directory.
2. ``skills/`` bundled *inside* the installed package (reserved for a future
   wheel that ships the skills as package data).
3. A ``skills/`` directory in one of the package's ancestor directories — the
   repo-root sibling for editable / source installs. Walking a few levels up
   works for both the ``src/media_ai`` layout (repo is ``parents[2]``) and a
   flat ``media_ai`` layout, before any PyPI release.
"""

from __future__ import annotations

import os
from pathlib import Path

# The "read this first" skill; its presence marks a real skills directory.
_ANCHOR = Path("media-ai-shared") / "SKILL.md"
# How far up from the package dir to look for a repo-root ``skills/`` sibling.
_MAX_ASCENT = 4


def _looks_like_skills_dir(path: Path) -> bool:
    return path.is_dir() and (path / _ANCHOR).is_file()


def _candidate_dirs() -> list[Path]:
    pkg_dir = Path(__file__).resolve().parent  # .../[src/]media_ai
    # In-package (future wheel) first, then repo-root sibling for src/ or flat layouts.
    candidates = [pkg_dir / "skills"]
    candidates += [ancestor / "skills" for ancestor in pkg_dir.parents[:_MAX_ASCENT]]
    return candidates


def agent_skills_dir() -> Path:
    """Return the directory holding the packaged ``<skill>/SKILL.md`` folders.

    Raises ``FileNotFoundError`` if it cannot be located (so callers can treat a
    missing skills tree as an explicit, catchable condition rather than a silent
    empty path).
    """
    override = os.environ.get("MEDIA_AI_SKILLS_DIR")
    if override:
        path = Path(override).expanduser()
        if _looks_like_skills_dir(path):
            return path
        raise FileNotFoundError(
            f"MEDIA_AI_SKILLS_DIR={override!r} is not a media-ai skills directory "
            f"(expected a {_ANCHOR.as_posix()} inside it)"
        )

    candidates = _candidate_dirs()
    for candidate in candidates:
        if _looks_like_skills_dir(candidate):
            return candidate

    searched = ", ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        "media-ai agent skills directory not found; set MEDIA_AI_SKILLS_DIR to override "
        f"(looked in: {searched})"
    )


__all__ = ["agent_skills_dir"]
