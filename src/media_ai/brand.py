"""The name this CLI is distributed under, in exactly one place.

Renaming the tool — for an internal rebrand, a white-label distribution, or to
install two builds side by side — means editing :data:`CLI_NAME` and rebuilding.
Everything a user or an agent ever sees the name in is derived from it here: the
executable, the command strings in ``error.hint``, the ``prog=`` in every parser,
the Agent Skill directory names, and the config directory.

**It is a build-time constant, not a runtime setting, and that is the whole point.**
The executable's name is fixed when the wheel is built (``[project.scripts]``), so a
knob in ``config.toml`` or an environment variable could only ever disagree with it:
the binary would be ``foo`` while every hint said ``media-ai``. This project promises
that ``error.hint`` is *usually runnable* and ships Agent Skills whose text an agent
executes verbatim — a name a user can set wrong turns both into ``command not found``,
which is worse than not offering the rename at all. So the name has one declaration,
the way ``__version__`` does, and the static files that must repeat it
(``pyproject.toml``, ``install/install.sh``) are pinned to it by ``tests/test_brand.py``
rather than trusted to stay in step.

Deriving it from ``sys.argv[0]`` was the other candidate and is rejected for the same
reason: skill text is rendered to disk once, at ``init``, while hints are rendered per
call, so a shell alias or a wrapper script would make the two disagree — two sources of
truth again, just less visible.

What is deliberately **not** derived from the brand:

``media_ai``, the import package
    It is the resource root (``files("media_ai") / "skills"``) and the entry-point
    group third-party manifests register under (``media_ai.bindings``). A distribution
    name differing from its import name is ordinary; renaming the import package would
    break every plugin for no user-visible gain.
``MEDIA_*``, the environment variables
    ``MEDIA_AI_CONFIG_FILE`` and friends name a *modality*, not a brand, and each is a
    per-invocation override rather than a namespace — what has to differ between two
    installs is the default path, which :func:`config_dir` already handles. Renaming
    them would break every caller's CI for no isolation gained.
The source repository
    ``install/install.sh`` still fetches ``BrandoZhang/media-ai``: where the code comes
    from is not what the tool is called.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["CLI_NAME", "cli_name", "cmd", "config_dir", "dist_name", "skill_name", "skill_prefix"]

#: The one declaration. A rebrand edits this line and nothing else in ``src/``.
#:
#: Must be usable as a filesystem name, a shell command and a PyPI distribution name
#: at once, since it becomes all three — lowercase, no spaces or path separators.
#: ``tests/test_brand.py`` enforces the shape and pins the files outside ``src/`` that
#: have to repeat it.
CLI_NAME = "media-ai"


def cli_name() -> str:
    """The executable name, as a user types it."""
    return CLI_NAME


def cmd(*parts: str) -> str:
    """A runnable command line for an ``error.hint`` — ``cmd("bindings", "available")``.

    Hints are contractual (``docs/ARCHITECTURE.md``: a hint is usually runnable), so
    they are built from the same constant the executable is named after rather than
    written out. Callers with interpolation to do can use an f-string on
    :func:`cli_name` instead; this exists so the common case has nothing to get wrong.
    """
    return " ".join((CLI_NAME, *parts))


def dist_name() -> str:
    """The distribution (wheel / ``uv tool install``) name.

    The same string as the executable: ``uv tool`` keys installed tools by
    distribution name, so two brands sharing one would replace rather than
    coexist — which is exactly what a rename is meant to prevent.
    """
    return CLI_NAME


def skill_prefix() -> str:
    """The leading segment of every installed Agent Skill directory.

    Skills go into a shared directory (``~/.claude/skills`` and friends) that is not
    ours, so the prefix is what makes a skill identifiably this tool's — it is both how
    :func:`media_ai.cli._discovery.group_of` recovers a skill's command group and the
    guard that stops ``uninstall`` from deleting a directory it did not write. Branding
    it is therefore also what lets two installs coexist there, and what scopes each
    one's ``uninstall`` to its own copies.
    """
    return f"{CLI_NAME}-"


def skill_name(group: str) -> str:
    """The installed directory name for one packaged skill (``image`` → ``media-ai-image``).

    The packaged tree stores skills under bare group names precisely so this mapping is
    the only place the brand enters — see :mod:`media_ai.cli._render`.
    """
    return f"{skill_prefix()}{group}"


def config_dir() -> Path:
    """The default directory for ``config.toml``, ``credentials.toml`` and the receipt.

    Unexpanded (``~/.config/<name>``); callers expand it. Branding this is what makes
    two installs genuinely independent rather than two front ends over one config —
    and a shared config would be worse than a collision, since it names bindings the
    other build may not ship at all.
    """
    return Path("~/.config") / CLI_NAME
