"""Booting the OpenTelemetry SDK, and doing without it.

The SDK is an **optional extra** (``pip install "<dist>[otel]"``). This CLI depends on
Pillow and a bundled ffmpeg and nothing else; an install that never exports should not
carry a dependency tree several times its own size, and an install that cannot reach an
index should still generate images. So the import happens lazily, once, and only when
telemetry is enabled — with the whole package importable, and every facade call a
no-op, when it is not there.

Three states and one rule::

                  SDK present            SDK absent
    telemetry off no import, no-op       no import, no-op
    telemetry on  spans/metrics/logs     no-op + a telemetry_unavailable notice

The notice is the load-bearing half of the last cell. Asking for telemetry and getting
silence looks exactly like asking for telemetry and getting a collector that drops
everything, and the party who can fix it — an operator, or the agent reading stdout —
never sees stderr in most harnesses. So it rides in ``notices[]`` with the install
command as its ``action``, and nothing fails: a missing exporter is not a reason to
refuse a generation.

**The global providers are left alone.** ``trace.set_tracer_provider`` is a process-wide
one-shot; a second call warns and is ignored. This CLI is also importable as a library,
and a host that has configured its own tracing must not find it hijacked by a
generation call — so the providers are held here and tracers are taken from them
directly. Context propagation is unaffected: ``start_as_current_span`` writes to the
same context vars either way, which is what lets a log line carry the trace id of the
span it happened inside.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from ..logging import get_logger
from .settings import Exporter, TelemetrySettings, settings

__all__ = ["Runtime", "active", "boot", "degrade", "shutdown"]

#: Distribution extra that supplies the SDK. Named here rather than in the message so
#: the notice, the doctor check and the docs cannot drift.
EXTRA = "otel"


@dataclass
class Runtime:
    """The live SDK objects for one process, or the fact that there are none."""

    settings: TelemetrySettings
    tracer: object | None = None
    meter: object | None = None
    #: Lazily created metric instruments, keyed by name (see :mod:`.metrics`).
    instruments: dict = field(default_factory=dict)
    #: Providers to flush and close on the way out, in creation order.
    providers: list = field(default_factory=list)
    #: A logging handler to detach on shutdown, when log export is on.
    log_handler: object | None = None


_ACTIVE: Runtime | None = None
#: Set once telemetry has decided it cannot run, so a failing boot is attempted once
#: per process rather than on every span.
_REFUSED = False


def active() -> Runtime | None:
    """The live runtime, or ``None`` when nothing is recording.

    Read on every span and every metric, so it is one module-global lookup and no
    import: the disabled path — which is the default path — costs an attribute read.
    """
    return _ACTIVE


def boot(cfg: TelemetrySettings | None = None) -> Runtime | None:
    """Start the SDK if telemetry is on and the SDK is here. Idempotent, never raises."""
    global _ACTIVE, _REFUSED
    if _ACTIVE is not None or _REFUSED:
        return _ACTIVE
    cfg = cfg or settings()
    if not cfg.enabled:
        _REFUSED = True
        return None
    try:
        _ACTIVE = _build(cfg)
        # Imported here, not at module scope: `spans` imports this module, and a
        # top-level import back would close the loop. The callback is what puts
        # `trace_id` on a log line without logging knowing telemetry exists.
        from ..logging import set_trace_context
        from .spans import current_ids

        set_trace_context(current_ids)
    except ImportError as exc:
        _REFUSED = True
        _unavailable(exc)
        return None
    except Exception as exc:  # noqa: BLE001 - telemetry never fails a command
        _REFUSED = True
        degrade("could not start telemetry", exc)
        return None
    return _ACTIVE


def shutdown(timeout: float | None = None) -> None:
    """Flush everything recorded and release the SDK. Never raises.

    Called from ``cli.common.run``'s ``finally``, which is what keeps the last span of
    a failing command — the interesting one — from being the one that never left. The
    exporters batch, and a CLI process is gone in a second or two.

    The flush is **bounded, once, for the whole of it**. An OTLP exporter that cannot
    reach its collector retries on its own schedule well past thirty seconds; without a
    deadline a working generation ends in a terminal that appears to have hung, over
    telemetry nobody was waiting for.

    Measured rather than assumed, which is how the shape of this got decided. A budget
    spent *per provider* made a half-second command take nine and a half against a
    refused connection — traces waited it out, then metrics waited it out again. And a
    budget the SDK is asked to honour is not the same as one it does: ``force_flush``
    takes a timeout, ``TracerProvider.shutdown`` takes none, and both end up joining a
    worker thread that is mid-retry. So the deadline is enforced from outside, on a
    daemon thread this function abandons when the time is up. Abandoning is safe
    precisely here: the providers were built with ``shutdown_on_exit=False``, so nothing
    of the SDK's runs after us, and a daemon thread does not hold up the interpreter.
    The data is dropped, a debug line says so, and the exit code is still the one the
    command earned.
    """
    global _ACTIVE, _REFUSED
    rt, _ACTIVE, _REFUSED = _ACTIVE, None, False
    if rt is None:
        return
    from ..logging import set_trace_context

    # Before the flush, not after: the ids belong to spans that are ending here, and a
    # line logged during shutdown would otherwise carry a correlation to nothing.
    set_trace_context(None)
    budget = max(0.0, timeout if timeout is not None else rt.settings.timeout)
    if rt.log_handler is not None:
        try:
            get_logger().removeHandler(rt.log_handler)
        except Exception as exc:  # noqa: BLE001
            degrade("could not detach the telemetry log handler", exc)
    worker = threading.Thread(target=_drain, args=(rt, budget), name="telemetry-flush", daemon=True)
    worker.start()
    worker.join(budget)
    if worker.is_alive():
        degrade(f"flush did not finish within {budget:g}s; dropping what was not exported")


def _drain(rt: Runtime, budget: float) -> None:
    """Flush and close every provider, sharing one deadline between them."""
    deadline = time.monotonic() + budget
    for provider in rt.providers:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        for method, args in (("force_flush", (remaining_ms,)), ("shutdown", ())):
            try:
                getattr(provider, method)(*args)
            except TypeError:
                # `MeterProvider.shutdown` takes a timeout, `TracerProvider.shutdown`
                # does not, and which is which has moved between SDK releases. The
                # signature is not worth a version check: try it, then try it bare.
                try:
                    getattr(provider, method)()
                except Exception as exc:  # noqa: BLE001
                    degrade(f"telemetry {method} failed", exc)
            except Exception as exc:  # noqa: BLE001
                degrade(f"telemetry {method} failed", exc)


#: Re-entrancy guard for :func:`degrade`. Reporting a degradation goes back through
#: the span and metric facades, which are the things that just failed — so without
#: this, one broken exporter is an unbounded recursion instead of one debug line.
_DEGRADING = False


def degrade(what: str, exc: BaseException | None = None) -> None:
    """Record that telemetry lost something, without telling the caller twice.

    Every entry point in this package funnels its failures here: a debug log line and,
    when a span is open, a ``telemetry.degraded`` event on it. Nothing propagates. The
    contract this package has with the rest of the CLI is that observing a command can
    never be the reason it failed.
    """
    global _DEGRADING

    get_logger().debug("telemetry: %s%s", what, f": {exc}" if exc else "")
    if _DEGRADING:
        return
    _DEGRADING = True
    try:
        from .events import DEGRADED, event

        event(DEGRADED, reason=what, error=type(exc).__name__ if exc else None)
    except Exception:  # noqa: BLE001 - the fallback for a failure has no fallback
        pass
    finally:
        _DEGRADING = False


def _unavailable(exc: BaseException) -> None:
    """Say that telemetry was asked for and the SDK is not installed."""
    from .. import notices
    from ..packaging import extra_hint

    get_logger().debug("telemetry requested but the SDK is not installed: %s", exc)
    notices.add(notices.Notice(
        kind="telemetry_unavailable",
        severity="warn",
        message=f"telemetry is enabled but the OpenTelemetry SDK is not installed; nothing is being exported ({exc})",
        action=extra_hint(EXTRA),
    ))


# --------------------------------------------------------------------------
# the SDK half
# --------------------------------------------------------------------------


def _build(cfg: TelemetrySettings) -> Runtime:
    """Construct providers, exporters and a tracer/meter pair. Raises on a missing SDK."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from ... import __version__

    _quiet_the_sdk()
    resource = _resource(cfg)
    rt = Runtime(settings=cfg)

    # `shutdown_on_exit=False` on both providers: the SDK's own `atexit` hook would run
    # after `shutdown()` has already given up on an unreachable collector, and block the
    # interpreter's exit on the very retries the deadline exists to abandon. This
    # function owns the lifecycle; nothing of the SDK's may outlive it.
    tracer_provider = TracerProvider(resource=resource, sampler=_sampler(cfg), shutdown_on_exit=False)
    if span_exporter := _span_exporter(cfg):
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    rt.providers.append(tracer_provider)
    rt.tracer = tracer_provider.get_tracer(__package__, __version__)

    readers = []
    if metric_exporter := _metric_exporter(cfg):
        # A very long interval, and the flush at shutdown is what actually exports. A
        # CLI process is shorter than any sensible period, so a periodic reader ticking
        # during it would only ever be a thread that wakes up to find nothing new.
        readers.append(PeriodicExportingMetricReader(metric_exporter, export_interval_millis=600_000))
    meter_provider = MeterProvider(resource=resource, metric_readers=readers, shutdown_on_exit=False)
    rt.providers.append(meter_provider)
    rt.meter = meter_provider.get_meter(__package__, __version__)

    if cfg.logs and cfg.exports:
        _attach_logs(rt, resource)
    return rt


def _quiet_the_sdk() -> None:
    """Keep the SDK's own complaints off stderr unless the caller asked for debug.

    A collector that is down makes the OTLP exporter log a warning per retry and an
    error per abandoned batch — six lines, on every command, about something the caller
    did not ask to be told and cannot act on mid-run. That is the observer becoming the
    problem it exists to report, and it is worse than silence because it lands on the
    stderr a human is reading a real failure on.

    Not silenced, though — *deferred to the same switch as everything else here*. At
    ``--log-level debug`` the SDK's own lines come through at warning, which is exactly
    when "why is nothing arriving in my collector?" is the question being asked. The
    ``telemetry.degraded`` event says the same thing in this project's own vocabulary at
    every level.
    """
    import logging

    debugging = get_logger().isEnabledFor(logging.DEBUG)
    logging.getLogger("opentelemetry").setLevel(logging.WARNING if debugging else logging.CRITICAL)


def _resource(cfg: TelemetrySettings):
    from opentelemetry.sdk.resources import Resource

    from ... import __version__

    # Deliberately short. Everything else a backend wants about the host — hostname,
    # pid, container id — it either detects itself or is a fact about the caller's
    # machine that this CLI has no business volunteering.
    return Resource.create({"service.name": cfg.service, "service.version": __version__})


def _sampler(cfg: TelemetrySettings):
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased

    if cfg.sample_percent >= 100:
        return ParentBased(ALWAYS_ON)
    return ParentBased(TraceIdRatioBased(max(0, cfg.sample_percent) / 100))


def _span_exporter(cfg: TelemetrySettings):
    import sys

    if cfg.exporter is Exporter.NONE:
        return None
    if cfg.exporter is Exporter.CONSOLE:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        # `out=sys.stderr`, and this is the whole reason the console exporter is
        # constructed here rather than left to its default: OTel writes spans to
        # **stdout**, which carries exactly one JSON object per invocation. A span
        # document beside the result document breaks every consumer of this CLI.
        return ConsoleSpanExporter(out=sys.stderr)
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=f"{cfg.endpoint}/v1/traces", timeout=cfg.timeout)


def _metric_exporter(cfg: TelemetrySettings):
    import sys

    if cfg.exporter is Exporter.NONE:
        return None
    if cfg.exporter is Exporter.CONSOLE:
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter

        return ConsoleMetricExporter(out=sys.stderr)  # stderr, for the reason above
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    return OTLPMetricExporter(endpoint=f"{cfg.endpoint}/v1/metrics", timeout=cfg.timeout)


def _attach_logs(rt: Runtime, resource) -> None:
    """Export the same log records over OTLP, correlated by trace id.

    Guarded separately from traces and metrics because the logs SDK is the one signal
    still churning: its module is spelled with a leading underscore
    (``opentelemetry.sdk._logs``), its console exporter was renamed mid-1.x, and
    ``LoggingHandler`` is deprecated in favour of a package this project does not depend
    on. All three have been stable in practice for many releases — but a private path is
    a promise nobody made, and losing the log signal is not worth losing the other two
    over. When the handler does move, this function is the whole of what changes.
    """
    try:
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        exporter = _log_exporter(rt.settings)
        if exporter is None:
            return
        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        rt.providers.append(provider)
        handler = LoggingHandler(logger_provider=provider)
        # On the package logger, which every module here logs through and whose own
        # handler already writes the human rendering to stderr. Attaching a second
        # handler adds a destination; it does not replace one.
        get_logger().addHandler(handler)
        rt.log_handler = handler
    except Exception as exc:  # noqa: BLE001
        degrade("could not export logs", exc)


def _log_exporter(cfg: TelemetrySettings):
    import sys

    if cfg.exporter is Exporter.CONSOLE:
        # The class was renamed mid-1.x and the old name warns on construction. Newest
        # first, older second: this is the churn the whole signal is guarded for, and a
        # deprecation warning printed on every telemetry-enabled run would land on the
        # stderr `_quiet_the_sdk` exists to keep clear.
        from opentelemetry.sdk import _logs

        console = getattr(_logs.export, "ConsoleLogRecordExporter", None) or _logs.export.ConsoleLogExporter
        return console(out=sys.stderr)
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

    return OTLPLogExporter(endpoint=f"{cfg.endpoint}/v1/logs", timeout=cfg.timeout)
