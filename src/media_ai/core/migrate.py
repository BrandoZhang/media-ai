"""Reading a document written by a *different version* of media-ai.

Every file this project writes carries a ``schema`` number, and until now the answer
to an unfamiliar one was always the same: refuse, and say to re-run ``init``. That is
the right answer for a file *this install wrote* — an install is a fresh start, so
there is nothing to carry forward — and the wrong answer for a document whose entire
purpose is to cross between installs. A configuration bundle is written on one machine
and read on another, which sooner or later runs a different release; refusing it there
means a fleet cannot be provisioned until every box is on the same version.

So the seam lives here, once, rather than in each reader: a chain of single-step
upgrades from one schema to the next, applied in order until the document is at the
version this build reads. Three outcomes, each of which says which one it is:

- **current** — nothing runs.
- **older** — the steps in between are applied in order. A *missing* step is an error
  naming both versions, never a silent pass-through: a document half-understood is
  worse than one refused, because the fields a later version reused would be read with
  their old meaning.
- **newer** — refused, with "upgrade media-ai" as the fix. A build cannot know what a
  later version added, nor whether what it does recognise still means the same thing.

**The chain owns the version bump, not the step.** A step that forgot to write the new
number would leave the loop applying it forever; a step that wrote the wrong one would
skip its successor. Steps therefore only transform content.
"""

from __future__ import annotations

from collections.abc import Callable

from .errors import ErrorCategory, MediaError

__all__ = ["Migrations", "Step"]

#: One upgrade: takes the document at version *n*, returns it at *n + 1* — content
#: only. The version key is written by :meth:`Migrations.upgrade`.
Step = Callable[[dict], dict]


class Migrations:
    """The upgrade chain for one kind of versioned document.

    ``what`` names it in errors and in their ``code`` (``bundle_schema_newer``,
    ``config_schema_unsupported``), so a caller can branch on which document was
    refused without parsing prose.
    """

    def __init__(self, what: str, *, target: int, key: str = "schema") -> None:
        self.what = what
        self.target = target
        self.key = key
        self._steps: dict[int, Step] = {}

    def step(self, from_version: int) -> Callable[[Step], Step]:
        """Register the one upgrade from ``from_version`` to ``from_version + 1``.

        Refused at import time if it is out of range or already registered: a chain
        with a hole or a duplicate is a bug that would otherwise surface as a confusing
        refusal on somebody else's machine, months later.
        """
        if not 1 <= from_version < self.target:
            raise ValueError(f"{self.what} step {from_version} is outside 1..{self.target - 1}")
        if from_version in self._steps:
            raise ValueError(f"{self.what} already has a step from schema {from_version}")

        def register(fn: Step) -> Step:
            self._steps[from_version] = fn
            return fn

        return register

    def version_of(self, data: dict, *, source: str) -> int:
        """The document's declared version, or a refusal naming what was expected.

        ``bool`` is excluded explicitly because it is an ``int`` subclass in Python:
        ``schema = true`` would otherwise read as version 1 and be quietly accepted.
        """
        raw = data.get(self.key)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise MediaError(
                f"{source}: not a media-ai {self.what} — expected `{self.key} = <number>`, got {raw!r}",
                category=ErrorCategory.CLI, code=f"{self.what}_schema_missing",
            )
        return raw

    def upgrade(self, data: dict, *, source: str) -> dict:
        """``data`` at :attr:`target`, or a refusal. Never mutates the argument."""
        version = self.version_of(data, source=source)
        if version > self.target:
            raise MediaError(
                f"{source}: {self.what} schema {version} was written by a newer media-ai; "
                f"this build reads schema {self.target}",
                category=ErrorCategory.CLI, code=f"{self.what}_schema_newer",
                details={"found": version, "supported": self.target},
                hint="upgrade media-ai on this machine, then run the import again",
            )
        out = dict(data)
        while version < self.target:
            step = self._steps.get(version)
            if step is None:
                raise MediaError(
                    f"{source}: no upgrade from {self.what} schema {version} to {version + 1}",
                    category=ErrorCategory.CLI, code=f"{self.what}_schema_unsupported",
                    details={"found": version, "supported": self.target},
                    hint=f"re-export the {self.what} from a machine running this version of media-ai",
                )
            out = dict(step(out))
            version += 1
            out[self.key] = version
        return out
