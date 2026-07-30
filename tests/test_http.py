"""Retry / idempotency policy of the shared HTTP client (fake urlopen).

Locks in the invariant that made the original Volc backend safe: a non-idempotent
POST (create video task) is NOT retried on a transient 5xx / network error (which
could double-submit a billed job), while 429 (rejected, not processed) and
idempotent GET/DELETE are retried.
"""

from __future__ import annotations

import io
import http.client
import urllib.error

import pytest
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.logging import configure
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


def test_get_retried_on_remote_disconnect(client, monkeypatch):
    # A signed artifact URL may close the connection before a response status. It is
    # still a transient network failure and a GET is safe to retry.
    count = _install(monkeypatch, [http.client.RemoteDisconnected("closed"), b'{"status": "succeeded"}'])
    assert client.request_json("GET", "/tasks/abc")["status"] == "succeeded"
    assert count["n"] == 2


def test_post_not_retried_on_remote_disconnect(client, monkeypatch):
    count = _install(monkeypatch, [http.client.RemoteDisconnected("closed")])
    with pytest.raises(MediaError) as ei:
        client.request_json("POST", "/tasks", body={"x": 1})
    assert ei.value.category == ErrorCategory.PROVIDER and ei.value.provider == "test"
    assert count["n"] == 1


def test_get_retried_on_408(client, monkeypatch):
    # 408 REQUEST_TIMEOUT is transient (per the Gemini troubleshooting guide) and
    # safe to retry on an idempotent GET.
    count = _install(monkeypatch, [_http_error(408), b'{"status": "succeeded"}'])
    assert client.request_json("GET", "/tasks/abc")["status"] == "succeeded"
    assert count["n"] == 2


def test_post_not_retried_on_408(client, monkeypatch):
    # ...but a non-idempotent POST must not be retried (could double-submit).
    count = _install(monkeypatch, [_http_error(408)])
    with pytest.raises(MediaError):
        client.request_json("POST", "/tasks", body={"x": 1})
    assert count["n"] == 1


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


def test_sse_json_parses_data_events_and_ignores_done(client, monkeypatch):
    raw = b'event: message\ndata: {"image_index":0,"url":"https://example.test/a.jpg"}\n\ndata: [DONE]\n'
    _install(monkeypatch, [raw])
    assert client.request_sse_json("POST", "/images", body={"stream": True}) == [
        {"image_index": 0, "url": "https://example.test/a.jpg"}
    ]


def test_sse_json_rejects_malformed_events(client, monkeypatch):
    _install(monkeypatch, [b"data: not-json\n"])
    with pytest.raises(MediaError) as ei:
        client.request_sse_json("POST", "/images", body={"stream": True})
    assert ei.value.category == ErrorCategory.PROVIDER


def test_verbose_http_diagnostics_show_redacted_headers_and_json_body(client, monkeypatch, capsys):
    _install(monkeypatch, [b'{"ok": true}'])
    configure("debug")
    try:
        client.request_json(
            "POST", "/tasks", headers={"Authorization": "Bearer ark-secret-123456", "X-API-Key": "header-secret"},
            body={"prompt": "visible", "api_key": "also-secret"},
        )
    finally:
        configure()
    stderr = capsys.readouterr().err
    assert "HTTP request:" in stderr and '"Authorization": "***"' in stderr and '"X-api-key": "***"' in stderr
    assert '"prompt": "visible"' in stderr and '"api_key": "***"' in stderr
    assert "ark-secret-123456" not in stderr and "header-secret" not in stderr and "also-secret" not in stderr


# ---- default error mapping (providers that ship no `_error` of their own) --


@pytest.mark.parametrize("status, category", [
    (400, ErrorCategory.VALIDATION), (401, ErrorCategory.AUTH), (403, ErrorCategory.AUTH),
    (404, ErrorCategory.NOT_FOUND), (408, ErrorCategory.TIMEOUT), (429, ErrorCategory.RATE_LIMIT),
    # An unenumerated 4xx is a request problem: exit 3, not retryable. Both arms of the
    # default said PROVIDER, so a 409/413/422 came back as an upstream fault and told the
    # caller to retry something that could never succeed.
    (409, ErrorCategory.VALIDATION), (413, ErrorCategory.VALIDATION), (422, ErrorCategory.VALIDATION),
    (500, ErrorCategory.PROVIDER), (503, ErrorCategory.PROVIDER),
])
def test_default_error_categorizes_by_status(client, status, category):
    err = client._default_error(status, "boom")
    assert err.category is category
    assert err.details["status"] == status
    assert err.retryable is (category in {ErrorCategory.RATE_LIMIT, ErrorCategory.TIMEOUT, ErrorCategory.PROVIDER})


# ---- a response body that is not what it claims to be ---------------------


@pytest.mark.parametrize("body", [
    b"<html><body>502 Bad Gateway</body></html>",   # a proxy or gateway answering for the API
    b"Service Unavailable",                          # a load balancer's plain text
    b"{not json at all",                             # a truncated body
])
def test_a_non_json_200_is_a_provider_error_not_a_traceback(client, monkeypatch, body):
    """`json.loads` raising here escaped the whole adapter layer.

    Past the error mapper, past MediaError, out to `run()`'s last-resort handler — so a
    proxy answering with an HTML page surfaced as exit 1 `unknown` reading "Expecting
    value: line 1 column 1 (char 0)": nothing to branch on, nobody named.
    """
    _install(monkeypatch, [body])
    with pytest.raises(MediaError) as ei:
        client.request_json("GET", "/x")
    assert ei.value.category is ErrorCategory.PROVIDER and ei.value.code == "malformed_response"
    assert ei.value.provider == "test" and "/x" in ei.value.details["url"]


def test_valid_json_that_is_not_an_object_is_the_same_refusal(client, monkeypatch):
    # Every caller does `.get` on the result; a list or a string would crash one step later.
    _install(monkeypatch, [b"[1, 2, 3]"])
    with pytest.raises(MediaError) as ei:
        client.request_json("GET", "/x")
    assert ei.value.code == "malformed_response" and "list" in ei.value.message


def test_an_empty_body_is_still_an_empty_object(client, monkeypatch):
    # A 200 with no body (DELETE/cancel) is legitimate and must not become an error.
    _install(monkeypatch, [b""])
    assert client.request_json("DELETE", "/tasks/abc") == {}


# ---- Retry-After is honoured, but not to the point of hanging -------------


def _retry_after(monkeypatch, seconds: str, *, status: int = 429):
    slept: list[float] = []
    monkeypatch.setattr(_http.time, "sleep", lambda s: slept.append(s))
    err = urllib.error.HTTPError("http://x", status, "slow down", {"Retry-After": seconds},
                                 io.BytesIO(b'{"error":"quota"}'))
    _install(monkeypatch, [err, b'{"ok": true}'])
    monkeypatch.setattr(_http.time, "sleep", lambda s: slept.append(s))  # _install re-stubs it
    return slept


def test_a_short_retry_after_is_waited_out(client, monkeypatch):
    slept = _retry_after(monkeypatch, "5")
    assert client.request_json("GET", "/x") == {"ok": True}
    assert 5 <= slept[0] <= 5.5  # the server's number, plus jitter


def test_a_retry_after_beyond_the_cap_hands_the_error_back_instead_of_sleeping(client, monkeypatch):
    """A daily-quota 429 answers `Retry-After: 3600`, and four attempts of that is four
    hours of a blocked process with nothing on stdout. The error already says
    `rate_limit`/`retryable: true` — waiting is the caller's decision to make."""
    slept = _retry_after(monkeypatch, "3600")
    with pytest.raises(MediaError) as ei:
        client.request_json("GET", "/x")
    assert ei.value.category is ErrorCategory.RATE_LIMIT and ei.value.retryable is True
    assert slept == [], "nothing should have been slept on"


def test_the_cap_is_per_client(monkeypatch):
    slept = _retry_after(monkeypatch, "120")
    c = HttpClient(base_url="https://api.test/v1", provider="test", max_retries=4, retry_base=0,
                   retry_after_max=300)
    assert c.request_json("GET", "/x") == {"ok": True}
    assert 120 <= slept[0] <= 120.5


def test_a_junk_retry_after_falls_back_to_backoff(client, monkeypatch):
    # An HTTP-date (legal, and what some CDNs send) is not a number of seconds.
    slept = _retry_after(monkeypatch, "Wed, 21 Oct 2026 07:28:00 GMT")
    assert client.request_json("GET", "/x") == {"ok": True}
    assert slept and slept[0] < 60
