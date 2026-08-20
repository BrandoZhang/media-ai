"""Reading an environment variable that means yes or no.

``bool(os.getenv("CI"))`` is the obvious way and it is wrong in the one case anybody
writes deliberately. ``CI=false`` is a real thing people set — it is how you tell a
tool "I know this looks like a runner, it is not" — and every non-empty string is
truthy, so the flag that says *off* reads as *on*. The mistake is invisible: the
variable is honoured, just backwards, and only by the person who went out of their way
to set it.

So a flag is read in three states, not two:

===========  =========================================
``None``     the variable says nothing (unset or blank)
``False``    ``0`` ``false`` ``no`` ``off``
``True``     anything else non-empty
===========  =========================================

The third state is the load-bearing one. "Unset" and "set to off" are different answers
wherever something *else* gets to decide in the absence of one — which is exactly the
shape of an override. ``MEDIA_AI_NO_TTY`` unset lets ``CI`` and ``TERM`` decide;
``MEDIA_AI_NO_TTY=0`` overrules them. Collapse the two and the variable can only ever
force one direction, which is why there was no way to say "yes, ``CI`` is set, there is
still somebody here".

**``NO_COLOR`` is deliberately not read through this.** Its spec
(https://no-color.org) says to honour it "when present and not an empty string,
*regardless of its value*", so ``NO_COLOR=0`` disables colour. A tidying pass that
routed it through here would break a cross-tool contract in order to be consistent
with a convention this file invented.
"""

from __future__ import annotations

import os

__all__ = ["env_flag"]

#: Spellings that mean *off*. Everything else non-empty means *on*, so a runner setting
#: ``CI=woodpecker`` still reads as a runner.
_FALSE = frozenset({"0", "false", "no", "off"})


def env_flag(name: str) -> bool | None:
    """``True``, ``False``, or ``None`` when ``name`` says nothing.

    Blank counts as saying nothing: ``CI=""`` is what unsetting looks like in a shell
    that cannot unset, and reading it as *on* would be the same defect one step along.
    """
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return None
    return raw not in _FALSE
