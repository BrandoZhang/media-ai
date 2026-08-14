"""Things the caller should know that are not the answer to what it asked.

"Your installed skills are older than this CLI" is not a result and not an error. It
belongs to the *installation*, not the call, and the call it happens to arrive with
succeeded. But the party that can act on it is the agent driving this CLI, and an
agent reads stdout — so a line on stderr is a line into the void whenever the harness
captures stderr and shows the model only the JSON.

So notices ride in the envelope, as an additive ``notices[]`` alongside ``ok`` and
``schema_version``, present only when there is something to say::

    {"ok": true, "schema_version": 2, "artifacts": [...],
     "notices": [{"kind": "skills_stale", "severity": "warn",
                  "message": "…", "action": "media-ai init --skills-only"}]}

Four rules hold this to something a consumer can rely on:

- **``kind`` is a closed set** (:data:`KINDS`). It is the field a program branches on,
  so it has to be enumerable; the prose in ``message`` is for a human or a model to
  read, never to match on.
- **``action`` is a command that can be run verbatim**, built from
  :func:`media_ai.brand.cmd`, or absent. The skills tell agents a hint is usually
  runnable, and a notice that says "you should upgrade" without saying how is one more
  thing for the agent to guess at.
- **A notice never fails a command.** Sources are consulted behind a bare ``except``
  and a broken one contributes nothing. Nothing here is load-bearing enough to be
  worth an exit code.
- **The payload does not vary with the terminal.** A human line on a tty is a separate
  rendering of the same fact, not an alternative to it — see
  :mod:`media_ai.cli._prompt` for the same rule about colour. What varies is
  presentation; ``notices[]`` is either there or there is nothing to say.

Sources are registered rather than called, and consulted lazily on the way out, so a
notice reaches even the paths where the command never ran — an argparse failure prints
the error contract without executing anything, and "you passed a flag this build does
not have" is *exactly* the symptom of the skill drift the first source detects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

__all__ = ["KINDS", "Notice", "add", "clear", "pending", "register_source", "reset"]

#: Every ``kind`` that can appear. Adding one is a deliberate edit here, next to the
#: consumers that have to learn it — which is the point of a closed set. Entries are
#: added with the code that produces them, never in advance.
KINDS = frozenset({"skills_stale", "update_available", "binding_deprecated"})

_SEVERITIES = frozenset({"info", "warn"})


@dataclass(frozen=True)
class Notice:
    """One thing worth telling the caller, and what to do about it."""

    kind: str
    message: str
    action: str | None = None
    severity: str = "info"

    def __post_init__(self) -> None:
        # Checked at construction, not at emission: a typo'd kind should fail in the
        # test that produces it, not silently become a value no consumer can branch on.
        if self.kind not in KINDS:
            raise ValueError(f"unknown notice kind {self.kind!r}; add it to notices.KINDS")
        if self.severity not in _SEVERITIES:
            raise ValueError(f"unknown notice severity {self.severity!r}")

    def to_dict(self) -> dict:
        out = {"kind": self.kind, "severity": self.severity, "message": self.message}
        if self.action:
            out["action"] = self.action
        return out


_SOURCES: list[Callable[[], Iterable[Notice]]] = []
_ADDED: list[Notice] = []
_CACHE: tuple[dict, ...] | None = None


def register_source(fn: Callable[[], Iterable[Notice]]) -> Callable[[], Iterable[Notice]]:
    """Register something to ask on the way out. Returns ``fn``, so it reads as a decorator.

    Registration rather than a call at the top of every command: a source has to be
    consulted on paths where no command body runs at all.
    """
    _SOURCES.append(fn)
    reset()
    return fn


def add(notice: Notice) -> None:
    """Add a notice about *this call*, rather than about the installation.

    The registered sources answer standing questions — is this build current, are the
    skills current — and can be asked at any point because the answer does not depend
    on what was run. A binding being deprecated is not like that: it is only worth
    saying when the call actually resolved to that binding, and only the code that
    resolved it knows.

    Resets the collection, so an ``add`` after something has already asked is not
    silently dropped. A command emits one payload at the end, so the ordering that
    matters is only ever "everything, then render".
    """
    _ADDED.append(notice)
    reset()


def pending() -> tuple[dict, ...]:
    """Every notice for this process, as dicts. Computed once.

    Once, because a process emits one JSON object and may render it twice
    (``--metadata-out``), and because a source may touch the filesystem — asking twice
    would be both wasted and, if the answers differed, incoherent.
    """
    global _CACHE
    if _CACHE is None:
        out: list[Notice] = list(_ADDED)
        for fn in _SOURCES:
            try:
                out.extend(fn())
            except Exception:  # noqa: BLE001 - a notice is never worth failing a command over
                continue
        _CACHE = tuple(n.to_dict() for n in out)
    return _CACHE


def reset() -> None:
    """Forget what was collected. For tests, and for registration to take effect."""
    global _CACHE
    _CACHE = None


def clear() -> None:
    """Forget the added notices too. Tests only — a process emits one payload."""
    _ADDED.clear()
    reset()
