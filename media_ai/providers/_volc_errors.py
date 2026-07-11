"""Volcengine Ark error classification.

Maps Ark's structured error ``code`` (and HTTP status) to the shared
:class:`MediaError` taxonomy, so tools get a deterministic ``category`` + exit
code and an actionable message instead of a raw body dump. Codes come from the
Ark common-error-code reference (BadRequest / Unauthorized / Forbidden / NotFound
/ TooManyRequests / InternalServerError). The same classifier handles both HTTP
errors and **async video task failures** (whose ``error`` object carries e.g.
``OutputVideoSensitiveContentDetected`` — an output-safety block that must surface
as ``SAFETY``, not a generic provider error).
"""

from __future__ import annotations

import json

from ..core.errors import ErrorCategory, MediaError

_HINT_MODEL = (
    "the model or endpoint may not exist or be enabled for your account — "
    "open it in the Volcengine Ark console"
)
_HINT_BILLING = (
    "account balance is overdue or the subscription is inactive — "
    "recharge/activate in the Volcengine console"
)
_HINT_QUOTA = (
    "a free-trial quota or a configured inference limit is exhausted — "
    "check the Ark console (retrying will not help)"
)

_SAFETY_MESSAGE_HINTS = ("sensitive", "risk", "violation", "copyright", "real person", "content policy")


def classify(code: str, status: int, message: str = "") -> tuple[ErrorCategory, bool | None, str | None]:
    """Return ``(category, retryable_override, hint)`` for an Ark error.

    ``retryable_override`` is ``None`` to defer to the category default; ``hint`` is
    an optional actionable suffix. Matching is case-insensitive on the code, with a
    message-keyword fallback for safety when no code is present.
    """
    c = (code or "").lower()

    # --- content safety (input or output), never retryable ---
    if "sensitivecontentdetected" in c or "riskdetection" in c or "sensitivecontent" in c:
        return ErrorCategory.SAFETY, False, None
    # CSD *service* failure (not a content rejection) is transient
    if "contentsecuritydetectionerror" in c:
        return ErrorCategory.PROVIDER, True, None

    # --- auth: bad/missing key ---
    if c == "authenticationerror":
        return ErrorCategory.AUTH, False, None
    # --- billing / account / subscription (non-retryable, needs action) ---
    if any(k in c for k in ("overdue", "invalidaccountstatus", "invalidsubscription")):
        return ErrorCategory.AUTH, False, _HINT_BILLING

    # --- endpoint temporarily closed: retry later ---
    if "closedendpoint" in c:
        return ErrorCategory.PROVIDER, True, None
    # --- model / endpoint not found or not enabled ---
    if (any(k in c for k in ("modelnotopen", "invalidendpointormodel", "servicenotopen", "unsupportedmodel"))
            or c.startswith("notfound") or status == 404):
        return ErrorCategory.NOT_FOUND, False, _HINT_MODEL

    # --- rate limit / quota (429 family) ---
    if "quotaexceeded" in c or "setlimitexceeded" in c:
        return ErrorCategory.RATE_LIMIT, False, _HINT_QUOTA  # hard cap, not transient
    if any(k in c for k in ("ratelimit", "rpm", "tpm", "ipm", "serveroverloaded",
                            "requestburst", "inflightbatchsize", "toomanyrequests")) or status == 429:
        return ErrorCategory.RATE_LIMIT, True, None

    # --- server error: transient ---
    if "internalservice" in c or "internalserver" in c or status >= 500:
        return ErrorCategory.PROVIDER, True, None

    # --- permission / operation denied ---
    if any(k in c for k in ("operationdenied", "accessdenied", "permissiondenied")):
        return ErrorCategory.AUTH, False, None

    # --- safety fallback when Ark returned no code but a descriptive message ---
    if not c and any(k in (message or "").lower() for k in _SAFETY_MESSAGE_HINTS):
        return ErrorCategory.SAFETY, False, None

    # --- fallback by HTTP status ---
    by_status = {400: ErrorCategory.VALIDATION, 401: ErrorCategory.AUTH, 403: ErrorCategory.AUTH,
                 404: ErrorCategory.NOT_FOUND, 429: ErrorCategory.RATE_LIMIT}
    return by_status.get(status, ErrorCategory.PROVIDER), None, None


def parse_error_body(body: str) -> tuple[str | None, str, str | None]:
    """Extract ``(code, message, request_id)`` from an Ark error body (tolerant of
    the OpenAI-compatible ``{"error": {...}}`` shape, a bare object, or plain text)."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None, body, None
    if not isinstance(data, dict):
        return None, str(body), None
    err = data.get("error")
    code = message = request_id = None
    if isinstance(err, dict):
        code = err.get("code") or err.get("type")
        message = err.get("message")
        request_id = err.get("request_id") or err.get("id")
    elif isinstance(err, str):
        message = err
    code = code or data.get("code")
    message = message or data.get("message")
    request_id = request_id or data.get("request_id") or data.get("id")
    return code, (message or body), request_id


def to_media_error(status: int, body: str, provider: str, model: str | None = None) -> MediaError:
    """Map an Ark HTTP error body to a categorized :class:`MediaError`."""
    code, message, request_id = parse_error_body(body)
    category, retryable, hint = classify(code or "", status, message)
    msg = f"Ark {code}: {message}" if code else f"Ark HTTP {status}: {message}"
    if hint:
        msg += f" — {hint}"
    details = {"status": status, "code": code, "request_id": request_id}
    return MediaError(msg, category=category, code=code, retryable=retryable, provider=provider,
                      model=model, details={k: v for k, v in details.items() if v is not None})


def task_failure_error(res: dict, provider: str, task_id: str) -> MediaError:
    """Map a terminal async video task result to a categorized :class:`MediaError`.

    A failed task carries its reason in ``res['error']`` (often an output-safety
    code); classify it the same way as an HTTP error. When there is no structured
    error, fall back to the terminal status.
    """
    status = str(res.get("status", "")).lower()
    err = res.get("error")
    code = message = request_id = None
    if isinstance(err, dict):
        code = err.get("code") or err.get("type")
        message = err.get("message")
        request_id = err.get("request_id") or err.get("id")
    elif isinstance(err, str):
        message = err

    if code or message:
        category, retryable, hint = classify(code or "", 400, message or "")
        label = f"[{code}] " if code else ""
        msg = f"Ark video task {task_id} failed: {label}{message or ''}".rstrip()
        if hint:
            msg += f" — {hint}"
    else:
        category, retryable = {
            "failed": (ErrorCategory.PROVIDER, True),
            "expired": (ErrorCategory.NOT_FOUND, False),
            "cancelled": (ErrorCategory.PROVIDER, False),
            "canceled": (ErrorCategory.PROVIDER, False),
        }.get(status, (ErrorCategory.PROVIDER, None))
        msg = f"Ark video task {task_id} {status or 'failed'}"
    details = {"task_id": task_id, "code": code, "status": status, "request_id": request_id}
    return MediaError(msg, category=category, code=code, retryable=retryable, provider=provider,
                      details={k: v for k, v in details.items() if v is not None})
