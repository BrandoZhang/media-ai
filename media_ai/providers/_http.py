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
import random
import socket
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from ..core.errors import ErrorCategory, MediaError
from ..credentials.redaction import redact

_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


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
        self.retry_statuses = retry_statuses

    # ---- public helpers --------------------------------------------------
    def request_json(self, method: str, path: str, *, body: dict | None = None, headers: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        raw = self._send(method, self._url(path), data=data, headers=hdrs)
        return json.loads(raw) if raw else {}

    def request_multipart(
        self, method: str, path: str, *, fields: dict, files: list[tuple], headers: dict | None = None
    ) -> dict:
        """POST multipart/form-data. ``files`` is a list of
        ``(field_name, filename, mime, bytes)`` tuples (e.g. OpenAI edits)."""
        boundary = uuid.uuid4().hex
        body = _encode_multipart(boundary, fields, files)
        hdrs = {"Content-Type": f"multipart/form-data; boundary={boundary}", **(headers or {})}
        raw = self._send(method, self._url(path), data=body, headers=hdrs)
        return json.loads(raw) if raw else {}

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
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                    raw = resp.read()
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
                if retryable and attempt < self.max_retries and self._retry_ok(status, raw):
                    self._sleep(e, attempt)
                    continue
                raise self.error_mapper(status, redact(raw[:800]))
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                is_timeout = isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(
                    getattr(exc, "reason", None), (socket.timeout, TimeoutError)
                )
                if idempotent and attempt < self.max_retries:
                    time.sleep(self.retry_base * (2**attempt) + random.uniform(0, 0.5))
                    continue
                cat = ErrorCategory.TIMEOUT if is_timeout else ErrorCategory.PROVIDER
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

    def _sleep(self, err: urllib.error.HTTPError, attempt: int) -> None:
        retry_after = err.headers.get("Retry-After") if err.headers else None
        delay = float(retry_after) if (retry_after and str(retry_after).isdigit()) else self.retry_base * (2**attempt)
        time.sleep(delay + random.uniform(0, 0.5))

    def _default_error(self, status: int, body: str) -> MediaError:
        cat = {
            400: ErrorCategory.VALIDATION, 401: ErrorCategory.AUTH, 403: ErrorCategory.AUTH,
            404: ErrorCategory.NOT_FOUND, 408: ErrorCategory.TIMEOUT, 429: ErrorCategory.RATE_LIMIT,
        }.get(status, ErrorCategory.PROVIDER if status >= 500 else ErrorCategory.PROVIDER)
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
