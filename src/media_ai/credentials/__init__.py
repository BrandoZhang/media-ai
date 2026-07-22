"""Credential handling: reveal-only :class:`Secret` handles, a resolver chain, and
a redaction filter. Designed so a raw provider key never reaches argv, logs,
generated metadata, or model-visible context.
"""

from .redaction import redact, redact_obj, register_secret
from .resolver import ChainCredentialProvider, CredentialProvider, default_chain
from .secret import BrokeredHandle, Credential, Secret

__all__ = [
    "Secret",
    "BrokeredHandle",
    "Credential",
    "CredentialProvider",
    "ChainCredentialProvider",
    "default_chain",
    "redact",
    "redact_obj",
    "register_secret",
]
