"""Provider + model registry — the extension point for custom providers.

Resolves ``(--provider, --model)`` (or a bare model id) to a constructed adapter.
Providers are looked up in a dynamic registry rather than a hardcoded table, so a
third party can add a backend **without editing this file**, two ways:

1. **In-process** — call :func:`register_provider` (e.g. at import time)::

       from media_ai import register_provider, Provider
       register_provider("acme", lambda **kw: AcmeProvider(**kw),
                         model_hints=("acme-",))

2. **As an installed package** — expose an entry point in the
   ``media_ai.providers`` group pointing at a :class:`Provider` subclass::

       # pyproject.toml of the plugin package
       [project.entry-points."media_ai.providers"]
       acme = "acme_media:AcmeProvider"

   The entry-point *name* becomes the provider name; the class's ``model_hints``
   attribute routes bare ``--model`` ids to it. Entry points are discovered lazily
   and a broken plugin is skipped (logged) rather than breaking the whole CLI.

Built-in providers (mock/volc/openai/gemini) are registered the same way, with
factory closures that import the adapter lazily so the CLI only loads what it uses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from ..credentials.profile import Profile, ProfileCredentialProvider, load_profile
from ..credentials.resolver import CredentialProvider, default_chain
from .errors import ErrorCategory, MediaError
from .provider import Provider
from .types import Modality

# A factory builds a provider: factory(credentials=..., config=...) -> Provider.
ProviderFactory = Callable[..., Provider]


@dataclass
class ProviderSpec:
    name: str
    factory: ProviderFactory
    model_hints: tuple[str, ...] = ()


_REGISTRY: dict[str, ProviderSpec] = {}
_BUILTINS_LOADED = False
_ENTRYPOINTS_LOADED = False


# --------------------------------------------------------------------------
# public registration API
# --------------------------------------------------------------------------


def register_provider(name: str, factory: ProviderFactory, *, model_hints: tuple[str, ...] = ()) -> str:
    """Register (or replace) a provider adapter under ``name``.

    ``factory(credentials=None, config=None)`` must return a :class:`Provider`.
    ``model_hints`` are lowercase substrings that route a bare ``--model`` id to
    this provider (so ``--model`` alone selects it). Idempotent; last wins.
    """
    key = name.lower()
    _REGISTRY[key] = ProviderSpec(key, factory, tuple(h.lower() for h in model_hints))
    return key


def unregister_provider(name: str) -> None:
    _REGISTRY.pop(name.lower(), None)


def is_registered(name: str) -> bool:
    _ensure_loaded()
    return name.lower() in _REGISTRY


def provider_names() -> list[str]:
    """All registered provider names (built-ins + plugins), sorted."""
    _ensure_loaded()
    return sorted(_REGISTRY)


# Backwards-compatible alias (was a static tuple). Prefer ``provider_names()``.
def __getattr__(name):  # module-level dynamic attribute
    if name == "PROVIDER_NAMES":
        return tuple(provider_names())
    raise AttributeError(name)


# --------------------------------------------------------------------------
# discovery / loading
# --------------------------------------------------------------------------


def _ensure_loaded() -> None:
    _register_builtins()
    _load_entry_points()


def _register_builtins() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True

    def _mock(**kw):
        from ..providers.mock import MockProvider

        return MockProvider(**kw)

    def _volc(**kw):
        from ..providers.volc import VolcProvider

        return VolcProvider(**kw)

    def _openai(**kw):
        from ..providers.openai import OpenAIProvider

        return OpenAIProvider(**kw)

    def _gemini(**kw):
        from ..providers.gemini import GeminiProvider

        return GeminiProvider(**kw)

    def _elevenlabs(**kw):
        from ..providers.elevenlabs import ElevenLabsProvider

        return ElevenLabsProvider(**kw)

    register_provider("mock", _mock, model_hints=("mock",))
    register_provider("volc", _volc, model_hints=("doubao", "seedream", "seedance"))
    # `dall-e`/`sora` route here so a request for a (dropped) DALL·E or the
    # unsupported Sora model gets a clear error instead of silently falling back to
    # mock — see OpenAIProvider._is_dalle / its no-video handling. Likewise `imagen-`
    # routes to gemini for a clear "use Nano Banana" removal error.
    register_provider("openai", _openai, model_hints=("gpt-image", "dall-e", "sora"))
    register_provider("gemini", _gemini, model_hints=("gemini-", "imagen-", "veo-"))
    register_provider("elevenlabs", _elevenlabs, model_hints=("eleven_", "eleven-"))


def _load_entry_points() -> None:
    global _ENTRYPOINTS_LOADED
    if _ENTRYPOINTS_LOADED:
        return
    _ENTRYPOINTS_LOADED = True
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present on 3.11
        return
    try:
        eps = entry_points(group="media_ai.providers")
    except TypeError:  # pragma: no cover - very old API
        eps = entry_points().get("media_ai.providers", [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            obj = ep.load()
            _register_entry_point(ep.name, obj)
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not break the CLI
            from .logging import get_logger

            get_logger().warning("skipping provider plugin %r: %s", getattr(ep, "name", "?"), exc)


def _register_entry_point(name: str, obj) -> None:
    """An entry point resolves to a Provider subclass (preferred) or a factory."""
    if isinstance(obj, type) and issubclass(obj, Provider):
        hints = tuple(getattr(obj, "model_hints", ()) or ())
        register_provider(name, lambda **kw: obj(**kw), model_hints=hints)
    elif callable(obj):
        register_provider(name, obj, model_hints=tuple(getattr(obj, "model_hints", ()) or ()))
    else:
        raise TypeError(f"entry point {name!r} is not a Provider subclass or factory")


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


def provider_for_model(model: str | None) -> str | None:
    if not model:
        return None
    _ensure_loaded()
    m = model.lower()
    for spec in _REGISTRY.values():
        if any(h in m for h in spec.model_hints):
            return spec.name
    return None


def default_provider_name() -> str:
    return os.getenv("MEDIA_PROVIDER") or os.getenv("MEDIA_BACKEND") or "mock"


def _resolve_profile(name: str | None) -> Profile | None:
    name = name or os.getenv("MEDIA_PROFILE")
    return load_profile(name) if name else None


def _profile_credentials(profile: Profile | None, credentials: CredentialProvider | None) -> CredentialProvider | None:
    if credentials is not None or profile is None:
        return credentials
    return ProfileCredentialProvider(profile, default_chain())


def _profile_config(profile: Profile | None, config: dict | None) -> dict | None:
    if profile is None or not profile.base_url:
        return config
    cfg = dict(config or {})
    cfg.setdefault("base_url", profile.base_url)
    return cfg


def _construct(name: str, credentials: CredentialProvider | None, config: dict | None) -> Provider:
    _ensure_loaded()
    spec = _REGISTRY.get(name.lower())
    if spec is None:
        raise MediaError(
            f"unknown provider {name!r}; registered: {', '.join(provider_names())}",
            category=ErrorCategory.CLI,
        )
    return spec.factory(credentials=credentials, config=config)


def build(
    provider: str | None = None,
    model: str | None = None,
    modality: Modality | None = None,
    *,
    profile: str | None = None,
    credentials: CredentialProvider | None = None,
    config: dict | None = None,
) -> tuple[Provider, str | None]:
    """Return ``(provider_instance, resolved_model_id)``.

    Provider precedence: explicit ``--provider`` → profile → inferred from
    ``--model`` → ``$MEDIA_PROVIDER`` → ``mock``. Model precedence: explicit
    ``--model`` → profile → the provider's default for the modality. A profile
    (``--provider-profile`` / ``$MEDIA_PROFILE``) also binds the credential source
    and an optional base URL.
    """
    prof = _resolve_profile(profile)
    name = (provider or (prof.provider if prof else None) or provider_for_model(model) or default_provider_name()).lower()
    inst = _construct(name, _profile_credentials(prof, credentials), _profile_config(prof, config))
    resolved = model or (prof.model if prof else None) or (inst.default_model(modality) if modality else None)
    return inst, resolved


def get_provider(
    name: str | None = None,
    *,
    profile: str | None = None,
    credentials: CredentialProvider | None = None,
    config: dict | None = None,
) -> Provider:
    prof = _resolve_profile(profile)
    name = name or (prof.provider if prof else None) or default_provider_name()
    return _construct(name.lower(), _profile_credentials(prof, credentials), _profile_config(prof, config))
