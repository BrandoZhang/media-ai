"""Retry / idempotency policy of the shared HTTP client (fake urlopen).

Locks in the invariant that made the original Volc backend safe: a non-idempotent
POST (create video task) is NOT retried on a transient 5xx / network error (which
could double-submit a billed job), while 429 (rejected, not processed) and
idempotent GET/DELETE are retried.
"""

from __future__ import annotations

import io
import urllib.error

import pytest
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.providers import _http
from media_ai.providers._http import HttpClient


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def _http_error(code: int):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b'{"error":"boom"}'))


def _install(monkeypatch, behaviors):
    seq = list(behaviors)
    count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        count["n"] += 1
        b = seq.pop(0)
        if isinstance(b, Exception):
            raise b
        return _Resp(b)

    monkeypatch.setattr(_http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(_http.time, "sleep", lambda *a, **k: None)
    return count


@pytest.fixture
def client():
    return HttpClient(base_url="https://api.test/v1", provider="test", max_retries=4, retry_base=0)


def test_post_not_retried_on_5xx(client, monkeypatch):
    count = _install(monkeypatch, [_http_error(503)])
    with pytest.raises(MediaError):
        client.request_json("POST", "/tasks", body={"x": 1})
    assert count["n"] == 1


def test_post_not_retried_on_urlerror(client, monkeypatch):
    count = _install(monkeypatch, [urllib.error.URLError("conn reset")])
    with pytest.raises(MediaError):
        client.request_json("POST", "/tasks", body={"x": 1})
    assert count["n"] == 1


def test_post_retried_on_429(client, monkeypatch):
    count = _install(monkeypatch, [_http_error(429), b'{"ok": true}'])
    assert client.request_json("POST", "/tasks", body={"x": 1}) == {"ok": True}
    assert count["n"] == 2


def test_get_retried_on_5xx(client, monkeypatch):
    count = _install(monkeypatch, [_http_error(500), b'{"status": "succeeded"}'])
    assert client.request_json("GET", "/tasks/abc")["status"] == "succeeded"
    assert count["n"] == 2


def test_delete_retried_on_urlerror(client, monkeypatch):
    count = _install(monkeypatch, [urllib.error.URLError("x"), b"{}"])
    client.request_json("DELETE", "/tasks/abc")
    assert count["n"] == 2


def test_retry_classifier_vetoes_retry(monkeypatch):
    # a classifier that returns False turns a would-be 429 retry into an immediate fail
    count = _install(monkeypatch, [_http_error(429), b'{"ok": true}'])
    c = HttpClient(base_url="https://api.test", provider="test", max_retries=4, retry_base=0,
                   retry_classifier=lambda status, body: False)
    with pytest.raises(MediaError):
        c.request_json("POST", "/tasks", body={"x": 1})
    assert count["n"] == 1  # not retried


def test_retry_classifier_allows_retry(monkeypatch):
    count = _install(monkeypatch, [_http_error(429), b'{"ok": true}'])
    c = HttpClient(base_url="https://api.test", provider="test", max_retries=4, retry_base=0,
                   retry_classifier=lambda status, body: True)
    assert c.request_json("POST", "/tasks", body={"x": 1}) == {"ok": True}
    assert count["n"] == 2


def test_error_mapper_categorizes(monkeypatch):
    calls = []

    def mapper(status, body):
        calls.append(status)
        return MediaError("mapped", category=ErrorCategory.AUTH)

    c = HttpClient(base_url="https://api.test", provider="test", error_mapper=mapper, max_retries=0)
    _install(monkeypatch, [_http_error(401)])
    with pytest.raises(MediaError) as ei:
        c.request_json("GET", "/x")
    assert ei.value.category == ErrorCategory.AUTH and calls == [401]
