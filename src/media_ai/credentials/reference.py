"""Credential references — one binding, one explicit source.

A binding says where its key comes from and there is no fallback::

    credential = "env://ARK_API_KEY"
    credential = "cred://volc-ark/seedance-2.0"     # an account in credentials.toml
    credential = "keychain://media-ai/openai"
    credential = "op://vault/item/field"            # pluggable backends
    credential = "broker://"                        # the process holds no key at all

This replaces a five-layer precedence chain (broker → secret manager → keychain →
config file → environment, first hit wins). The chain was convenient and answered
the wrong question: *"where did this key come from?"* required knowing the order and
inspecting five places, and a key present in two of them silently resolved to one.
Every capability the chain had survives as a scheme; what is gone is the implicit
precedence between them.

The trust boundary is unchanged. A reference is not a secret, so it lives in the
shareable ``config.toml``; the value materializes as a reveal-only
:class:`~media_ai.credentials.secret.Secret` inside the adapter's request builder
and is registered with the redactor on creation.
"""

from __future__ import annotations

import os

from ..core.errors import ErrorCategory, MediaError
from .secret import BrokeredHandle, Credential, Secret
from .stores import named_account, register_secret_backend, secret_backend

__all__ = ["is_reference", "resolve_reference", "split_reference", "BindingCredentials"]

#: Schemes resolved in-process. Anything else needs a registered backend.
_BUILTIN = ("env", "cred", "keychain", "broker")


def is_reference(value: str) -> bool:
    """Whether ``value`` names a source rather than *being* a key.

    Used to refuse a raw key written into the shareable config file. Both forms a
    backend may use are accepted: ``scheme://rest`` and the bare ``scheme:rest`` that
    ARNs take (``arn:aws:secretsmanager:…``).
    """
    return "://" in value or (":" in value and value.split(":", 1)[0].isalnum())


def split_reference(ref: str) -> tuple[str, str]:
    """``"cred://volc"`` → ``("cred", "volc")``; also splits the bare ``scheme:rest``.

    Public because a caller may need to know *which* source a binding names without
    resolving it — deciding which accounts a bundle has to carry, for one.
    """
    scheme, sep, rest = ref.partition("://")
    if sep:
        return scheme, rest
    scheme, _, rest = ref.partition(":")
    return scheme, rest


def resolve_reference(ref: str, *, provider: str = "") -> Credential:
    """Resolve one reference to a :class:`Credential`, or raise.

    Raising is the point. A reference that does not resolve is a configuration
    error with an obvious fix, and the alternative — quietly trying somewhere else —
    is what made "which key did this call use?" unanswerable.
    """
    if not ref:
        raise MediaError("no credential reference to resolve", category=ErrorCategory.AUTH, provider=provider)
    scheme, rest = split_reference(ref)

    if scheme == "broker":
        endpoint = rest or os.getenv("MEDIA_CRED_BROKER", "")
        if not endpoint:
            raise MediaError(
                "credential is 'broker://' but no broker endpoint is set; "
                "use broker://<host> or export MEDIA_CRED_BROKER",
                category=ErrorCategory.AUTH, code="credential_unresolved", provider=provider,
            )
        return BrokeredHandle(provider=provider, endpoint=endpoint, token=os.getenv("MEDIA_CRED_BROKER_TOKEN", ""))

    return Secret(_reveal(scheme, rest, ref, provider), provider=provider, source=scheme)


def _reveal(scheme: str, rest: str, ref: str, provider: str) -> str:
    if scheme == "env":
        value = os.getenv(rest, "")
        if not value:
            raise _unresolved(ref, f"environment variable {rest} is unset or empty", provider)
        return value

    if scheme == "cred":
        value = named_account(rest)
        if not value:
            raise _unresolved(ref, f"no [{rest}] account in credentials.toml", provider)
        return value

    if scheme == "keychain":
        return _from_keychain(rest, ref, provider)

    backend = secret_backend(scheme)
    if backend is None:
        known = ", ".join(sorted({*_BUILTIN, *_registered()}))
        raise MediaError(
            f"credential {ref!r}: no backend for scheme {scheme!r} (known: {known}); "
            "register one with media_ai.credentials.stores.register_secret_backend",
            category=ErrorCategory.AUTH, code="credential_scheme_unknown", provider=provider,
        )
    value = backend(ref)
    if not value:
        raise _unresolved(ref, "the backend returned nothing", provider)
    return value


def _from_keychain(rest: str, ref: str, provider: str) -> str:
    try:
        import keyring  # type: ignore
    except ModuleNotFoundError:
        raise MediaError(
            f"credential {ref!r} needs the OS keychain; install the extra: pip install 'media-ai[keychain]'",
            category=ErrorCategory.AUTH, code="credential_backend_missing", provider=provider,
        ) from None
    service, _, account = rest.rpartition("/")
    service = service or "media-ai"
    try:
        value = keyring.get_password(service, account)
    except Exception as exc:  # noqa: BLE001 - a locked keychain is a resolution failure, not a crash
        raise _unresolved(ref, f"the keychain could not be read ({exc})", provider) from None
    if not value:
        raise _unresolved(ref, f"no {service}/{account} entry in the keychain", provider)
    return value


def _registered() -> tuple[str, ...]:
    from .stores import registered_schemes

    return registered_schemes()


def _unresolved(ref: str, why: str, provider: str) -> MediaError:
    return MediaError(
        f"credential {ref!r} did not resolve: {why}",
        category=ErrorCategory.AUTH, code="credential_unresolved", provider=provider,
    )


class BindingCredentials:
    """Supplies one binding's credential, resolved per call.

    Adapters ask for "my credential" without knowing where it lives; re-resolving on
    every invocation is what makes rotation and short-lived broker tokens work
    without restarting anything.
    """

    def __init__(self, reference: str | None, *, provider: str = "") -> None:
        self.reference = reference
        self.provider = provider

    def resolve(self, provider: str | None = None) -> Credential:
        if not self.reference:
            raise MediaError(
                f"binding for provider {self.provider or provider!r} has no credential configured",
                category=ErrorCategory.AUTH, code="credential_missing", provider=self.provider or provider,
            )
        return resolve_reference(self.reference, provider=self.provider or provider or "")


# Re-exported so callers touch one module. `register_secret_backend` is how a
# deployment plugs in 1Password, Vault or a bespoke vault without a fork.
__all__ += ["register_secret_backend"]
