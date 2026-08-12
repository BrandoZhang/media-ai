"""Transport-agnostic retry with exponential backoff.

The HTTP client has its own HTTP-tuned retry (Retry-After, 429-vs-5xx idempotency
rules). This helper is for **non-HTTP** providers (gRPC, JSON-RPC, an SDK, a
subprocess, a message queue) so they get idempotency-aware retry without depending
on ``providers/_http``. The caller supplies a ``retryable`` predicate that encodes
its own transport's transient-error rules (and idempotency).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry(
    fn: Callable[[], T],
    *,
    retryable: Callable[[BaseException], bool],
    attempts: int = 4,
    base: float = 2.0,
    cap: float = 30.0,
) -> T:
    """Call ``fn()`` up to ``attempts+1`` times with exponential backoff + jitter.

    ``retryable(exc)`` decides whether a raised exception is worth retrying — put
    your idempotency rule there (e.g. only retry non-mutating RPCs on a transient
    status). A non-retryable exception, or the final attempt, propagates unchanged.
    """
    for attempt in range(attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised unless retryable
            if attempt >= attempts or not retryable(exc):
                raise
            time.sleep(min(cap, base * (2**attempt)) + random.uniform(0, 0.5))
    raise AssertionError("unreachable")  # pragma: no cover
