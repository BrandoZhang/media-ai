"""Spans, and the attribute rules that keep them affordable.

One facade with two behaviours: with the SDK booted, :func:`span` opens a real span;
without it, the same ``with`` block yields a null object whose methods do nothing. Call
sites are written once and never ask which.

**Attributes are low cardinality, and never user content.** A prompt is unbounded text
belonging to the caller; it goes nowhere near a span, and ``prompt.chars`` — an integer
— goes instead. Ids that identify one call (a job id, a binding) are fine on a *span*,
where cardinality is affordable and a job id is the only way to join a submit to the
poll that finished it in a later process; they are kept off *metric* attributes, where
each distinct value is a series that never goes away.

Every string that does go on a span is passed through
:func:`~media_ai.credentials.redaction.redact` on the way in. Nothing here is sourced
from a credential, so this is belt-and-braces — which is the point: it is the same
masking the logs get, applied at the other sink, so a careless interpolation in some
future adapter cannot make an attribute the one place a key escapes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from traceback import format_exception
from typing import Any

from ...credentials.redaction import redact
from . import runtime

__all__ = ["Span", "current_ids", "current_span_event", "span"]


class Span:
    """A live span, or nothing at all, behind one interface."""

    __slots__ = ("_span",)

    def __init__(self, raw: Any = None) -> None:
        self._span = raw

    def set(self, **attrs) -> Span:
        """Attach attributes, dropping the ones that say nothing.

        ``None`` is skipped rather than written as ``"None"``: a missing attribute is
        readable as missing, while a string that spells the absence is a value a query
        has to know to exclude.
        """
        if self._span is None:
            return self
        try:
            for key, value in attrs.items():
                cleaned = _clean(value)
                if cleaned is not None:
                    self._span.set_attribute(key, cleaned)
        except Exception as exc:  # noqa: BLE001
            runtime.degrade("could not set span attributes", exc)
        return self

    def add_event(self, name: str, **attrs) -> None:
        if self._span is None:
            return
        try:
            self._span.add_event(name, {k: v for k, v in ((k, _clean(v)) for k, v in attrs.items()) if v is not None})
        except Exception as exc:  # noqa: BLE001
            runtime.degrade("could not add a span event", exc)

    def record_error(self, exc: BaseException) -> None:
        """Mark the span failed and label it with this project's error taxonomy.

        ``error.category`` is the same value the JSON carries and the same one the exit
        code is derived from (:mod:`media_ai.core.errors`), so an alert on
        ``error.category="auth"`` fires on the fact that exit code 4 reports. A second
        vocabulary for failure would be a second thing to keep in agreement.
        """
        if self._span is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode

            message = redact(str(exc))
            self._span.record_exception(
                exc,
                {
                    "exception.message": message,
                    "exception.stacktrace": redact("".join(format_exception(type(exc), exc, exc.__traceback__))),
                },
            )
            self._span.set_status(Status(StatusCode.ERROR, message[:200]))
        except Exception as err:  # noqa: BLE001
            runtime.degrade("could not record an exception on a span", err)
        self.set(**error_attributes(exc))

    @property
    def recording(self) -> bool:
        return self._span is not None


#: One shared instance for the disabled path, so a command that opens a dozen spans
#: with telemetry off allocates nothing.
_NULL = Span(None)


@contextmanager
def span(name: str, **attrs) -> Iterator[Span]:
    """Open a span named ``name``, or yield a no-op when nothing is recording.

    Failure to *create* a span yields the null object rather than raising: the block
    is the caller's actual work, and it runs whether or not it is being watched.
    """
    rt = runtime.active()
    tracer = getattr(rt, "tracer", None)
    if tracer is None:
        yield _NULL
        return
    try:
        cm = tracer.start_as_current_span(name)
    except Exception as exc:  # noqa: BLE001
        runtime.degrade(f"could not start span {name!r}", exc)
        yield _NULL
        return
    with cm as raw:
        current = Span(raw)
        current.set(**attrs)
        try:
            yield current
        except BaseException as exc:
            # The SDK already records the exception and sets the status; this adds the
            # category, which is the field anything downstream will group by.
            current.set(**error_attributes(exc))
            raise


def error_attributes(exc: BaseException) -> dict:
    """The span/metric labels for a failure, from this project's taxonomy."""
    category = getattr(getattr(exc, "category", None), "value", None)
    return {
        "error.type": type(exc).__name__,
        "error.category": category,
        "error.code": getattr(exc, "code", None),
        "error.retryable": getattr(exc, "retryable", None),
    }


def current_ids() -> dict:
    """``{"trace_id": …, "span_id": …}`` for the span in scope, or ``{}``.

    Used by the log formatter to join a line to the span it happened inside. Read from
    the context rather than from a runtime we hold, so it is correct even when the host
    process — not this CLI — is the one that started the trace.
    """
    if runtime.active() is None:
        return {}
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return {}
        return {"trace_id": format(context.trace_id, "032x"), "span_id": format(context.span_id, "016x")}
    except Exception:  # noqa: BLE001 - a log line is never worth failing over
        return {}


def current_span_event(name: str, **attrs) -> None:
    """Add an event to whatever span is in scope, without holding a reference to one.

    What :mod:`.events` needs: an event is emitted deep inside a call (an adapter, the
    HTTP client) that has no business knowing which span it is under, and the answer is
    in the context anyway.
    """
    if runtime.active() is None:
        return
    try:
        from opentelemetry import trace

        Span(trace.get_current_span()).add_event(name, **attrs)
    except Exception as exc:  # noqa: BLE001
        runtime.degrade("could not add an event to the current span", exc)


def _clean(value: Any) -> Any:
    """Coerce a value into something the SDK accepts, or ``None`` to drop it."""
    if value is None:
        return None
    if isinstance(value, (int, float)):  # bool included, and the SDK takes it as one
        return value
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, (list, tuple)):
        return [str(_clean(v)) for v in value if v is not None]
    return redact(value if isinstance(value, str) else str(value))
