"""Announcements shown at the top of ``<cli> init``.

Setup is the one moment a user is definitely reading the terminal, so it is where a
"before you build on this" warning has to go — a line in the README is read by the
people who least need it.

Today this is a constant. It is a *function* returning a list because the intent is
for a future version to fetch current notices (a deprecation, a provider outage, a
security advisory) from a published file. Three rules that a fetched source will have
to keep, and that shape this interface now:

- **Setup must never block on the network.** A wizard that hangs because a CDN is slow
  is worse than one that shows a stale notice. Any fetch belongs in a background
  refresh with a cached file on disk, read synchronously here.
- **A failed fetch is silence, not an error.** Announcements are not load-bearing;
  nothing about them may fail an install.
- **They are display-only.** Text arriving from a remote source is rendered in a box
  and never parsed, executed, or allowed to change what the wizard does.
"""

from __future__ import annotations

from ..brand import cli_name

__all__ = ["announcements"]

def _pre_release() -> tuple[str, str]:
    return (
        "Heads up",
        f"{cli_name()} is under rapid development. Interfaces, flags, and the result "
        "schema can change between releases, and breaking changes are expected "
        "before 1.0 — pin a version and read the release notes before upgrading. "
        "Not recommended for production use yet.",
    )


def announcements() -> list[tuple[str, str]]:
    """``[(title, body), …]`` to show under the intro, most important first.

    The compiled-in warning first, then whatever the published feed has to say to a
    build of this version — a deprecation, an outage, an advisory. The feed half is
    read from the cache :mod:`media_ai.core.update` keeps, so this cannot block even
    when the wizard has just refreshed it, and shows nothing at all on a machine that
    has never reached the network.

    Rendered in a box and never parsed further: the three rules at the top of this
    module hold whatever the source is.
    """
    from .. import __version__
    from ..core import update

    remote = [(n["title"], n["body"]) for n in update.notices_for(update.cached(), __version__)]
    return [_pre_release(), *remote]
