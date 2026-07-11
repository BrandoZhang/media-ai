"""Error taxonomy shared by every provider adapter and the CLI.

A single :class:`MediaError` carries a *category* (auth vs rate-limit vs
validation vs …). The category maps deterministically to a process **exit code**
so an Agent Skill can branch on failure kind without parsing text, and to a
``retryable`` hint so a caller knows whether a retry could ever succeed.

Provider adapters translate their native HTTP errors into this taxonomy (see each
provider's ``_error`` mapper); nothing above the adapter layer inspects raw HTTP.
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    """Kind of failure. The string value appears in the machine-readable error."""

    CLI = "cli"  # bad invocation / argparse (exit 2)
    VALIDATION = "validation"  # request malformed for the model (won't retry-succeed)
    UNSUPPORTED = "unsupported"  # option/operation not supported by the model
    AUTH = "auth"  # missing/invalid credentials
    RATE_LIMIT = "rate_limit"  # 429 / quota (retryable)
    PROVIDER = "provider"  # upstream 5xx / unexpected response (maybe retryable)
    TIMEOUT = "timeout"  # polling / request deadline exceeded
    SAFETY = "safety"  # content moderation / safety block
    NOT_FOUND = "not_found"  # unknown model / job / file
    IO = "io"  # local filesystem / artifact error
    UNKNOWN = "unknown"


# Category -> process exit code. Distinct codes let a Skill switch on $? alone.
EXIT_CODES: dict[ErrorCategory, int] = {
    ErrorCategory.CLI: 2,
    ErrorCategory.VALIDATION: 3,
    ErrorCategory.UNSUPPORTED: 3,
    ErrorCategory.AUTH: 4,
    ErrorCategory.RATE_LIMIT: 5,
    ErrorCategory.PROVIDER: 6,
    ErrorCategory.TIMEOUT: 7,
    ErrorCategory.SAFETY: 8,
    ErrorCategory.NOT_FOUND: 9,
    ErrorCategory.IO: 1,
    ErrorCategory.UNKNOWN: 1,
}

# Categories where an identical retry could plausibly succeed later.
_RETRYABLE = frozenset({ErrorCategory.RATE_LIMIT, ErrorCategory.TIMEOUT, ErrorCategory.PROVIDER})


class MediaError(RuntimeError):
    """Any recoverable failure. Carries a category, exit code and retry hint."""

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        code: str | None = None,
        retryable: bool | None = None,
        provider: str | None = None,
        model: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.category = category
        # `code` is a finer-grained, provider-stable slug (defaults to the category).
        self.code = code or category.value
        self.retryable = (category in _RETRYABLE) if retryable is None else retryable
        self.provider = provider
        self.model = model
        self.details = details or {}

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.category, 1)

    def to_dict(self) -> dict:
        """The ``error`` object embedded in the CLI's failure JSON."""
        return {
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "provider": self.provider,
            "model": self.model,
            "details": self.details,
        }
