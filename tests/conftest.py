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

    def request_bytes(self, method, path, *, headers=None):
        self.calls.append({"method": method, "path": path, "bytes": True})
        return b"FAKE-VIDEO-BYTES"

    def download(self, url, out, *, headers=None):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKE-DOWNLOAD")
        self.downloads.append(url)
        self.calls.append({"download": url})
        return out


@pytest.fixture
def fake_provider(monkeypatch):
    """Return a factory: ``make(ProviderClass, responses) -> (provider, fake_client)``
    with the provider's HTTP layer replaced by a recording FakeClient."""

    def make(provider_cls, responses):
        prov = provider_cls()
        fake = FakeClient(responses)
        monkeypatch.setattr(prov, "_prepare", lambda **kw: (fake, {"Authorization": "Bearer test"}))
        return prov, fake

    return make


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("MEDIA_PROVIDER", "mock")
    # keep tests hermetic: no ambient provider keys / broker
    for var in ("ARK_API_KEY", "VOLC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                "MEDIA_CRED_BROKER", "MEDIA_CREDENTIALS_FILE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "usage.jsonl"


def have_media_stack() -> bool:
    try:
        import PIL  # noqa: F401

        from media_ai.media import ffmpeg

        ffmpeg.ffmpeg_exe()
        return True
    except Exception:
        return False
