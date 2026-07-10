"""Provider + model registry.

Resolves ``(--provider, --model)`` (or a bare model id) to a constructed adapter
and a concrete model. Providers are imported lazily so the CLI only loads the
adapter it actually uses. A model id can imply its provider (e.g. ``gpt-image-2``
→ openai, ``veo-3.1-generate-preview`` → gemini) so ``--model`` alone is enough.
"""

from __future__ import annotations

import os

from ..credentials.resolver import CredentialProvider
from .errors import ErrorCategory, MediaError
from .provider import Provider
from .types import Modality

PROVIDER_NAMES = ("mock", "volc", "openai", "gemini")

# Substrings that imply a provider from a model id alone.
_MODEL_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("volc", ("doubao", "seedream", "seedance")),
    ("openai", ("gpt-image", "dall-e", "sora")),
    ("gemini", ("gemini-", "imagen-", "veo-")),
    ("mock", ("mock",)),
)


def provider_for_model(model: str | None) -> str | None:
    if not model:
        return None
    m = model.lower()
    for name, hints in _MODEL_HINTS:
        if any(h in m for h in hints):
            return name
    return None


def _construct(name: str, credentials: CredentialProvider | None, config: dict | None) -> Provider:
    if name == "mock":
        from ..providers.mock import MockProvider

        return MockProvider(credentials=credentials, config=config)
    if name == "volc":
        from ..providers.volc import VolcProvider

        return VolcProvider(credentials=credentials, config=config)
    if name == "openai":
        from ..providers.openai import OpenAIProvider

        return OpenAIProvider(credentials=credentials, config=config)
    if name == "gemini":
        from ..providers.gemini import GeminiProvider

        return GeminiProvider(credentials=credentials, config=config)
    raise MediaError(
        f"unknown provider {name!r}; expected one of {', '.join(PROVIDER_NAMES)}",
        category=ErrorCategory.CLI,
    )


def default_provider_name() -> str:
    return os.getenv("MEDIA_PROVIDER") or os.getenv("MEDIA_BACKEND") or "mock"


def build(
    provider: str | None = None,
    model: str | None = None,
    modality: Modality | None = None,
    *,
    credentials: CredentialProvider | None = None,
    config: dict | None = None,
) -> tuple[Provider, str | None]:
    """Return ``(provider_instance, resolved_model_id)``.

    Provider precedence: explicit ``--provider`` → inferred from ``--model`` →
    ``$MEDIA_PROVIDER`` → ``mock``. Model precedence: explicit ``--model`` → the
    provider's default for the modality.
    """
    name = (provider or provider_for_model(model) or default_provider_name()).lower()
    inst = _construct(name, credentials, config)
    resolved = model or (inst.default_model(modality) if modality else None)
    return inst, resolved


def get_provider(name: str, *, credentials: CredentialProvider | None = None, config: dict | None = None) -> Provider:
    return _construct(name.lower(), credentials, config)
