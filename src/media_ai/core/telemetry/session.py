"""One invocation, from the first argument parsed to the last span flushed.

``cli.common.run`` wraps every command in :func:`invocation`, which is the only place
telemetry is started and the only place it is shut down. That matters more than it
sounds: the exporters batch, a CLI process is over in a second or two, and the spans
worth having are the ones from the call that just failed. A flush that lives anywhere
but a ``finally`` around the whole command is a flush that the interesting runs skip.

The root span is named for the command (``cli.image.generate``), which is the one
piece of the argv that is bounded — the prompt, the paths and the ids are not, and a
span name is a series name in every backend that has ever read one.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from .. import logging as _logging
from . import metrics, runtime
from .events import CLI_FINISH, CLI_START, ERROR_RAISED, event
from .spans import error_attributes, span

__all__ = ["Invocation", "invocation"]


class Invocation:
    """The running command, and what is known about how it ended."""

    __slots__ = ("command", "error", "exit_code", "_span")

    def __init__(self, command: str, current) -> None:
        self.command = command
        self.error: BaseException | None = None
        self.exit_code: int | None = None
        self._span = current

    def failed(self, exc: BaseException) -> None:
        """Record the failure this command is about to report on stdout.

        Called with the :class:`~media_ai.core.errors.MediaError` the CLI actually
        emits, not with whatever was raised underneath it: the category and the exit
        code are what a dashboard groups by, and those belong to the error the caller
        sees.
        """
        self.error = exc
        self._span.record_error(exc)
        event(ERROR_RAISED, str(exc)[:200], command=self.command, **error_attributes(exc))

    def finish(self, exit_code: int) -> int:
        """Record the exit code and hand it straight back, so a call site reads
        ``return inv.finish(emit_result(...))``."""
        self.exit_code = exit_code
        return exit_code

    @property
    def outcome(self) -> str:
        return "error" if self.error is not None else "ok"


@contextmanager
def invocation(command: str) -> Iterator[Invocation]:
    """Boot telemetry, open the root span, and flush on the way out. Never raises."""
    runtime.boot()
    _logging.set_context(command=command)
    started = time.monotonic()
    try:
        with span(f"cli.{command}", command=command) as current:
            inv = Invocation(command, current)
            event(CLI_START, command=command)
            try:
                yield inv
            except BaseException as exc:  # noqa: BLE001 - re-raised; `run` owns the reporting
                # A command that raises past `run`'s own handlers (a SystemExit from
                # argparse, an interrupt during the emit) still gets an outcome, or the
                # duration histogram would quietly only ever describe the calls that
                # ended tidily.
                if inv.error is None:
                    inv.error = exc
                raise
            finally:
                _finish(inv, started)
    finally:
        runtime.shutdown()


def _finish(inv: Invocation, started: float) -> None:
    elapsed_ms = (time.monotonic() - started) * 1000
    labels = {"command": inv.command, "outcome": inv.outcome}
    inv._span.set(exit_code=inv.exit_code, outcome=inv.outcome)
    event(CLI_FINISH, command=inv.command, outcome=inv.outcome, exit_code=inv.exit_code,
          duration_ms=round(elapsed_ms, 1))
    metrics.count("media_ai.cli.invocations", **labels,
                  exit_code=inv.exit_code,
                  **{"error.category": getattr(getattr(inv.error, "category", None), "value", None)})
    metrics.observe("media_ai.cli.duration", elapsed_ms, **labels)
    _logging.set_context()
