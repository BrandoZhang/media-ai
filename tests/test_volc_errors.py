"""Ark error-code classification (from the Volcengine Ark common-error-code table)."""

from __future__ import annotations

import json

import pytest
from media_ai.core.errors import ErrorCategory
from media_ai.providers._volc_errors import parse_error_body, task_failure_error, to_media_error

# (code, http status) -> (expected category, expected retryable)
CASES = [
    ("SensitiveContentDetected", 400, ErrorCategory.SAFETY, False),
    ("OutputVideoSensitiveContentDetected", 400, ErrorCategory.SAFETY, False),
    ("InputImageSensitiveContentDetected.PolicyViolation", 400, ErrorCategory.SAFETY, False),
    ("InputTextRiskDetection", 400, ErrorCategory.SAFETY, False),
    ("ContentSecurityDetectionError", 400, ErrorCategory.PROVIDER, True),
    ("AuthenticationError", 401, ErrorCategory.AUTH, False),
    ("AccountOverdueError", 403, ErrorCategory.AUTH, False),
    ("OperationDenied.ServiceOverdue", 403, ErrorCategory.AUTH, False),
    ("ModelNotOpen", 404, ErrorCategory.NOT_FOUND, False),
    ("InvalidEndpointOrModel.NotFound", 404, ErrorCategory.NOT_FOUND, False),
    ("OperationDenied.ServiceNotOpen", 403, ErrorCategory.NOT_FOUND, False),
    ("InvalidEndpoint.ClosedEndpoint", 400, ErrorCategory.PROVIDER, True),
    ("RateLimitExceeded.EndpointRPMExceeded", 429, ErrorCategory.RATE_LIMIT, True),
    ("ServerOverloaded", 429, ErrorCategory.RATE_LIMIT, True),
    ("QuotaExceeded", 429, ErrorCategory.RATE_LIMIT, False),      # hard cap, not transient
    ("SetLimitExceeded", 429, ErrorCategory.RATE_LIMIT, False),
    ("InternalServiceError", 500, ErrorCategory.PROVIDER, True),
    ("MissingParameter", 400, ErrorCategory.VALIDATION, False),
    ("InvalidParameter.size", 400, ErrorCategory.VALIDATION, False),
    ("AccessDenied", 403, ErrorCategory.AUTH, False),
]


@pytest.mark.parametrize("code,status,category,retryable", CASES, ids=[c[0] for c in CASES])
def test_http_error_classification(code, status, category, retryable):
    body = json.dumps({"error": {"code": code, "message": "some detail", "request_id": "req-1"}})
    err = to_media_error(status, body, "volc")
    assert err.category == category
    assert err.retryable is retryable
    assert err.code == code                      # Ark code preserved for the agent
    assert err.details["request_id"] == "req-1"
    assert code in err.message and "some detail" in err.message


def test_model_not_open_has_actionable_hint():
    body = json.dumps({"error": {"code": "ModelNotOpen", "message": "not activated"}})
    err = to_media_error(404, body, "volc")
    assert err.category == ErrorCategory.NOT_FOUND and err.exit_code == 9
    assert "console" in err.message.lower()


def test_overdue_has_billing_hint_and_is_not_auth_key_error():
    err = to_media_error(403, json.dumps({"error": {"code": "AccountOverdueError", "message": "overdue"}}), "volc")
    assert err.category == ErrorCategory.AUTH and err.retryable is False
    assert "overdue" in err.message.lower() or "recharge" in err.message.lower()


def test_quota_exceeded_is_not_retryable_even_on_429():
    err = to_media_error(429, json.dumps({"error": {"code": "QuotaExceeded", "message": "used up"}}), "volc")
    assert err.category == ErrorCategory.RATE_LIMIT and err.retryable is False


def test_parse_error_body_shapes():
    assert parse_error_body('{"error":{"code":"X","message":"m","request_id":"r"}}') == ("X", "m", "r")
    assert parse_error_body('{"error":"just a string"}')[1] == "just a string"
    assert parse_error_body('{"code":"Y","message":"n"}')[:2] == ("Y", "n")
    assert parse_error_body("not json at all") == (None, "not json at all", None)


def test_plaintext_safety_fallback_without_code():
    # a body with no JSON code but a safety-sounding message still maps to SAFETY
    err = to_media_error(400, "The request failed because the input text may contain sensitive information.", "volc")
    assert err.category == ErrorCategory.SAFETY


def test_unknown_code_falls_back_to_status():
    assert to_media_error(401, json.dumps({"error": {"code": "SomethingNew"}}), "volc").category == ErrorCategory.AUTH
    assert to_media_error(503, "gateway boom", "volc").category == ErrorCategory.PROVIDER


# --- async video task failures ---


def test_task_failure_output_safety_is_safety():
    res = {"status": "failed", "error": {"code": "OutputVideoSensitiveContentDetected", "message": "blocked"}}
    err = task_failure_error(res, "volc", "task-1")
    assert err.category == ErrorCategory.SAFETY and err.exit_code == 8
    assert "OutputVideoSensitiveContentDetected" in err.message and err.details["task_id"] == "task-1"


def test_task_failure_no_error_object_maps_by_status():
    assert task_failure_error({"status": "failed"}, "volc", "t").category == ErrorCategory.PROVIDER
    assert task_failure_error({"status": "expired"}, "volc", "t").category == ErrorCategory.NOT_FOUND


def test_task_failure_internal_error_is_retryable_provider():
    res = {"status": "failed", "error": {"code": "InternalServiceError", "message": "boom"}}
    err = task_failure_error(res, "volc", "t")
    assert err.category == ErrorCategory.PROVIDER and err.retryable is True
