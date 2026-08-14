"""Key events — one fact, three signals.

An event lands in all three at once: a **span event** on whatever span is open, a
**structured log record** on stderr, and a tick of the ``media_ai.events`` **counter**
labelled with its name. That is what makes an event different from a log line: the same
occurrence is greppable in a terminal, joinable to a trace, and countable on a
dashboard, without three call sites that can disagree about whether it happened.

:data:`EVENTS` is a **closed set**, for the reason :data:`media_ai.core.notices.KINDS`
is: the name is the field a consumer branches on, so it has to be enumerable, and a
typo has to fail in the test that produced it rather than become a value nothing
matches. ``tests/test_telemetry.py`` walks the syntax tree of ``src/`` and fails on a
call site naming anything not in this table — which is what makes raising here safe on
an error path.

The level attached to each name is the level its log record gets. Most are ``DEBUG``:
the default log level is ``warning``, so an ordinary run says nothing new on stderr,
and ``--log-level debug`` turns the whole event stream on. The two exceptions are the
ones that describe something *going wrong* rather than something happening.
"""

from __future__ import annotations

import logging

from ..logging import get_logger
from . import metrics
from .spans import current_span_event

__all__ = ["EVENTS", "event"]

CLI_START = "cli.start"
CLI_FINISH = "cli.finish"
BINDING_RESOLVED = "binding.resolved"
REQUEST_VALIDATED = "request.validated"
PROVIDER_CALL = "provider.call"
JOB_SUBMITTED = "job.submitted"
JOB_POLLED = "job.polled"
ARTIFACT_WRITTEN = "artifact.written"
USAGE_RECORDED = "usage.recorded"
ERROR_RAISED = "error.raised"
DEGRADED = "telemetry.degraded"

#: name -> the level of the log record it writes.
EVENTS: dict[str, int] = {
    CLI_START: logging.DEBUG,
    CLI_FINISH: logging.DEBUG,
    BINDING_RESOLVED: logging.DEBUG,
    REQUEST_VALIDATED: logging.DEBUG,
    PROVIDER_CALL: logging.DEBUG,
    JOB_SUBMITTED: logging.INFO,
    JOB_POLLED: logging.DEBUG,
    ARTIFACT_WRITTEN: logging.DEBUG,
    USAGE_RECORDED: logging.DEBUG,
    # The command already reports its failure on stdout with a category and an exit
    # code; this is the same fact where a log pipeline can aggregate it.
    ERROR_RAISED: logging.WARNING,
    # Not a warning. Telemetry losing data is a fact about the observer, and shouting
    # about it on a run the user never asked to observe would be the observer becoming
    # the problem it exists to report.
    DEGRADED: logging.DEBUG,
}

#: The logger every event writes through — a child of the package logger, so it
#: inherits the level, the redaction and the JSON rendering, while a log pipeline can
#: still select the event stream alone by logger name.
_EVENT_LOGGER = "events"


def event(name: str, message: str | None = None, **fields) -> None:
    """Emit one key event. Raises only on an undeclared name.

    ``fields`` are the event's attributes: bounded, secret-free, and ``None``-valued
    entries are dropped rather than rendered. They reach the log record whole (the JSON
    formatter writes them as fields, the text formatter appends them as ``k=v``) and
    the span event redacted.
    """
    if name not in EVENTS:
        raise KeyError(f"unknown event {name!r}; declare it in telemetry.events.EVENTS")
    fields = {k: v for k, v in fields.items() if v is not None}
    get_logger(_EVENT_LOGGER).log(EVENTS[name], message or name, extra={"fields": {"event": name, **fields}})
    current_span_event(name, **fields)
    metrics.count("media_ai.events", event=name)
