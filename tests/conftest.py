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
                "MEDIA_CRED_BROKER", "MEDIA_CREDENTIALS_FILE", "MEDIA_CONFIG_FILE"):
        monkeypatch.delenv(var, raising=False)
    # A test that never writes a config still gets an empty one, so nothing reads the
    # developer's real ~/.config/media-ai while the suite runs.
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "config.toml"))
    registry.reset_catalog()
    return tmp_path / "usage.jsonl"


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
