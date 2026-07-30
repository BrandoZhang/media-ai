"""Shared HTTP client for provider adapters (stdlib only).

Generalizes the original Volc backend's request logic:

* **Idempotency-aware retry** — a ``429`` (rejected, not processed) is always safe
  to retry; a transient ``408``/``5xx``/network error is retried **only** on
  idempotent methods (``GET``/``DELETE``), so a non-idempotent ``POST`` create-task
  can't double-submit a billed job.
* **Backoff** — honors ``Retry-After`` when present, else exponential backoff with
  jitter.
* **Error mapping** — a provider supplies an ``error_mapper(status, body)`` that
  turns an HTTP error into a categorized :class:`MediaError`.

Response/error bodies are redacted before they enter any exception message.
"""

from __future__ import annotations

import json
import http.client
import random
import socket
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from ..core.errors import ErrorCategory, MediaError
from ..core.logging import get_logger
from ..credentials.redaction import redact, redact_obj

_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
#: Longest ``Retry-After`` this client will wait out. Past it the error is handed back
#: instead — see :meth:`HttpClient._retry_delay`.
_RETRY_AFTER_MAX = 60.0
_DEBUG_BODY_LIMIT = 8_000


def _debug_body(data: bytes | None, headers: dict) -> str:
    """Render an outbound body for diagnostics without leaking credentials or blobs."""
    if data is None:
        return "<empty>"
    content_type = str(headers.get("Content-Type", "")).lower()
    if "application/json" not in content_type:
        return f"<{len(data)} bytes; {content_type or 'binary'} body omitted>"
    try:
        rendered = json.dumps(redact_obj(json.loads(data)), ensure_ascii=False, sort_keys=True)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"<{len(data)} bytes; malformed JSON body omitted>"
    if len(rendered) > _DEBUG_BODY_LIMIT:
        return f"{rendered[:_DEBUG_BODY_LIMIT]}… <truncated; {len(rendered)} chars total>"
    return rendered


class HttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        provider: str,
        error_mapper: Callable[[int, str], MediaError] | None = None,
        retry_classifier: Callable[[int, str], bool] | None = None,
        timeout: float = 120.0,
        max_retries: int = 4,
        retry_base: float = 2.0,
        retry_after_max: float = _RETRY_AFTER_MAX,
        retry_statuses: frozenset[int] = _RETRY_STATUSES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.provider = provider
        self.error_mapper = error_mapper or self._default_error
        # Optional hook to VETO a would-be retry after inspecting the response body
        # (e.g. a 429 QuotaExceeded is a hard cap, not a transient rate limit). It
        # can only turn a retry off, never force one on.
        self.retry_classifier = retry_classifier
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base = retry_base
        self.retry_after_max = retry_after_max
        self.retry_statuses = retry_statuses

    # ---- public helpers --------------------------------------------------
    def request_json(self, method: str, path: str, *, body: dict | None = None, headers: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        raw = self._send(method, self._url(path), data=data, headers=hdrs)
        return self._json(raw, self._url(path))

    def request_sse_json(self, method: str, path: str, *, body: dict | None = None, headers: dict | None = None) -> list[dict]:
        """Return JSON payloads from a completed server-sent-event response.

        The request keeps the same retry/idempotency rules as ``request_json``.  In
        particular, an interrupted POST is not replayed: it may already have started
        a billed generation.  Providers that expose a synchronous streaming response
        can consume the ordered events once the server sends its final ``[DONE]``.
        """
        data = json.dumps(body).encode("utf-8") if body is not None else None
        hdrs = {"Content-Type": "application/json", "Accept": "text/event-stream", **(headers or {})}
        raw = self._send(method, self._url(path), data=data, headers=hdrs)
        events: list[dict] = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            item = line.removeprefix("data:").strip()
            if not item or item == "[DONE]":
                continue
            try:
                payload = json.loads(item)
            except json.JSONDecodeError as exc:
                raise MediaError(
                    f"{self.provider} returned malformed SSE JSON",
                    category=ErrorCategory.PROVIDER,
                    provider=self.provider,
                ) from exc
            if not isinstance(payload, dict):
                raise MediaError(
                    f"{self.provider} returned an SSE event that is not an object",
                    category=ErrorCategory.PROVIDER,
                    provider=self.provider,
                )
            events.append(payload)
        return events

    def request_multipart(
        self, method: str, path: str, *, fields: dict, files: list[tuple], headers: dict | None = None
    ) -> dict:
        """POST multipart/form-data. ``files`` is a list of
        ``(field_name, filename, mime, bytes)`` tuples (e.g. OpenAI edits)."""
        boundary = uuid.uuid4().hex
        body = _encode_multipart(boundary, fields, files)
        hdrs = {"Content-Type": f"multipart/form-data; boundary={boundary}", **(headers or {})}
        # Raw multipart bytes can contain large images/audio. Keep the diagnostic
        # useful without dumping the binary payload into a terminal scrollback.
        get_logger().debug(
            "HTTP multipart body: fields=%s files=%s",
            json.dumps(redact_obj(fields), ensure_ascii=False, sort_keys=True),
            json.dumps(
                [
                    {"field": field, "filename": filename, "mime": mime, "bytes": len(content)}
                    for field, filename, mime, content in files
                ],
                ensure_ascii=False,
            ),
        )
        raw = self._send(method, self._url(path), data=body, headers=hdrs)
        return self._json(raw, self._url(path))

    def request_bytes(self, method: str, path: str, *, body: dict | None = None, headers: dict | None = None) -> bytes:
        """Return the raw response bytes (e.g. audio/video). ``body`` (optional) is
        JSON-encoded, so a POST that sends JSON and returns binary is expressible.
        A POST is still non-idempotent, so ``_send`` won't retry it on transient 5xx."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        hdrs = {**({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})}
        return self._send(method, self._url(path), data=data, headers=hdrs, decode=False)

    def download(self, url: str, out: Path, *, headers: dict | None = None) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self._send("GET", url, data=None, headers=headers or {}, decode=False, timeout=max(self.timeout, 180))
        out.write_bytes(data)
        return out

    def _json(self, raw: str, url: str) -> dict:
        """Parse a response body that is supposed to be JSON, or say whose it was.

        A 200 carrying something else is not exotic: a proxy or gateway that answers with
        an HTML page, a captive portal, a base URL pointing at a web root. ``json.loads``
        raising here escaped the whole adapter layer — past the error mapper, past
        ``MediaError`` — and surfaced as an exit-1 ``unknown`` reading "Expecting value:
        line 1 column 1 (char 0)": no category to branch on, no provider named, and a
        message about a Python parser rather than about the request. A body that is
        valid JSON but not an object is the same problem one step later, since every
        caller immediately does ``.get``.
        """
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MediaError(
                f"{self.provider} returned a non-JSON response from {url}: {redact(raw[:200])}",
                category=ErrorCategory.PROVIDER, code="malformed_response", provider=self.provider,
                details={"url": url},
            ) from exc
        if not isinstance(data, dict):
            raise MediaError(
                f"{self.provider} returned {type(data).__name__}, not a JSON object, from {url}",
                category=ErrorCategory.PROVIDER, code="malformed_response", provider=self.provider,
                details={"url": url},
            )
        return data

    # ---- core send + retry ----------------------------------------------
    def _url(self, path: str) -> str:
        return path if path.startswith(("http://", "https://")) else f"{self.base_url}{path}"

    def _send(self, method, url, *, data, headers, decode=True, timeout=None):
        idempotent = method in ("GET", "DELETE")
        timeout = timeout or self.timeout
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=data, method=method)
            for k, v in headers.items():
                req.add_header(k, v)
            # A Request normalizes header names and adds its own framing headers, so
            # log it only after construction. ``redact_obj`` masks Authorization and
            # every known secret-shaped field before it reaches stderr.
            get_logger().debug(
                "HTTP request: attempt=%d method=%s url=%s headers=%s body=%s",
                attempt + 1, method, url,
                json.dumps(redact_obj(dict(req.header_items())), ensure_ascii=False, sort_keys=True),
                _debug_body(data, headers),
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                    raw = resp.read()
                    get_logger().debug(
                        "HTTP response: method=%s url=%s status=%s bytes=%d",
                        method, url, getattr(resp, "status", 200), len(raw),
                    )
                    return raw.decode("utf-8") if decode else raw
            except urllib.error.HTTPError as e:
                status = e.code
                raw = ""
                if hasattr(e, "read"):
                    try:
                        raw = e.read().decode("utf-8", "replace")[:2000]
                    except Exception:  # noqa: BLE001
                        raw = ""
                # 429 is always safe to retry (rejected, not processed); transient
                # 5xx/network only on idempotent methods. A classifier may then veto
                # a retry it knows is pointless (e.g. quota exhausted).
                retryable = status == 429 or (status in self.retry_statuses and idempotent)
                delay = self._retry_delay(e, attempt) if retryable else None
                if retryable and delay is not None and attempt < self.max_retries and self._retry_ok(status, raw):
                    get_logger().debug(
                        "HTTP response: method=%s url=%s status=%d retrying=true in=%.1fs",
                        method, url, status, delay,
                    )
                    time.sleep(delay)
                    continue
                get_logger().debug(
                    "HTTP response: method=%s url=%s status=%d retrying=false", method, url, status,
                )
                raise self.error_mapper(status, redact(raw[:800]))
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, http.client.HTTPException) as exc:
                is_timeout = isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(
                    getattr(exc, "reason", None), (socket.timeout, TimeoutError)
                )
                if idempotent and attempt < self.max_retries:
                    get_logger().debug(
                        "HTTP failure: method=%s url=%s retrying=true error=%s", method, url, redact(str(exc)),
                    )
                    time.sleep(self.retry_base * (2**attempt) + random.uniform(0, 0.5))
                    continue
                cat = ErrorCategory.TIMEOUT if is_timeout else ErrorCategory.PROVIDER
                get_logger().debug(
                    "HTTP failure: method=%s url=%s retrying=false error=%s", method, url, redact(str(exc)),
                )
                raise MediaError(
                    f"{self.provider} request failed: {redact(str(exc))}", category=cat, provider=self.provider
                ) from None
        raise MediaError(f"{self.provider} request failed after retries", category=ErrorCategory.PROVIDER, provider=self.provider)

    def _retry_ok(self, status: int, body: str) -> bool:
        """Let a provider veto a would-be retry (default: allow). A classifier
        error never blocks a legitimate retry."""
        if self.retry_classifier is None:
            return True
        try:
            return bool(self.retry_classifier(status, body))
        except Exception:  # noqa: BLE001
            return True

    def _retry_delay(self, err: urllib.error.HTTPError, attempt: int) -> float | None:
        """How long to wait before retrying, or ``None`` to stop retrying now.

        ``Retry-After`` is honoured up to ``retry_after_max`` and **not beyond**. The
        header is where a provider reports a *daily* cap — Gemini and Ark both answer a
        quota 429 with hours — and sleeping on it turned a rate-limit error into a
        blocked process: four attempts × one hour, with nothing on stdout and no way for
        the caller to reconsider. Handing the error back instead costs nothing, because
        it arrives categorized ``rate_limit`` with ``retryable: true``: an agent that
        wants to wait an hour can, and one that wants to switch bindings can too.
        """
        retry_after = err.headers.get("Retry-After") if err.headers else None
        if retry_after and str(retry_after).strip().isdigit():
            wait = float(str(retry_after).strip())
            return None if wait > self.retry_after_max else wait + random.uniform(0, 0.5)
        return self.retry_base * (2**attempt) + random.uniform(0, 0.5)

    def _default_error(self, status: int, body: str) -> MediaError:
        # An unenumerated 4xx (409, 413, 422 …) is something about the *request*: not
        # retryable, exit 3, and the caller has to change what they sent. Only 5xx is an
        # upstream fault. Both arms of this default used to say PROVIDER, which told a
        # caller to retry a request that could never succeed.
        cat = {
            400: ErrorCategory.VALIDATION, 401: ErrorCategory.AUTH, 403: ErrorCategory.AUTH,
            404: ErrorCategory.NOT_FOUND, 408: ErrorCategory.TIMEOUT, 429: ErrorCategory.RATE_LIMIT,
        }.get(status, ErrorCategory.PROVIDER if status >= 500 else ErrorCategory.VALIDATION)
        return MediaError(f"{self.provider} HTTP {status}: {body}", category=cat, provider=self.provider,
                          details={"status": status})


def _encode_multipart(boundary: str, fields: dict, files: list[tuple]) -> bytes:
    out = bytearray()
    b = boundary.encode()
    for name, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for v in value:
                out += b"--" + b + b"\r\n"
                out += f'Content-Disposition: form-data; name="{name}[]"\r\n\r\n'.encode()
                out += str(v).encode() + b"\r\n"
            continue
        out += b"--" + b + b"\r\n"
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += str(value).encode() + b"\r\n"
    for field_name, filename, mime, content in files:
        out += b"--" + b + b"\r\n"
        out += f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
        out += f"Content-Type: {mime}\r\n\r\n".encode()
        out += content + b"\r\n"
    out += b"--" + b + b"--\r\n"
    return bytes(out)
