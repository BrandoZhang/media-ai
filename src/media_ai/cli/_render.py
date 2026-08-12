"""Rendering the packaged Agent Skills for the name this build is distributed under.

A skill's text is *executed*: an agent reads ``{{cli}} bindings list`` and runs it. So
the skills cannot hardcode a command name that a rebranded build (see
:mod:`media_ai.brand`) does not answer to — the failure would be a ``command not
found`` inside an agent's tool call, with the skill itself as the thing that lied.

Two tokens, expanded when a skill is copied to disk:

``{{cli}}``
    The executable name — ``{{cli}} image generate …``.
``{{skill}}``
    The Agent Skill directory prefix, for the cross-references between skills
    (``../{{skill}}shared/SKILL.md``) and for the ``needs:`` edges in frontmatter.
    Separate from ``{{cli}}`` because it is a *directory name* being built, and reading
    ``{{cli}}-shared`` at the call sites hid that the hyphen was load-bearing.

Substitution, rather than the packaged files simply carrying the default name and
being rewritten, is what makes the invariant checkable: ``tests/test_brand.py`` asserts
no packaged skill file contains the default name at all, so a new reference file that
spells out ``media-ai`` fails CI instead of shipping a command that only works for the
unrenamed build. The same reason ``tests/test_ffmpeg.py`` walks the syntax tree to keep
the spawn site singular — the invariant is what gets forgotten, so a test holds it.

The cost is that the packaged tree is a template, not a working skill: the symlink
shortcut in ``skills/README.md`` would put ``{{cli}}`` in front of an agent, so
development installs go through ``{{cli}} init`` (a sync, so re-running it is cheap)
instead. That is documented there.
"""

from __future__ import annotations

import re

from ..brand import cli_name, skill_prefix

__all__ = ["TOKEN_RE", "render", "unknown_tokens"]

#: Anything in the ``{{…}}`` shape, whether or not we know it. Used to *find* the
#: typos (``{{clii}}``), which is why it does not just match the known names.
TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")

#: Token name -> the function producing its value. Deliberately tiny: every token is
#: another thing a skill author has to know, and both of these earn it by being
#: unwritable as a literal.
_TOKENS = {
    "cli": cli_name,
    "skill": skill_prefix,
}


def render(text: str) -> str:
    """Expand the brand tokens in one packaged skill file.

    An unknown token is left **verbatim** rather than raising or blanking: this runs
    inside the apply phase of ``init``, where a half-written skill tree is the worst
    outcome, and a stray ``{{foo}}`` is visible in the installed file while an
    exception here would abort an install over a typo in prose. ``tests/test_brand.py``
    is what actually catches it, before release.
    """
    return TOKEN_RE.sub(lambda m: _TOKENS[m.group(1)]() if m.group(1) in _TOKENS else m.group(0), text)


def unknown_tokens(text: str) -> set[str]:
    """``{{…}}`` names :func:`render` would not expand. For the tests, and for `doctor`."""
    return {m.group(1) for m in TOKEN_RE.finditer(text)} - set(_TOKENS)
