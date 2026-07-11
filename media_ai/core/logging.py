"""Structured logging to **stderr**, with redaction applied to every record.

stdout is reserved for the one-line JSON contract; all human/diagnostic logging
goes to stderr and is passed through :func:`~media_ai.credentials.redaction.redact`
so a secret can never appear even if some adapter interpolates one into a message.
"""

from __future__ import annotations

import logging
import os
import sys

from ..credentials.redaction import redact

_LOGGER_NAME = "media_ai"


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def configure(level: str | None = None, *, json_lines: bool = False) -> logging.Logger:
    """Configure the package logger. Idempotent (safe to call once per CLI run)."""
    logger = get_logger()
    logger.handlers.clear()
    lvl = (level or os.getenv("MEDIA_LOG_LEVEL") or "warning").upper()
    logger.setLevel(getattr(logging, lvl, logging.WARNING))
    handler = logging.StreamHandler(sys.stderr)
    fmt = "%(message)s" if not json_lines else '{"level":"%(levelname)s","msg":"%(message)s"}'
    handler.setFormatter(_RedactingFormatter(fmt))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
