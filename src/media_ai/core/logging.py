"""Structured logging to **stderr**, with redaction applied to every record.

stdout is reserved for the one-line JSON contract; all human/diagnostic logging goes to
stderr and is passed through :func:`~media_ai.credentials.redaction.redact` so a secret
can never appear even if some adapter interpolates one into a message.

Two renderings of the same records, chosen by ``--log-format`` / ``MEDIA_AI_LOG_FORMAT``::

    binding resolved: binding=mock/mock scene=image.text_to_image

    {"ts":"2026-08-14T10:31:02.418Z","level":"debug","logger":"media_ai",
     "msg":"binding resolved: …","command":"image.generate","trace_id":"4bf92f…"}

A *rendering*, not a second set of call sites: the same ``logger.debug(...)`` produces
both, so nothing has to be logged twice to be machine-readable and the choice of format
changes nothing about what is logged — or about stdout, which is one JSON object either
way. That is the same line :mod:`media_ai.cli._prompt` holds about colour: presentation
may vary with who is reading, the payload may not.

Two things enrich a record without any call site passing them:

``set_context`` — facts about the *invocation* (which command is running), set once by
    ``cli.common.run`` and true of every line after it.
``set_trace_context`` — a callback installed by :mod:`media_ai.core.telemetry` that
    returns the current trace and span ids, so a line is joinable to the span it
    happened inside. A callback rather than an import because the dependency only runs
    in one direction: telemetry knows about logging, logging knows nothing about
    telemetry, and with the SDK absent the fields are simply not there.
"""

from __future__ import annotations

import datetime as _datetime
import json
import logging
import os
import sys
from collections.abc import Callable

from ..credentials.redaction import redact
from . import envvars

_LOGGER_NAME = "media_ai"

#: Rendering names accepted by ``--log-format`` and ``MEDIA_AI_LOG_FORMAT``.
FORMATS = ("text", "json")

#: Facts true of the whole invocation, attached to every record. Replaced wholesale
#: rather than merged: a process runs one command, and a leftover key from a previous
#: one would be a lie in the only field a reader trusts to say what was running.
_CONTEXT: dict = {}

#: Installed by telemetry; returns ``{"trace_id": …, "span_id": …}`` or ``{}``.
_TRACE_CONTEXT: Callable[[], dict] | None = None

#: Record attributes that belong to :mod:`logging` itself, so everything else a caller
#: attached through ``extra=`` can be found without an allow-list to keep in step.
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime", "taskName"}


def set_context(**fields) -> None:
    """Replace the per-invocation fields attached to every record. No args clears them."""
    _CONTEXT.clear()
    _CONTEXT.update({k: v for k, v in fields.items() if v is not None})


def set_trace_context(source: Callable[[], dict] | None) -> None:
    """Install (or remove) the callback that supplies trace correlation ids."""
    global _TRACE_CONTEXT
    _TRACE_CONTEXT = source


def _extras(record: logging.LogRecord) -> dict:
    """Everything a call site attached, from ``extra={"fields": {...}}`` or loose keys."""
    fields = dict(getattr(record, "fields", {}) or {})
    fields.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED and k != "fields"})
    return {k: v for k, v in fields.items() if v is not None}


def _correlation() -> dict:
    if _TRACE_CONTEXT is None:
        return {}
    try:
        return _TRACE_CONTEXT() or {}
    except Exception:  # noqa: BLE001 - a log line is never worth failing over
        return {}


class _RedactingFormatter(logging.Formatter):
    """Human rendering: the message, then whatever fields came with it."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = {**_CONTEXT, **_extras(record)}
        if fields:
            line = f"{line} " + " ".join(f"{k}={v}" for k, v in fields.items())
        return redact(line)


class _JsonFormatter(logging.Formatter):
    """One JSON object per record, on stderr.

    Redaction wraps the *finished* line rather than each value, so a secret cannot
    escape through a key nobody thought to mask — the same reason the text formatter
    redacts last.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": _datetime.datetime.fromtimestamp(record.created, _datetime.UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            **_CONTEXT,
            **_correlation(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, ensure_ascii=False, default=str))


def get_logger(name: str | None = None) -> logging.Logger:
    """The package logger, or a named child of it (``get_logger("events")``).

    A child inherits the level, the handler and the redaction, while a log pipeline can
    still select one stream by name — which is what lets the event stream be filtered
    out of, or down to, on its own.
    """
    return logging.getLogger(f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME)


def configure(level: str | None = None, *, fmt: str | None = None) -> logging.Logger:
    """Configure the package logger. Idempotent (safe to call once per CLI run)."""
    logger = get_logger()
    logger.handlers.clear()
    lvl = (level or os.getenv(envvars.LOG_LEVEL) or "warning").upper()
    logger.setLevel(getattr(logging, lvl, logging.WARNING))
    handler = logging.StreamHandler(sys.stderr)
    chosen = (fmt or os.getenv(envvars.LOG_FORMAT) or FORMATS[0]).strip().lower()
    handler.setFormatter(_JsonFormatter() if chosen == "json" else _RedactingFormatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
