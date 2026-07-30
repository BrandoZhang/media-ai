"""Gemini Files API upload (resumable protocol).

``generateContent`` can reference a large local input by URI (``fileData.fileUri``)
instead of inlining its bytes, so inputs above the ~20 MB inline-request cap become
usable. The upload is a two-step resumable exchange followed by a readiness poll:

1. **start** — ``POST /upload/v1beta/files`` with ``X-Goog-Upload-Command: start`` and
   the content length/type in headers; the response carries an ``X-Goog-Upload-URL``.
2. **upload+finalize** — ``POST`` the raw bytes to that URL with
   ``X-Goog-Upload-Command: upload, finalize``; the response is the file resource.
3. **await ACTIVE** — images are ``ACTIVE`` immediately; video/large media may report
   ``PROCESSING`` and must be polled until usable.

Only the direct API-key path is supported: this is a separate Google endpoint that a
credential broker does not forward, so a brokered call would arrive with no key at all.
The caller gates on that before the first byte — see
:meth:`media_ai.providers.gemini.GeminiAdapter._require_direct_key`. Uses stdlib urllib
(proxy/TLS come from the environment, same as :mod:`media_ai.providers._http`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from ..core.errors import ErrorCategory, MediaError
from ..credentials.redaction import redact


def _files_endpoint(base_url: str) -> str:
    b = base_url.rstrip("/")
    # base_url ends with /v1beta; the upload endpoint lives under /upload/v1beta.
    return (b[: -len("/v1beta")] if b.endswith("/v1beta") else b) + "/upload/v1beta/files"


def _raise(exc: urllib.error.HTTPError) -> MediaError:
    body = ""
    try:
        body = exc.read().decode("utf-8", "replace")[:400]
    except Exception:  # noqa: BLE001
        pass
    cat = {401: ErrorCategory.AUTH, 403: ErrorCategory.AUTH, 404: ErrorCategory.NOT_FOUND,
           429: ErrorCategory.RATE_LIMIT}.get(exc.code, ErrorCategory.VALIDATION if exc.code < 500 else ErrorCategory.PROVIDER)
    return MediaError(f"Gemini Files API HTTP {exc.code}: {redact(body)}", category=cat, provider="gemini",
                      details={"status": exc.code})


def _timed_out(exc: BaseException) -> bool:
    """Whether a urlopen failure is a deadline, not a transport error.

    ``urlopen`` reports a timeout two ways: it raises the deadline directly (on 3.10+
    ``socket.timeout`` *is* ``TimeoutError``, so one except clause covers both spellings),
    or it wraps it in a ``URLError`` whose ``reason`` is the ``TimeoutError``. Both have
    to land on :class:`~media_ai.core.errors.ErrorCategory.TIMEOUT`: a deadline exit code
    (7) tells a caller to raise the timeout or shrink the input, while ``provider`` (6)
    sends them looking for an upstream outage that isn't there.
    """
    return isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)


def _open(req: urllib.request.Request, timeout: float):
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
    except urllib.error.HTTPError as e:
        raise _raise(e) from None
    except (urllib.error.URLError, TimeoutError) as e:
        timed_out = _timed_out(e)
        detail = f"timed out after {timeout:g}s" if timed_out else f"failed: {redact(str(e))}"
        raise MediaError(
            f"Gemini Files API request {detail}",
            category=ErrorCategory.TIMEOUT if timed_out else ErrorCategory.PROVIDER, provider="gemini",
        ) from None


def upload_bytes(base_url: str, auth_headers: dict, data: bytes, mime: str, *,
                 display_name: str = "input", timeout: float = 300.0, poll_timeout: float = 120.0) -> str:
    """Upload ``data`` via the resumable protocol and return the ACTIVE file URI."""
    start = urllib.request.Request(
        _files_endpoint(base_url), method="POST",
        data=json.dumps({"file": {"display_name": display_name}}).encode(),
        headers={**auth_headers, "Content-Type": "application/json",
                 "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                 "X-Goog-Upload-Header-Content-Length": str(len(data)),
                 "X-Goog-Upload-Header-Content-Type": mime})
    with _open(start, timeout) as resp:
        upload_url = resp.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise MediaError("Gemini Files API did not return an upload URL", category=ErrorCategory.PROVIDER, provider="gemini")

    finalize = urllib.request.Request(
        upload_url, data=data, method="POST",
        headers={"Content-Length": str(len(data)), "X-Goog-Upload-Offset": "0",
                 "X-Goog-Upload-Command": "upload, finalize"})
    with _open(finalize, timeout) as resp:
        file = (json.loads(resp.read() or b"{}") or {}).get("file", {})

    uri, name, state = file.get("uri"), file.get("name"), file.get("state")
    if not uri:
        raise MediaError("Gemini Files API upload returned no file uri", category=ErrorCategory.PROVIDER, provider="gemini")
    if state and state != "ACTIVE" and name:
        uri = _await_active(base_url, auth_headers, name, uri, poll_timeout)
    return uri


def _await_active(base_url: str, auth_headers: dict, name: str, uri: str, poll_timeout: float) -> str:
    url = f"{base_url.rstrip('/')}/{name}"  # name is like "files/abc"
    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        with _open(urllib.request.Request(url, method="GET", headers=auth_headers), 60.0) as resp:
            f = json.loads(resp.read() or b"{}") or {}
        state = f.get("state")
        if state == "ACTIVE":
            return f.get("uri", uri)
        if state == "FAILED":
            raise MediaError(f"Gemini Files API failed to process {name}", category=ErrorCategory.PROVIDER, provider="gemini")
        time.sleep(2)
    raise MediaError(f"Gemini Files API file {name} not ACTIVE after {poll_timeout:g}s",
                     category=ErrorCategory.TIMEOUT, provider="gemini")
