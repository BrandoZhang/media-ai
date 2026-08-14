"""Bringing an older ``config.toml`` up to the shape this build reads.

The registry is empty, and that is the point of it existing now: it is checked by
``tests/test_migrations.py``, which asserts that every schema below :data:`SCHEMA` is
either convertible or *deliberately* not. The day somebody bumps the number, that test
fails until they have decided which — rather than the decision being made implicitly by
whoever first hits the error in the wild.

A migration is a pure function over the parsed document. Pure because it runs on every
read of an older file (see :func:`media_ai.core.config.load_config`), so it must be
cheap, repeatable, and free of anything that would make reading a config a side effect.

``lossless`` is the one flag, and it decides where the result may land. A lossless
migration is a rename or a default: the old document and the new one say the same thing,
so it can be applied in memory and the file left alone until something writes it
anyway. A lossy one needs a decision nobody can make on the user's behalf — "the
implicit credential chain is gone, pick a source for this binding" — and refuses,
pointing at the interactive path. Applying that one silently would be guessing with
somebody's credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["Migration", "UNMIGRATABLE", "migration", "plan", "registered"]


@dataclass(frozen=True)
class Migration:
    """One step from ``frm`` to ``frm + 1``."""

    frm: int
    to: int
    lossless: bool
    apply: Callable[[dict], dict]
    summary: str


#: Schemas this build knows about and will not convert, with the reason. Membership is a
#: decision, not an omission: ``tests/test_migrations.py`` refuses a gap that is neither
#: a registered migration nor an entry here.
#:
#: 1 is the pre-binding layout — ``[profiles]`` / ``[providers.x]``. Nothing in it maps
#: onto a binding: it named one model per modality per provider, and the thing a binding
#: is (an endpoint plus a credential plus a scene set) has no counterpart to read from.
#: A conversion would be inventing entries, which is worse than the honest refusal
#: ``_reject_v1`` already gives.
UNMIGRATABLE: dict[int, str] = {
    1: "the pre-binding layout has no entry a binding could be built from",
}

_REGISTRY: dict[int, Migration] = {}


def migration(*, frm: int, to: int, lossless: bool, summary: str):
    """Register one step. ``to`` must be ``frm + 1``.

    One step at a time, so a chain is composed rather than written: a direct 2→5 beside
    a 2→3 is two answers to the same question, and the one that runs depends on lookup
    order.
    """
    if to != frm + 1:
        raise ValueError(f"a migration goes one schema at a time; got {frm} -> {to}")
    if frm in _REGISTRY:
        raise ValueError(f"schema {frm} already has a migration")

    def register(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        _REGISTRY[frm] = Migration(frm=frm, to=to, lossless=lossless, apply=fn, summary=summary)
        return fn

    return register


def registered() -> dict[int, Migration]:
    """The registry, for the test that keeps it complete."""
    return dict(_REGISTRY)


def plan(frm: int, to: int) -> list[Migration] | None:
    """The steps from ``frm`` to ``to``, or ``None`` if the chain is broken.

    ``None`` rather than a partial list: half a conversion is a document in a shape no
    version of this tool has ever read.
    """
    steps = []
    at = frm
    while at < to:
        step = _REGISTRY.get(at)
        if step is None:
            return None
        steps.append(step)
        at = step.to
    return steps
