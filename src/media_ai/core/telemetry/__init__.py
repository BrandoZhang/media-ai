"""Observability: structured logs, OpenTelemetry traces and metrics, key events.

The whole surface a call site needs::

    from ..core import telemetry

    with telemetry.span("provider.generate_image", binding=rb.id) as sp:
        result = adapter.generate_image(req)
        sp.set(artifacts=len(result.artifacts))
    telemetry.event(telemetry.ARTIFACT_WRITTEN, binding=rb.id, count=len(result.artifacts))
    telemetry.count("media_ai.artifacts", len(result.artifacts), binding=rb.id)

Every one of those is a no-op when telemetry is off, which is the default (see
:mod:`.settings`), and none of them can raise: the contract this package holds with the
rest of the CLI is that **observing a command is never the reason it failed**, and that
**stdout stays exactly one JSON object** — every sink here is stderr or a socket.

``docs/OBSERVABILITY.md`` is the design; the four modules under it split along the
question each answers: :mod:`.settings` (is it on, and where does it go),
:mod:`.runtime` (the SDK, or the absence of one), :mod:`.spans` and :mod:`.metrics`
(the two signals), :mod:`.events` (the facts worth naming in all three at once), and
:mod:`.session` (one invocation, flushed).
"""

from __future__ import annotations

from .events import (
    ARTIFACT_WRITTEN,
    BINDING_RESOLVED,
    CLI_FINISH,
    CLI_START,
    DEGRADED,
    ERROR_RAISED,
    EVENTS,
    JOB_POLLED,
    JOB_SUBMITTED,
    PROVIDER_CALL,
    REQUEST_VALIDATED,
    USAGE_RECORDED,
    event,
)
from .metrics import COUNTERS, HISTOGRAMS, count, observe
from .runtime import active, boot, degrade, shutdown
from .session import Invocation, invocation
from .settings import Exporter, TelemetrySettings, settings
from .spans import Span, current_ids, current_span_event, error_attributes, span

__all__ = [
    "ARTIFACT_WRITTEN", "BINDING_RESOLVED", "CLI_FINISH", "CLI_START", "COUNTERS", "DEGRADED",
    "ERROR_RAISED", "EVENTS", "Exporter", "HISTOGRAMS", "Invocation", "JOB_POLLED", "JOB_SUBMITTED",
    "PROVIDER_CALL", "REQUEST_VALIDATED", "Span", "TelemetrySettings", "USAGE_RECORDED",
    "active", "boot", "count", "current_ids", "current_span_event", "degrade",
    "error_attributes", "event", "invocation", "observe", "settings", "shutdown", "span",
]
