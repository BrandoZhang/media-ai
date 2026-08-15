"""Observability: the three signals, and the four things that must never happen.

The invariants, in the order they would hurt:

1. **stdout stays exactly one JSON object.** Every exporter and formatter here writes
   to stderr or a socket. OTel's own console exporters default to *stdout*, so this is
   a live hazard rather than a theoretical one, and it is checked against a real SDK
   with the console exporters on.
2. **Telemetry never fails a command.** A missing SDK, a broken exporter, an
   unreachable collector: each degrades to a no-op, and the exit code is the one the
   command earned.
3. **An unreachable collector never hangs the CLI.** The flush has one budget for the
   whole of it, enforced from outside the SDK.
4. **Nothing leaks.** Secrets are masked in span attributes as they are in logs, and a
   URL loses its query string before it becomes one.

The rest is the ordinary contract: settings precedence, the closed event set, and the
spans and metrics a real command actually produces.
"""

from __future__ import annotations

import ast
import builtins
import importlib.util
import json
import logging
import sys
import threading
import time
from pathlib import Path

import pytest

from media_ai.core import notices, telemetry
from media_ai.core.config import Exporter, TelemetrySettings
from media_ai.core.errors import EXIT_CODES, ErrorCategory, MediaError
from media_ai.core.logging import configure, get_logger
from media_ai.core.telemetry import events as events_mod
from media_ai.core.telemetry import metrics as metrics_mod
from media_ai.core.telemetry import runtime as runtime_mod

SRC = Path(__file__).resolve().parents[1] / "src" / "media_ai"

#: Only the tests that need real spans are skipped without the extra. Skipping the whole
#: module would take the *degradation* contract with it — and a machine that does not
#: have the SDK is exactly the one that has to prove telemetry stays a no-op there.
try:
    # `find_spec` on a submodule imports the parent, and raises rather than returning
    # None when the parent is the missing one.
    HAVE_SDK = importlib.util.find_spec("opentelemetry.sdk") is not None
except ModuleNotFoundError:
    HAVE_SDK = False
needs_sdk = pytest.mark.skipif(not HAVE_SDK, reason="the otel extra is not installed")


# --------------------------------------------------------------------- helpers


class _CapturingMetricExporter:
    """A metric exporter that keeps the batches instead of sending them."""

    def __init__(self) -> None:
        from opentelemetry.sdk.metrics.export import AggregationTemporality

        self.batches: list = []
        self._preferred_temporality = {}
        self._preferred_aggregation = {}
        self._temporality = AggregationTemporality.CUMULATIVE

    # the MetricExporter protocol the PeriodicExportingMetricReader drives
    def _preferred_temporality_for(self, kind):  # pragma: no cover - defensive
        return self._temporality

    @property
    def _preferred_temporality_map(self):  # pragma: no cover - defensive
        return self._preferred_temporality

    def export(self, metrics_data, timeout_millis=10_000, **kwargs):
        from opentelemetry.sdk.metrics.export import MetricExportResult

        self.batches.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis=10_000):
        return True

    def shutdown(self, timeout_millis=30_000, **kwargs):
        return None

    def points(self, name: str) -> list:
        """The data points for one instrument, from the newest batch that has it.

        The newest and not all of them: the reader exports cumulatively and is drained
        twice on the way out (a flush, then a shutdown), so every batch carries the
        whole state and concatenating them would count each point once per export.
        """
        for batch in reversed(self.batches):
            for resource_metrics in batch.resource_metrics:
                for scope_metrics in resource_metrics.scope_metrics:
                    for metric in scope_metrics.metrics:
                        if metric.name == name:
                            return list(metric.data.data_points)
        return []


def _keep_open_span_exporter():
    """An in-memory exporter that survives being shut down.

    Every invocation shuts telemetry down for real — that is the flush, and it is the
    behaviour under test — while a test process runs several invocations through one
    exporter. Left as it is, ``InMemorySpanExporter`` marks itself stopped after the
    first and silently drops everything the later ones produce.

    Built in a function so the module imports on a machine without the extra.
    """
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    class KeepOpen(InMemorySpanExporter):
        def shutdown(self) -> None:
            return None

    return KeepOpen()


class Recorded:
    """What one telemetry-enabled run produced."""

    def __init__(self, spans, metrics: _CapturingMetricExporter) -> None:
        self._spans = spans
        self.metrics = metrics

    def spans(self):
        return list(self._spans.get_finished_spans())

    def span(self, name: str):
        return next((s for s in self.spans() if s.name == name), None)

    def names(self) -> list[str]:
        return [s.name for s in self.spans()]


@pytest.fixture(autouse=True)
def _no_notices_leak_out():
    """The added notices are a process-global list, and one test here adds one.

    Without this, ``telemetry_unavailable`` from the missing-SDK test turns up in the
    JSON of whatever runs next — which is the same test-order-dependent confusion the
    notice itself exists to prevent, one layer down.
    """
    notices.clear()
    try:
        yield
    finally:
        notices.clear()


@pytest.fixture
def recording(monkeypatch):
    """Telemetry on, exporting into memory. Yields a :class:`Recorded`.

    The exporter *factories* are patched rather than the runtime, so everything else —
    settings resolution, the providers, the sampler, the flush — is the real boot path.
    """
    if not HAVE_SDK:
        pytest.skip("the otel extra is not installed")
    spans, metrics = _keep_open_span_exporter(), _CapturingMetricExporter()
    monkeypatch.setenv("MEDIA_TELEMETRY", "1")
    monkeypatch.setattr(runtime_mod, "_span_exporter", lambda cfg: spans)
    monkeypatch.setattr(runtime_mod, "_metric_exporter", lambda cfg: metrics)
    # Log export is the one signal built on a private SDK module; it has its own test.
    monkeypatch.setattr(runtime_mod, "_attach_logs", lambda rt, resource: None)
    return Recorded(spans, metrics)


@pytest.fixture
def generate(configured, tmp_path):
    """Run a real ``image generate`` against the offline mock binding."""
    from media_ai.cli import image

    configured({"mock/mock": None}, defaults={"image.text_to_image": "mock/mock"})

    def run(*extra: str, prompt: str = "a red bicycle") -> int:
        argv = ["generate", "--prompt", prompt, "--output", str(tmp_path / "out.png"), *extra]
        old, sys.argv = sys.argv, ["media-ai image", *argv]
        try:
            return image.main()
        finally:
            sys.argv = old

    return run


# ------------------------------------------------- 1. stdout stays one object


@needs_sdk
def test_stdout_is_one_json_object_with_the_console_exporters_on(generate, capsys, monkeypatch):
    """The hazard this whole design is arranged around.

    ``ConsoleSpanExporter`` writes to **stdout** by default. One span document beside
    the result document and every consumer of this CLI breaks — including the Agent
    Skills, which tell an agent that stdout is exactly one object.
    """
    monkeypatch.setenv("MEDIA_TELEMETRY", "1")
    monkeypatch.setenv("MEDIA_TELEMETRY_EXPORTER", "console")
    assert generate() == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["ok"] is True  # parses whole: nothing else was printed
    assert '"name": "cli.image.generate"' in out.err  # …and the spans did go somewhere


@needs_sdk
def test_the_console_exporters_are_constructed_against_stderr():
    """Named directly, because the default is the wrong one and a default is silent.

    A future edit that drops ``out=`` would still pass every functional test in this
    file; it would fail here, next to the reason.
    """
    cfg = TelemetrySettings(enabled=True, exporter=Exporter.CONSOLE)
    assert runtime_mod._span_exporter(cfg).out is sys.stderr
    assert runtime_mod._metric_exporter(cfg).out is sys.stderr


def test_a_debug_run_writes_no_json_to_stdout(generate, capsys, monkeypatch):
    """The JSON *log* rendering is on stderr too — two JSON streams, one on each fd."""
    monkeypatch.setenv("MEDIA_TELEMETRY", "1")
    monkeypatch.setenv("MEDIA_TELEMETRY_EXPORTER", "none")
    assert generate("--log-level", "debug", "--log-format", "json") == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["ok"] is True
    events = [json.loads(line) for line in out.err.splitlines() if line.startswith('{"ts"')]
    assert {e.get("event") for e in events} >= {"cli.start", "binding.resolved", "cli.finish"}


# ------------------------------------------------- 2. telemetry never fails a command


def test_a_missing_sdk_degrades_to_a_notice_and_the_command_still_succeeds(generate, capsys, monkeypatch):
    """Enabled telemetry with no SDK is silence, and silence is what the notice is for.

    Indistinguishable, from the outside, from a collector that drops everything — and
    the party who can fix it reads stdout, not stderr.
    """
    monkeypatch.setenv("MEDIA_TELEMETRY", "1")
    real_import = builtins.__import__

    def no_otel(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError("No module named 'opentelemetry'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_otel)
    notices.clear()
    assert generate() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    found = [n for n in payload["notices"] if n["kind"] == "telemetry_unavailable"]
    assert found and found[0]["severity"] == "warn"
    assert runtime_mod.EXTRA in found[0]["action"]


def test_an_exporter_that_explodes_on_boot_does_not_fail_the_command(generate, capsys, monkeypatch):
    monkeypatch.setenv("MEDIA_TELEMETRY", "1")
    monkeypatch.setattr(runtime_mod, "_span_exporter", lambda cfg: 1 / 0)
    assert generate() == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert telemetry.active() is None


def test_a_metric_recorded_against_a_broken_meter_is_swallowed(recording, generate, capsys):
    assert generate() == 0  # boots and shuts down cleanly
    telemetry.count("media_ai.artifacts", 1, binding="mock/mock")  # after shutdown: no meter
    telemetry.observe("media_ai.cli.duration", 1.0, command="image.generate")
    capsys.readouterr()


def test_an_undeclared_instrument_is_a_programming_error():
    """The one thing here that *does* raise, for the reason ``notices.KINDS`` does."""
    with pytest.raises(KeyError):
        telemetry.count("media_ai.not.a.real.instrument", 1)
    with pytest.raises(KeyError):
        telemetry.event("not.a.real.event")


def test_degrade_does_not_recurse_when_the_span_layer_is_what_broke(monkeypatch, caplog):
    """Reporting a degradation goes back through spans and metrics — the things that
    just failed. Without the guard, one broken exporter is a stack overflow."""
    monkeypatch.setattr(telemetry, "count", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(events_mod.metrics, "count", lambda *a, **k: 1 / 0)
    with caplog.at_level(logging.DEBUG, logger="media_ai"):
        runtime_mod.degrade("something broke")
    assert any("something broke" in r.getMessage() for r in caplog.records)


# ------------------------------------------------- 3. the flush is bounded


def test_shutdown_gives_up_on_a_provider_that_will_not_flush(monkeypatch, caplog):
    """One budget for the whole shutdown, enforced from outside the SDK.

    Measured, not assumed: a per-provider budget made a half-second command take nine
    and a half seconds against a refused connection, because traces waited it out and
    then metrics waited it out again.
    """
    class _Stuck:
        def force_flush(self, timeout_millis=None):
            time.sleep(30)

        def shutdown(self):  # pragma: no cover - never reached
            time.sleep(30)

    rt = runtime_mod.Runtime(settings=TelemetrySettings(enabled=True, timeout=1))
    rt.providers = [_Stuck(), _Stuck()]
    monkeypatch.setattr(runtime_mod, "_ACTIVE", rt)
    started = time.monotonic()
    with caplog.at_level(logging.DEBUG, logger="media_ai"):
        telemetry.shutdown()
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"shutdown took {elapsed:.1f}s against a stuck exporter"
    assert any("did not finish" in r.getMessage() for r in caplog.records)
    assert telemetry.active() is None


def test_the_providers_do_not_register_their_own_exit_hooks(recording, generate):
    """``shutdown_on_exit=False``, or the SDK's ``atexit`` hook blocks the interpreter
    on exactly the retries the deadline just abandoned."""
    telemetry.boot()
    rt = telemetry.active()
    assert rt is not None
    assert all(getattr(p, "_atexit_handler", None) is None for p in rt.providers)


# ------------------------------------------------- 4. nothing leaks


def test_a_secret_in_a_span_attribute_is_masked(recording):
    from media_ai.credentials.redaction import register_secret

    register_secret("sk-not-a-real-key-000000")
    with telemetry.invocation("image.generate"):
        with telemetry.span("provider.generate_image") as sp:
            sp.set(note="called with sk-not-a-real-key-000000")
    span = recording.span("provider.generate_image")
    assert span is not None
    assert "sk-not-a-real-key-000000" not in span.attributes["note"]
    assert "***" in span.attributes["note"]


@needs_sdk
def test_a_secret_in_a_recorded_exception_is_masked(recording):
    """OTel's default exception event carries both the message and the stack trace."""
    from media_ai.credentials.redaction import register_secret

    secret = "sk-not-in-an-exception-event-000000"
    register_secret(secret)
    with telemetry.invocation("image.generate"):
        with telemetry.span("provider.generate_image") as sp:
            sp.record_error(RuntimeError(f"provider rejected {secret}"))
    span = recording.span("provider.generate_image")
    assert span is not None
    exception = next(event for event in span.events if event.name == "exception")
    rendered = f"{span.status.description}\n{exception.attributes}"
    assert secret not in rendered
    assert "***" in rendered


def test_a_url_attribute_drops_its_query_string():
    """Where a key ends up when an API takes one there (``?key=…``)."""
    from media_ai.providers._http import _scrub

    assert _scrub("https://api.example.com/v1/models?key=AIzaSECRET") == "https://api.example.com/v1/models"
    assert _scrub("https://api.example.com/v1/x") == "https://api.example.com/v1/x"


def test_a_log_line_masks_a_secret_in_both_renderings(capsys):
    from media_ai.credentials.redaction import register_secret

    register_secret("sk-another-fake-key-11111")
    for fmt in ("text", "json"):
        configure("debug", fmt=fmt)
        get_logger().warning("using sk-another-fake-key-11111")
        err = capsys.readouterr().err
        assert "sk-another-fake-key-11111" not in err and "***" in err


# ------------------------------------------------- settings precedence


def test_telemetry_is_off_by_default():
    assert telemetry.settings().enabled is False
    assert telemetry.boot() is None


def test_the_config_can_turn_it_on(configured, tmp_path, monkeypatch):
    path = configured({"mock/mock": None})
    path.write_text(path.read_text() + '\n[telemetry]\nenabled = true\nendpoint = "http://collector:4318"\n')
    cfg = telemetry.settings()
    assert cfg.enabled is True and cfg.endpoint == "http://collector:4318"


def test_the_environment_can_turn_a_configured_yes_back_off(configured, monkeypatch):
    """The three-state read, in the case it exists for: a shared config, one machine
    that must not export. A two-state read could only ever force it *on*."""
    path = configured({"mock/mock": None})
    path.write_text(path.read_text() + "\n[telemetry]\nenabled = true\n")
    monkeypatch.setenv("MEDIA_TELEMETRY", "0")
    assert telemetry.settings().enabled is False
    monkeypatch.setenv("MEDIA_TELEMETRY", "1")
    assert telemetry.settings().enabled is True


def test_otels_own_endpoint_variable_is_the_last_word_before_the_default(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://shared:4318")
    assert telemetry.settings().endpoint == "http://shared:4318"
    monkeypatch.setenv("MEDIA_TELEMETRY_ENDPOINT", "http://mine:4318/")
    assert telemetry.settings().endpoint == "http://mine:4318"  # ours wins, slash trimmed


def test_a_nonsense_exporter_in_the_environment_is_ignored_not_fatal(monkeypatch):
    """A stray variable in a shell profile must not fail every command in that shell."""
    monkeypatch.setenv("MEDIA_TELEMETRY_EXPORTER", "carrier-pigeon")
    assert telemetry.settings().exporter is Exporter.OTLP


def test_an_unreadable_config_does_not_stop_telemetry_from_answering(cfg_path, monkeypatch):
    """The diagnosis belongs to whoever needed the config, not to the observer."""
    cfg_path.write_text("schema = 2\nthis is not toml", encoding="utf-8")
    assert telemetry.settings().enabled is False


@pytest.mark.parametrize("table,message", [
    ('[telemetry]\nexporter = "otel"\n', "exporter"),
    ("[telemetry]\nenabled = 1\n", "enabled"),
    ("[telemetry]\ntimeout = -1\n", "timeout"),
    ("[telemetry]\nsample_percent = 300\n", "sample_percent"),
    ("[telemetry]\ntimeout = true\n", "timeout"),
])
def test_a_nonsense_value_in_the_config_is_a_config_error(cfg_path, table, message):
    """Strict on this side, because the failure it prevents is silence: ``otel`` for
    ``otlp`` read leniently leaves telemetry enabled and exporting nowhere."""
    from media_ai.core.config import load_config

    cfg_path.write_text(f"schema = 2\n\n{table}", encoding="utf-8")
    with pytest.raises(MediaError) as err:
        load_config()
    assert message in err.value.message


def test_the_table_survives_a_round_trip(cfg_path):
    from media_ai.core.config import load_config, render_config

    cfg_path.write_text(
        'schema = 2\n\n[telemetry]\nenabled = true\nexporter = "console"\ntimeout = 9\n', encoding="utf-8",
    )
    rendered = render_config(load_config())
    cfg_path.write_text(rendered, encoding="utf-8")
    again = load_config().telemetry
    assert again == TelemetrySettings(enabled=True, exporter=Exporter.CONSOLE, timeout=9)


def test_the_table_survives_an_unrelated_config_edit(cfg_path):
    """``bindings add`` rebuilds the whole file from the parsed object.

    A modelled table that some writer forgets is a setting undone by the next command
    about something else — which is the failure ``merged_with`` exists to prevent, and
    the reason every writer goes through it rather than reconstructing a ``Config``.
    """
    from media_ai.core.config import UserBinding, load_config, render_config

    cfg_path.write_text("schema = 2\n\n[telemetry]\nenabled = true\n", encoding="utf-8")
    config = load_config()
    edited = config.merged_with(bindings={"mock/mock": UserBinding(id="mock/mock")}, exists=True)
    cfg_path.write_text(render_config(edited), encoding="utf-8")
    assert load_config().telemetry.enabled is True


def test_a_default_configuration_writes_no_telemetry_table(cfg_path):
    """Same rule as ``[update]``: a table of defaults in every file is noise that
    invites editing settings nobody chose."""
    from media_ai.core.config import Config, render_config

    assert "[telemetry]" not in render_config(Config())


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(path))
    return path


# ------------------------------------------------- what a real command records


def test_a_generation_records_the_span_tree(recording, generate):
    assert generate() == 0
    names = recording.names()
    assert "cli.image.generate" in names
    assert "binding.resolve" in names
    assert "provider.generate_image" in names
    root = recording.span("cli.image.generate")
    assert root.parent is None
    assert root.attributes["command"] == "image.generate"
    assert root.attributes["exit_code"] == 0
    assert root.attributes["outcome"] == "ok"
    # the child spans are children, so a backend renders one trace and not three
    assert recording.span("binding.resolve").parent.span_id == root.context.span_id


def test_a_generation_labels_the_binding_it_chose_and_how(recording, generate):
    assert generate() == 0
    resolve = recording.span("binding.resolve")
    assert resolve.attributes["binding"] == "mock/mock"
    assert resolve.attributes["scene"] == "image.text_to_image"
    # nobody named a binding: the scene default chose, which is the case worth counting
    assert resolve.attributes["chosen_by"] == "default"


def test_a_generation_counts_its_artifacts_and_its_tokens(recording, generate):
    assert generate() == 0
    artifacts = recording.metrics.points("media_ai.artifacts")
    assert artifacts and artifacts[0].value == 1
    assert artifacts[0].attributes["binding"] == "mock/mock"
    assert artifacts[0].attributes["kind"] == "image"
    assert recording.metrics.points("media_ai.artifact.bytes")[0].value > 0
    # from the ledger, so the counter cannot disagree with `<cli> usage`
    assert recording.metrics.points("media_ai.usage.tokens")[0].value > 0


def test_a_generation_counts_one_invocation_with_its_exit_code(recording, generate):
    assert generate() == 0
    points = recording.metrics.points("media_ai.cli.invocations")
    assert len(points) == 1
    assert points[0].value == 1
    assert points[0].attributes == {"command": "image.generate", "outcome": "ok", "exit_code": 0}
    assert recording.metrics.points("media_ai.cli.duration")[0].count == 1


def test_a_failing_command_carries_the_error_category_on_both_signals(recording, generate, capsys):
    """``error.category`` is this project's taxonomy — the same value the JSON carries
    and the same one the exit code comes from. No second vocabulary for failure."""
    assert generate("--binding", "openai/gpt-image-2") == EXIT_CODES[ErrorCategory.AUTH]
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    root = recording.span("cli.image.generate")
    assert root.attributes["error.category"] == payload["error"]["category"]
    assert root.attributes["outcome"] == "error"
    assert root.status.status_code.name == "ERROR"
    point = recording.metrics.points("media_ai.cli.invocations")[0]
    assert point.attributes["outcome"] == "error"
    assert point.attributes["error.category"] == payload["error"]["category"]


def test_an_http_request_is_one_span_with_its_retries_counted(recording, monkeypatch):
    """One span per request, retries included: "how long did this take" means the whole
    thing, and the resend count is an attribute of that answer."""
    import urllib.error

    from media_ai.providers._http import HttpClient

    client = HttpClient(base_url="https://api.example.com", provider="example", max_retries=1, retry_base=0)
    calls = []

    def flaky(req, timeout=None):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", flaky)
    with telemetry.invocation("image.generate"):
        with pytest.raises(MediaError):
            client.request_json("POST", "/v1/images")
    span = recording.span("http.POST")
    assert span.attributes["url.full"] == "https://api.example.com/v1/images"
    assert span.attributes["http.response.status_code"] == 429
    assert span.attributes["http.request.resend_count"] == 1
    assert recording.metrics.points("media_ai.http.retries")[0].value == 1
    requests = recording.metrics.points("media_ai.http.requests")
    assert requests[0].attributes == {"provider": "example", "method": "POST", "status": 429, "outcome": "error"}


def test_a_local_encode_gets_a_span_of_its_own(recording, configured, tmp_path):
    """A local encode has no HTTP span to account for it, so without this a concat is a
    minute of wall clock a trace cannot explain."""
    from conftest import have_media_stack

    from media_ai.cli import video

    if not have_media_stack():
        pytest.skip("needs Pillow + ffmpeg")
    configured({"mock/mock": None, "local/ffmpeg": None},
               defaults={"video.text_to_video": "mock/mock", "video.concat": "local/ffmpeg"})
    clips = []
    for i in range(2):
        clip = tmp_path / f"clip{i}.mp4"
        old, sys.argv = sys.argv, ["media-ai video", "generate", "--prompt", "x", "--output", str(clip),
                                   "--duration", "1"]
        try:
            assert video.main() == 0
        finally:
            sys.argv = old
        clips.append(str(clip))
    old, sys.argv = sys.argv, ["media-ai video", "concat", "--inputs", *clips,
                               "--output", str(tmp_path / "film.mp4")]
    try:
        assert video.main() == 0
    finally:
        sys.argv = old
    assert "subprocess.ffmpeg" in recording.names()
    assert "provider.concat" in recording.names()
    assert recording.metrics.points("media_ai.subprocess.duration")


# ------------------------------------------------- logs


def test_a_log_line_carries_the_trace_it_happened_inside(recording, capsys):
    configure("debug", fmt="json")
    with telemetry.invocation("image.generate"):
        get_logger().info("something happened")
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.startswith('{"ts"')]
    said = next(line for line in lines if line["msg"] == "something happened")
    assert said["command"] == "image.generate"
    assert len(said["trace_id"]) == 32 and len(said["span_id"]) == 16
    root = recording.span("cli.image.generate")
    assert said["trace_id"] == format(root.context.trace_id, "032x")


def test_a_log_line_has_no_trace_fields_when_nothing_is_recording(capsys):
    configure("debug", fmt="json")
    get_logger().info("no trace here")
    line = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert "trace_id" not in line and "span_id" not in line


def test_the_text_rendering_keeps_the_fields_a_json_line_would_have(capsys):
    configure("debug", fmt="text")
    telemetry.event(telemetry.CLI_START, command="image.generate")
    err = capsys.readouterr().err
    assert "event=cli.start" in err and "command=image.generate" in err


def test_the_log_format_can_come_from_the_environment(capsys, monkeypatch):
    monkeypatch.setenv("MEDIA_LOG_FORMAT", "json")
    configure("info")
    get_logger().info("hello")
    assert json.loads(capsys.readouterr().err.splitlines()[-1])["msg"] == "hello"


def test_the_sdks_own_complaints_are_deferred_to_the_same_switch(monkeypatch):
    """A collector that is down must not put six retry warnings on the stderr a human
    is reading a real failure on — but ``--log-level debug`` is exactly when they help."""
    otel_logger = logging.getLogger("opentelemetry")
    configure("warning")
    runtime_mod._quiet_the_sdk()
    assert otel_logger.level == logging.CRITICAL
    configure("debug")
    runtime_mod._quiet_the_sdk()
    assert otel_logger.level == logging.WARNING


# ------------------------------------------------- the declarations are closed sets


def _calls_named(tree: ast.AST, names: set[str]) -> list[ast.Call]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) in names or getattr(node.func, "id", None) in names)
    ]


def _sources() -> list[tuple[Path, ast.AST]]:
    return [(p, ast.parse(p.read_text(encoding="utf-8"))) for p in sorted(SRC.rglob("*.py"))]


def test_every_event_call_site_names_a_declared_event():
    """The closed set, enforced where it is cheap.

    ``event()`` raises on an unknown name, which is only safe because this test finds
    the typo first — an error path is not where a new exception should be discovered.
    """
    offenders = []
    for path, tree in _sources():
        for call in _calls_named(tree, {"event"}):
            if not call.args:
                continue
            name = _literal_event(call.args[0])
            if name is not None and name not in events_mod.EVENTS:
                offenders.append(f"{path.relative_to(SRC)}:{call.lineno}: {name}")
    assert not offenders, "declare these in telemetry.events.EVENTS:\n" + "\n".join(offenders)


def _literal_event(node: ast.AST) -> str | None:
    """The event name a call site passes, whether spelled as a constant or a name."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    identifier = getattr(node, "attr", None) or getattr(node, "id", None)
    if identifier and identifier.isupper():
        return getattr(events_mod, identifier, None)
    return None


def test_every_metric_call_site_names_a_declared_instrument():
    offenders = []
    for path, tree in _sources():
        for call in _calls_named(tree, {"count", "observe"}):
            if not call.args or not isinstance(call.args[0], ast.Constant):
                continue
            name = call.args[0].value
            if not isinstance(name, str) or not name.startswith("media_ai."):
                continue
            if name not in metrics_mod.COUNTERS and name not in metrics_mod.HISTOGRAMS:
                offenders.append(f"{path.relative_to(SRC)}:{call.lineno}: {name}")
    assert not offenders, "declare these in telemetry.metrics:\n" + "\n".join(offenders)


def test_an_enum_label_is_flattened_to_its_value(recording):
    """This project's enums subclass ``str``, so a duck-typed check misses them and the
    label comes out as ``Scene.IMAGE_TEXT_TO_IMAGE``."""
    from media_ai.core.scene import Scene

    with telemetry.invocation("image.generate"):
        telemetry.count("media_ai.artifacts", 1, scene=Scene.IMAGE_TEXT_TO_IMAGE, kind="image")
    point = recording.metrics.points("media_ai.artifacts")[0]
    assert point.attributes["scene"] == "image.text_to_image"
    assert type(point.attributes["scene"]) is str


def test_the_metric_names_are_namespaced_by_the_import_package_not_the_brand():
    """A dashboard that survives a white-label rebuild is worth more than one whose
    series names match the executable — and ``brand.py`` says the import package is
    exactly what a rebrand does not rename."""
    assert all(name.startswith("media_ai.") for name in {**metrics_mod.COUNTERS, **metrics_mod.HISTOGRAMS})


def test_every_declared_event_has_a_log_level():
    assert set(events_mod.EVENTS) == {
        v for k, v in vars(events_mod).items() if k.isupper() and isinstance(v, str) and "." in v
    }


# ------------------------------------------------- the OTLP path, against a real socket


@needs_sdk
def test_the_otlp_exporter_reaches_a_collector(monkeypatch, generate, capsys):
    """The one test that proves the wire works, against a loopback stub.

    Everything else here patches the exporter out, which cannot catch a wrong path
    (``/v1/traces`` is appended by us, not by the SDK) or a payload the SDK refuses to
    build. Loopback only — no external host, nothing to reach.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):  # keep the test output quiet
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # `requests` honours the ambient proxy variables, and a proxy for a loopback
        # address is a connection that cannot succeed.
        for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        monkeypatch.setenv("MEDIA_TELEMETRY", "1")
        monkeypatch.setenv("MEDIA_TELEMETRY_EXPORTER", "otlp")
        monkeypatch.setenv("MEDIA_TELEMETRY_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
        assert generate() == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
    finally:
        server.shutdown()
    assert "/v1/traces" in received
    assert "/v1/metrics" in received
