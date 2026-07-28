"""Credential handling: reveal-only :class:`Secret` handles, explicit references, and
a redaction filter. Designed so a raw provider key never reaches argv, logs,
generated metadata, or model-visible context.
"""

from .redaction import redact, redact_obj, register_secret
from .reference import BindingCredentials, is_reference, resolve_reference
from .secret import BrokeredHandle, Credential, Secret
from .stores import register_secret_backend

__all__ = [
    "Secret",
    "BrokeredHandle",
    "Credential",
    "BindingCredentials",
    "is_reference",
    "resolve_reference",
    "register_secret_backend",
    "redact",
    "redact_obj",
    "register_secret",
]
