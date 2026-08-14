"""The metric set, declared once.

Instruments are **declared** here and created lazily from the meter on first use, the
same way a binding manifest declares capabilities and the adapter supplies the wire: a
name that is not in this table is a typo, not a new metric, and
``tests/test_telemetry.py`` walks the source to prove no call site invents one.

Names live under ``media_ai.*`` — the **import package**, deliberately not the brand.
:mod:`media_ai.brand` says outright that the import package is what a white-label build
does *not* rename, and a dashboard that survives a rebrand is worth more than one whose
series names match the executable.

Metric attributes are bounded sets: a binding id, a scene, a provider, an outcome, an
error category. Never a path, a job id, a prompt or a URL — each distinct value is a
time series that never goes away, and the questions metrics answer ("how often does
this binding fail with auth?") are all asked of bounded fields. The unbounded ones
belong on spans, where they cost one trace each.
"""

from __future__ import annotations

from enum import Enum

from . import runtime

__all__ = ["COUNTERS", "HISTOGRAMS", "count", "observe"]

#: name -> (unit, description). ``1`` is OTel's unit for a dimensionless count.
COUNTERS: dict[str, tuple[str, str]] = {
    "media_ai.cli.invocations": ("1", "CLI invocations, by command and outcome"),
    "media_ai.events": ("1", "key events, by name"),
    "media_ai.provider.calls": ("1", "adapter operations, by binding and outcome"),
    "media_ai.http.requests": ("1", "HTTP requests to a provider, by status and outcome"),
    "media_ai.http.retries": ("1", "HTTP attempts retried, by status and reason"),
    "media_ai.artifacts": ("1", "files produced, by binding, scene and kind"),
    "media_ai.artifact.bytes": ("By", "bytes written to artifacts"),
    "media_ai.usage.tokens": ("1", "provider tokens billed, from the usage ledger"),
}

#: name -> (unit, description).
HISTOGRAMS: dict[str, tuple[str, str]] = {
    "media_ai.cli.duration": ("ms", "wall-clock time of a whole invocation"),
    "media_ai.provider.duration": ("ms", "wall-clock time of one adapter operation"),
    "media_ai.http.duration": ("ms", "wall-clock time of one HTTP request, retries included"),
    "media_ai.subprocess.duration": ("ms", "wall-clock time of one spawned encoder run"),
}


def count(name: str, value: int = 1, **attrs) -> None:
    """Add to a counter. No-op when nothing is recording; never raises."""
    _record(COUNTERS, name, "create_counter", value, attrs)


def observe(name: str, value: float, **attrs) -> None:
    """Record a histogram value. No-op when nothing is recording; never raises."""
    _record(HISTOGRAMS, name, "create_histogram", value, attrs)


def _record(table: dict, name: str, factory: str, value, attrs: dict) -> None:
    if name not in table:
        # A hard failure, like an unknown notice kind: an instrument nobody declared is
        # a series nobody can query, and the mistake belongs in the test that produced
        # it rather than in a dashboard six weeks later.
        raise KeyError(f"unknown instrument {name!r}; declare it in telemetry.metrics")
    rt = runtime.active()
    if rt is None or rt.meter is None:
        return
    try:
        instrument = rt.instruments.get(name)
        if instrument is None:
            unit, description = table[name]
            instrument = getattr(rt.meter, factory)(name, unit=unit, description=description)
            rt.instruments[name] = instrument
        labels = _labels(attrs)
        if factory == "create_counter":
            instrument.add(value, labels)
        else:
            instrument.record(value, labels)
    except Exception as exc:  # noqa: BLE001
        runtime.degrade(f"could not record {name}", exc)


def _labels(attrs: dict) -> dict:
    """Drop the absent ones and flatten enums, so ``scene=None`` is simply not a label.

    ``isinstance(value, Enum)`` rather than a duck-typed ``.value``, because this
    project's enums subclass ``str`` — a check that excluded strings would hand back
    ``Scene.IMAGE_TEXT_TO_IMAGE`` where ``image.text_to_image`` was meant, and the two
    render differently the moment anything calls ``str()`` on the label.
    """
    return {k: (v.value if isinstance(v, Enum) else v) for k, v in attrs.items() if v is not None}
