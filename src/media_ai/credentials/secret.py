"""Reveal-only credential handles.

A :class:`Secret` wraps a plaintext value that is accessible **only** via
``.reveal()`` — used at the last possible moment inside a provider's HTTP request
builder. Its ``repr``/``str``/pickle all render ``***`` (or a source descriptor),
so accidental interpolation into an f-string, log line, or JSON dump cannot leak
the value. The value is also registered with the redactor so it is masked even if
it reaches a sink by some other path.

A :class:`BrokeredHandle` carries **no** secret at all — only a session token and
a broker endpoint. Adapters route brokered requests through the broker instead of
setting a local header, so the CLI process never holds the provider key.
"""

from __future__ import annotations

from .redaction import register_secret


class Credential:
    """Common base so adapters can accept either a real secret or a broker handle."""

    provider: str
    source: str


class Secret(Credential):
    """An in-memory credential value, accessible only through :meth:`reveal`."""

    __slots__ = ("_value", "provider", "source", "expiry")

    def __init__(self, value: str, *, provider: str, source: str, expiry: float | None = None) -> None:
        self._value = value
        self.provider = provider
        self.source = source
        self.expiry = expiry  # epoch seconds; None = no known expiry
        register_secret(value)

    def reveal(self) -> str:
        return self._value

    def is_expired(self, now: float) -> bool:
        return self.expiry is not None and now >= self.expiry

    # --- leak guards: never render the value -------------------------------
    def __repr__(self) -> str:
        return f"Secret(provider={self.provider!r}, source={self.source!r})"

    def __str__(self) -> str:
        return "***"

    def __reduce__(self):  # pickling yields the mask, never the value
        return (str, ("***",))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash((self.provider, self.source))


class BrokeredHandle(Credential):
    """A pointer to a broker that injects the real credential at egress. Holds no
    secret; adapters send their request through ``endpoint`` with ``token``."""

    __slots__ = ("provider", "source", "endpoint", "token", "expiry")

    def __init__(self, *, provider: str, endpoint: str, token: str, expiry: float | None = None) -> None:
        self.provider = provider
        self.source = "broker"
        self.endpoint = endpoint
        self.token = token
        self.expiry = expiry
        register_secret(token)

    def reveal(self) -> str:  # a broker handle intentionally has no revealable key
        from ..core.errors import ErrorCategory, MediaError

        raise MediaError(
            "brokered credential has no local value; the request must be routed through the broker",
            category=ErrorCategory.AUTH,
            provider=self.provider,
        )

    def __repr__(self) -> str:
        return f"BrokeredHandle(provider={self.provider!r}, endpoint={self.endpoint!r})"

    def __str__(self) -> str:
        return "***"

    def __reduce__(self):
        return (str, ("***",))
