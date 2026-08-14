"""Bringing an older ``config.toml`` or ``credentials.toml`` up to the shape this build reads.

Both registries are empty, and that is the point of them existing now: they are checked
by ``tests/test_migrations.py``, which asserts that every schema below each file's
current one is either convertible or *deliberately* not. The day somebody bumps either
number, that test fails until they have decided which — rather than the decision being
made implicitly by whoever first hits the error in the wild.

One mechanism, two documents, because the question is identical and the *stakes* are
not. A config can be re-derived by re-running setup, so refusing to convert one costs
an afternoon. ``credentials.toml`` holds keys pasted in from somewhere else and
possibly issued once, which is why that file's own docstring promises an existing file
will be **convertible rather than replaced** — a promise with nowhere to land until
there was a registry to land in.

A migration is a pure function over the parsed document. Pure because it runs on every
read of an older file (:func:`media_ai.core.config.load_config`,
:func:`media_ai.credentials.stores.named_account`), so it must be cheap, repeatable,
and free of anything that would make reading a file a side effect.

``lossless`` is the one flag, and it decides where the result may land. A lossless
migration is a rename or a default: the old document and the new one say the same thing,
so it can be applied in memory and the file left alone until something writes it
anyway. A lossy one needs a decision nobody can make on the user's behalf — "the
implicit credential chain is gone, pick a source for this binding" — and refuses,
pointing at ``config migrate``. Applying that one silently would be guessing with
somebody's credentials.

Where that path stops
---------------------

``config migrate`` **reports**; it does not **ask**. A lossy step runs and its
``summary`` says what changed — enough for "the ``profiles`` table was dropped", not
enough for "you had three credential sources here and one had to win". A migration
that needs the user to *choose* between alternatives is not expressible by this
command as it stands, and bending one to fit would produce exactly the silent guess
:attr:`Migration.lossless` exists to prevent.

That is not a gap to close in advance. The first migration of that kind will know
which decision it has to surface, and it should arrive with the interaction that
surfaces it — not be squeezed into a shape designed before anyone knew.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["CONFIG", "CREDENTIALS", "DOCUMENTS", "Migration", "UNMIGRATABLE", "migration", "plan", "registered"]

#: The two files with a schema of their own. Named rather than passed as bare strings
#: so a typo is an ``AttributeError`` here instead of a registry nobody ever reads.
CONFIG = "config.toml"
CREDENTIALS = "credentials.toml"

DOCUMENTS = (CONFIG, CREDENTIALS)


@dataclass(frozen=True)
class Migration:
    """One step from ``frm`` to ``frm + 1``, for one document."""

    document: str
    frm: int
    to: int
    lossless: bool
    apply: Callable[[dict], dict]
    summary: str


#: Schemas this build knows about and will not convert, with the reason. Membership is a
#: decision, not an omission: ``tests/test_migrations.py`` refuses a gap that is neither
#: a registered migration nor an entry here.
#:
#: Config 1 is the pre-binding layout — ``[profiles]`` / ``[providers.x]``. Nothing in
#: it maps onto a binding: it named one model per modality per provider, and the thing a
#: binding is (an endpoint plus a credential plus a scene set) has no counterpart to read
#: from. A conversion would be inventing entries, which is worse than the honest refusal
#: ``_reject_v1`` already gives.
#:
#: Credentials has no entry, and should acquire one only under protest: the keys in that
#: file are the one thing here a user cannot reconstruct, so "start over" is not an
#: answer available to it the way it is to a config.
UNMIGRATABLE: dict[str, dict[int, str]] = {
    CONFIG: {1: "the pre-binding layout has no entry a binding could be built from"},
    CREDENTIALS: {},
}

_REGISTRY: dict[str, dict[int, Migration]] = {doc: {} for doc in DOCUMENTS}


def migration(*, document: str, frm: int, to: int, lossless: bool, summary: str):
    """Register one step. ``to`` must be ``frm + 1``.

    One step at a time, so a chain is composed rather than written: a direct 2→5 beside
    a 2→3 is two answers to the same question, and the one that runs depends on lookup
    order.
    """
    if document not in _REGISTRY:
        raise ValueError(f"unknown document {document!r}; expected one of {DOCUMENTS}")
    if to != frm + 1:
        raise ValueError(f"a migration goes one schema at a time; got {frm} -> {to}")
    if frm in _REGISTRY[document]:
        raise ValueError(f"{document} schema {frm} already has a migration")

    def register(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        _REGISTRY[document][frm] = Migration(
            document=document, frm=frm, to=to, lossless=lossless, apply=fn, summary=summary
        )
        return fn

    return register


def registered(document: str) -> dict[int, Migration]:
    """One document's registry, for the test that keeps it complete."""
    return dict(_REGISTRY[document])


def plan(document: str, frm: int, to: int) -> list[Migration] | None:
    """The steps from ``frm`` to ``to``, or ``None`` if the chain is broken.

    ``None`` rather than a partial list: half a conversion is a document in a shape no
    version of this tool has ever read.
    """
    steps = []
    at = frm
    while at < to:
        step = _REGISTRY[document].get(at)
        if step is None:
            return None
        steps.append(step)
        at = step.to
    return steps
