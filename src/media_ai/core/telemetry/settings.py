"""What telemetry is on, where it goes, and who gets to decide.

Two sources, and the environment wins. ``[telemetry]`` in ``config.toml`` is the
machine's standing answer — parsed and type-checked in :mod:`media_ai.core.config`
beside ``[update]``, because it is a config table like any other — and
``MEDIA_AI_TELEMETRY`` and friends are this invocation's, which is the shape every other
override here takes (see :mod:`media_ai.core.envflag`).

**Off unless somebody said otherwise.** A CLI that exports on first run is a CLI that
ships the caller's prompts to a collector nobody declared, so there is no
"auto-detect a local collector" path and no default-on: the only way telemetry starts
is a config that says so or a variable that says so.

``enabled`` is read three-state for the reason :mod:`media_ai.core.envflag` explains at
length — ``MEDIA_AI_TELEMETRY=0`` has to be able to overrule a config that says ``true``,
which is exactly the shared-config-on-a-machine-that-must-not-export case. A two-state
read could only ever force it *on*.

**There is no header or token setting**, deliberately. A collector behind
authentication is configured through OTel's own ``OTEL_EXPORTER_OTLP_HEADERS``, which
the exporter reads for itself. ``config.toml`` is the *shareable* file — it refuses a
raw provider key outright — and a bearer token for an observability backend is the same
kind of secret wearing a different hat.
"""

from __future__ import annotations

import os

from ...brand import cli_name
from .. import envvars
from ..config import DEFAULT_TELEMETRY_ENDPOINT, Exporter, TelemetrySettings
from ..envflag import env_flag

__all__ = ["DEFAULT_TELEMETRY_ENDPOINT", "Exporter", "TelemetrySettings", "settings"]


def settings() -> TelemetrySettings:
    """Read the configuration and the environment. Never raises.

    A broken ``config.toml`` is a real error and the command reports it — but it is
    reported by the code that needed the config, not by telemetry deciding whether to
    trace. Swallowing it here means the diagnosis arrives from ``bind()`` with the file
    and the field named, rather than as a mystery from the observability layer.

    An unusable *value* is treated differently on each side. In the config it is a
    parse error like any other hand-edited field, raised by
    :func:`media_ai.core.config.load_config` before this is reached. In the environment
    it is ignored with the configured value kept, because a stray
    ``MEDIA_AI_TELEMETRY_EXPORTER`` in a shell profile must not make every command in that
    shell fail.
    """
    cfg = _configured()
    enabled = env_flag(envvars.TELEMETRY)
    return TelemetrySettings(
        enabled=cfg.enabled if enabled is None else enabled,
        exporter=_exporter(os.getenv(envvars.TELEMETRY_EXPORTER)) or cfg.exporter,
        endpoint=(
            os.getenv(envvars.TELEMETRY_ENDPOINT)
            or cfg.endpoint
            # OTel's own variable, last: a collector already declared for the rest of a
            # deployment is the right endpoint for this too, and making an operator
            # repeat it under a second name is only a way to get it wrong once.
            or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            or DEFAULT_TELEMETRY_ENDPOINT
        ).rstrip("/"),
        service=cfg.service or os.getenv("OTEL_SERVICE_NAME") or cli_name(),
        timeout=_int(os.getenv(envvars.TELEMETRY_TIMEOUT), cfg.timeout),
        sample_percent=cfg.sample_percent,
        logs=cfg.logs,
    )


def _configured() -> TelemetrySettings:
    try:
        from ..config import load_config

        return load_config().telemetry
    except Exception:  # noqa: BLE001 - see the docstring on settings()
        return TelemetrySettings()


def _exporter(raw: str | None) -> Exporter | None:
    if not raw:
        return None
    try:
        return Exporter(raw.strip().lower())
    except ValueError:
        return None


def _int(raw: str | None, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default
