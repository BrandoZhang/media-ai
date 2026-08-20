"""Shared test fixtures.

Everything runs offline. Provider adapters are exercised against a ``FakeClient``
that records the request bodies they build and returns canned responses, so no
network or credentials are needed (the pattern the original ``test_volc_request``
used, generalized to every provider).
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from media_ai.core import registry
from media_ai.core.binding import builtin_catalog

# a 1x1 PNG, so image-saving paths can write bytes without a network fetch
PNG_1x1 = base64.b64encode(
    base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
).decode()
PNG_1x1_BYTES = base64.b64decode(PNG_1x1)


class FakeClient:
    """Stand-in for :class:`media_ai.providers._http.HttpClient`. Records calls and
    dispenses queued responses (an ``Exception`` in the queue is raised)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.downloads: list[str] = []

    def _next(self):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def request_json(self, method, path, *, body=None, headers=None):
        self.calls.append({"method": method, "path": path, "body": body, "headers": headers})
        return self._next()

    def request_sse_json(self, method, path, *, body=None, headers=None):
        self.calls.append({"method": method, "path": path, "body": body, "headers": headers, "sse": True})
        return self._next()

    def request_multipart(self, method, path, *, fields, files, headers=None):
        self.calls.append({"method": method, "path": path, "fields": fields, "files": files, "multipart": True})
        return self._next()

    def request_bytes(self, method, path, *, body=None, headers=None):
        self.calls.append({"method": method, "path": path, "body": body, "bytes": True})
        # Pop a queued response only when it's actually bytes (or an Exception to raise);
        # otherwise return the constant so existing JSON-queue tests (volc/openai) stay green.
        if self.responses and isinstance(self.responses[0], (bytes, bytearray, Exception)):
            return self._next()
        return b"FAKE-VIDEO-BYTES"

    def download(self, url, out, *, headers=None):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKE-DOWNLOAD")
        self.downloads.append(url)
        self.calls.append({"download": url})
        return out


def bound(binding_id: str, **overrides):
    """A :class:`ResolvedBinding` for a shipped binding, as the CLI would resolve one.

    Adapters are constructed from a binding and nothing else, so a test that wants one
    names the binding it is testing. That is more typing than the old
    ``VolcProvider()`` and it is the point: which model, which endpoint and which
    limits apply are now the same question, and a test has to answer it.
    """
    from media_ai.core.config import Config, UserBinding
    from media_ai.core.resolve import resolve

    config = Config(bindings={binding_id: UserBinding(
        id=binding_id, credential=overrides.pop("credential", "env://MEDIA_TEST_KEY"), **overrides,
    )})
    return resolve(binding=binding_id, catalog=registry.catalog(), config=config)


def adapter_for(binding_id: str, **overrides):
    """The adapter a binding names, constructed but not stubbed."""
    from media_ai.core.registry import build_adapter

    return build_adapter(bound(binding_id, **overrides))


@pytest.fixture
def fake_provider(monkeypatch):
    """``make(binding_id, responses) -> (adapter, fake_client)`` with HTTP stubbed out."""

    def make(binding_id, responses, **overrides):
        prov = adapter_for(binding_id, **overrides)
        fake = FakeClient(responses)
        monkeypatch.setattr(prov, "_prepare", lambda **kw: (fake, {"Authorization": "Bearer test"}))
        return prov, fake

    return make


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch, request):
    monkeypatch.setenv("MEDIA_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    # `live` tests hit real APIs and MUST keep the real environment (keys, base
    # URLs, model ids); only the offline tests are scrubbed hermetic.
    if request.node.get_closest_marker("live"):
        return tmp_path / "usage.jsonl"
    for var in ("ARK_API_KEY", "VOLC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                "ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "ELEVENLABS_BASE_URL",
                "MEDIA_CRED_BROKER", "MEDIA_CREDENTIALS_FILE", "MEDIA_CONFIG_FILE",
                # Telemetry is off unless a test says otherwise. Left set, a developer's
                # own `MEDIA_TELEMETRY=1` would make the suite boot an SDK and try to
                # reach their collector — and `OTEL_EXPORTER_OTLP_ENDPOINT` is exactly
                # the variable a machine with one already exports globally.
                "MEDIA_TELEMETRY", "MEDIA_TELEMETRY_EXPORTER", "MEDIA_TELEMETRY_ENDPOINT",
                "MEDIA_TELEMETRY_TIMEOUT", "MEDIA_LOG_FORMAT", "MEDIA_LOG_LEVEL",
                "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_SERVICE_NAME"):
        monkeypatch.delenv(var, raising=False)
    # A test that never writes a config still gets an empty one, so nothing reads the
    # developer's real ~/.config/media-ai while the suite runs. Both files, not just the
    # config: unsetting `MEDIA_CREDENTIALS_FILE` sends the resolver to the real path,
    # which was survivable only while nothing in the suite *wrote* there. It stopped
    # being survivable the moment `config migrate` grew a credentials half — a test
    # asserting "nothing to convert" would have converted the developer's own keys.
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
    # The release feed points at a file that is not there, rather than being unset.
    # Unset means the published URL, so a test that reaches `update.refresh` without
    # saying otherwise would make a real network call — quietly passing on a laptop and
    # failing, or worse succeeding, in CI. A test that wants a feed writes one and
    # points this at it.
    monkeypatch.setenv("MEDIA_UPDATE_FEED", (tmp_path / "no-such-feed.json").as_uri())
    # And the check is off by default, which it did not used to be — back when nothing
    # happened unless a test asked, "the code path that decides whether to check at all
    # is itself under test" was reason enough to leave this alone. It stopped being: a
    # command now ends by *forking*, and several tests here drive the CLI as a real
    # subprocess (`test_cli`, `test_brand`, `test_version`, `test_init`, `test_doctor`),
    # where no amount of monkeypatching reaches the child. An environment variable is
    # the only lever that crosses that boundary. The five files that are about update
    # checking delete it in their own fixtures, exactly as they already do for `CI`.
    monkeypatch.setenv("MEDIA_UPDATE_CHECK", "0")
    registry.reset_catalog()
    return tmp_path / "usage.jsonl"


@pytest.fixture(autouse=True)
def _no_background_refresh(monkeypatch, request):
    """The suite forks nothing. Every command now ends by spawning a detached child.

    That is the feature, and in a test run it is 70-odd real processes per `pytest -q`
    — each one an interpreter start, each one writing into a `tmp_path` asynchronously
    after the test that owned it has been torn down, and each one on a `live` run
    pointed at the published feed for real. None of it is what any of those tests are
    asserting, and a background writer racing a fixture's cleanup is the kind of flake
    that gets blamed on everything except the thing that caused it.

    `_spawn` and not `subprocess.Popen`: this must not touch the ffmpeg tests, which
    spawn on purpose. Everything above the fork — `due`, the stamp, the lock, the argv
    — still runs, so what is stubbed out is the one syscall a test has no use for.
    `tests/test_update_auto.py` puts the real one back where it is the subject.

    The second half of a pair, and each half covers what the other cannot.
    `MEDIA_UPDATE_CHECK=0` above crosses into CLI subprocesses, where nothing in this
    process can reach; this covers the five files that delete that variable because
    they are *about* update checking, and which would otherwise fork while asserting
    something else entirely.
    """
    from media_ai.core import update

    if request.node.get_closest_marker("spawns_refresh"):
        return
    monkeypatch.setattr(update, "_spawn", lambda: True)


@pytest.fixture(autouse=True)
def _telemetry_is_not_left_running():
    """No test hands the next one a booted SDK.

    The runtime is a module global — one per process, as a CLI wants — so a test that
    enables telemetry and fails before its own teardown would otherwise leave every
    later test exporting into its exporter. Shutting down after each is cheap (a no-op
    when nothing booted) and makes the state per-test rather than per-session.
    """
    from media_ai.core import telemetry

    telemetry.shutdown()
    try:
        yield
    finally:
        telemetry.shutdown()


@pytest.fixture(autouse=True)
def _terminal_env(monkeypatch):
    """Give every test one terminal environment, whatever the runner's own is.

    ``CI`` and ``TERM=dumb`` now steer :func:`media_ai.cli._prompt.get_prompter` to the
    non-interactive fallback — which is the point of them, and which would otherwise
    make every pty test here fail on CI and pass on a laptop. The tests that exercise
    that behaviour set the variables back explicitly.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


@pytest.fixture
def clean_registry():
    """Snapshot and restore the module-global binding catalog around a test."""
    saved = list(registry._EXTRA)
    try:
        yield
    finally:
        registry._EXTRA[:] = saved
        registry.reset_catalog()


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """Write a config that makes the named bindings callable, and return its path.

    ``configured({"mock/mock": None}, defaults={"image.text_to_image": "mock/mock"})``
    is the offline equivalent of having run the wizard.
    """

    def make(bindings: dict, defaults: dict | None = None, *, name="config.toml") -> Path:
        from media_ai.core.config import Config, UserBinding, render_config

        path = tmp_path / name
        config = Config(
            bindings={bid: UserBinding(id=bid, credential=cred) for bid, cred in bindings.items()},
            defaults=defaults or {},
        )
        path.write_text(render_config(config), encoding="utf-8")
        monkeypatch.setenv("MEDIA_CONFIG_FILE", str(path))
        return path

    return make


CATALOG = builtin_catalog()


def have_media_stack() -> bool:
    try:
        import PIL  # noqa: F401

        from media_ai.media import ffmpeg

        ffmpeg.ffmpeg_exe()
        return True
    except Exception:
        return False
