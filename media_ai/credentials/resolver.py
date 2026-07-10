"""The credential provider chain.

Resolvers are tried in order, most-secure first; the first hit wins:
broker → secret-manager reference → OS keychain → config file → environment.
Credentials are re-resolved **per invocation** (no process-global plaintext
cache) so rotation and short-lived/broker tokens are picked up automatically.
"""

from __future__ import annotations

from ..core.errors import ErrorCategory, MediaError
from .secret import Credential
from .stores import (
    broker_resolver,
    env_resolver,
    file_resolver,
    keychain_resolver,
    secret_manager_resolver,
)


class CredentialProvider:
    """Resolves a provider name to a :class:`Credential`. Subclass to plug in a
    custom source (e.g. a bespoke broker or vault)."""

    def resolve(self, provider: str) -> Credential:  # pragma: no cover - interface
        raise NotImplementedError


class ChainCredentialProvider(CredentialProvider):
    def __init__(self, resolvers) -> None:
        self._resolvers = list(resolvers)

    def resolve(self, provider: str) -> Credential:
        for r in self._resolvers:
            cred = r(provider)
            if cred is not None:
                return cred
        raise MediaError(
            f"no credential found for provider {provider!r}. Set an env var "
            f"(e.g. {_hint(provider)}), a config file, keychain entry, or a broker.",
            category=ErrorCategory.AUTH,
            provider=provider,
        )


def _hint(provider: str) -> str:
    from .stores import ENV_VARS

    return " / ".join(ENV_VARS.get(provider, (f"{provider.upper()}_API_KEY",)))


# Order matters: most-secure / most-explicit first.
_DEFAULT_RESOLVERS = (
    broker_resolver,
    secret_manager_resolver,
    keychain_resolver,
    file_resolver,
    env_resolver,
)


def default_chain() -> ChainCredentialProvider:
    return ChainCredentialProvider(_DEFAULT_RESOLVERS)
