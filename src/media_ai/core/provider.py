"""The internal :class:`Provider` interface every adapter implements.

The CLI never touches credentials: it names a binding; the registry
constructs the adapter with the binding's :class:`BindingCredentials`; the adapter
resolves its :class:`Credential` lazily (only when a call actually needs the
network) and reveals the value only inside its HTTP request builder.
"""

from __future__ import annotations

from pathlib import Path

from ..credentials.reference import BindingCredentials
from ..credentials.secret import Credential
from .capabilities import ModelCapabilities
from .errors import ErrorCategory, MediaError
from .result import GenerationResult, JobHandle, JobStatus
from .types import (
    DialogueRequest,
    ImageRequest,
    JobRef,
    Modality,
    MusicPlanRequest,
    MusicRequest,
    SoundEffectRequest,
    SpeechRequest,
    VideoRequest,
)


class Provider:
    """Base class for a generation backend. Adapters override the operations they
    support and declare capabilities per model; unsupported operations raise a
    deterministic ``UNSUPPORTED`` error."""

    name: str = "base"
    requires_credentials: bool = True
    catalog = None
    """Optional :class:`~media_ai.core.modelspec.Catalog` describing this provider's
    models. When set, ``all_models()`` can report retired and undiscoverable ids that
    ``models()`` deliberately withholds."""
    # Lowercase substrings that route a bare ``--model`` id to this provider when
    # it is discovered via an entry point (see media_ai.core.registry).
    model_hints: tuple[str, ...] = ()

    def __init__(self, *, credentials: BindingCredentials | None = None, config: dict | None = None) -> None:
        # No default chain to fall back on: an adapter is constructed from a binding,
        # and the binding names its credential. One built without one can still run
        # whatever needs no key (the local and mock backends).
        self._credentials = credentials or BindingCredentials(None, provider=self.name)
        self.config = config or {}

    # ---- credentials -----------------------------------------------------
    def credential(self) -> Credential:
        """Resolve this provider's credential (re-resolved per call for rotation)."""
        return self._credentials.resolve(self.name)

    # ---- model aliases ---------------------------------------------------
    def backing_model(self, model: str | None) -> str | None:
        """The real model behind an opaque deployment id, per ``[providers.<name>.endpoints]``.

        Some backends address a model by an id that names a *deployment* rather than
        the model itself — Ark custom inference endpoints (``ep-2026…-zrbtw``) are the
        motivating case, and Azure-style deployments behave the same way. Such an id
        carries no capability information, so without a mapping the CLI has to fail
        open and let the API be the authority.

        Users map the id to the model it actually serves::

            [providers.volc.endpoints]
            "ep-2026…-img" = "doubao-seedream-4-5-251128"

        and everything the CLI knows about that model — operations, geometry limits,
        options — applies to the endpoint. Capability lookups resolve through this;
        **the wire request must keep using the original id**, which is the only name
        the API accepts.

        Returns ``model`` unchanged when no mapping exists, preserving today's
        fail-open behaviour.
        """
        if not model:
            return model
        endpoints = self.config.get("endpoints")
        if not isinstance(endpoints, dict):
            return model
        target = endpoints.get(model)
        return target if isinstance(target, str) and target else model

    # ---- discovery -------------------------------------------------------
    def models(self) -> list[str]:  # pragma: no cover - interface
        raise NotImplementedError

    def all_models(self) -> list[str]:
        """Every model this adapter knows, including deprecated and removed ones.

        ``models()`` answers "what should I use?"; this answers "what do you know
        about?" — the question you need to plan a migration off something retired.
        Always a superset of ``models()``. That is not automatic: Volc's discovery
        comes from config/env (an ``ep-…`` endpoint the user enabled), not from the
        catalogue, so returning catalogue ids alone would *drop* the very models that
        account can actually call and list two it never enabled.
        """
        listed = self.models()
        if self.catalog is None:
            return listed
        known = self.catalog.real_ids()
        return listed + [m for m in known if m not in listed]

    def default_model(self, modality: Modality) -> str | None:  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model: str | None = None, modality: Modality | None = None) -> ModelCapabilities:  # pragma: no cover
        # ``modality`` is the modality the caller is about to use (from the
        # command, e.g. ``video generate``). Adapters whose model ids don't encode
        # modality (e.g. Ark endpoint ids ``ep-…``) should trust it rather than
        # guessing from the name; name-encoded providers may ignore it.
        raise NotImplementedError

    def all_capabilities(self) -> list[ModelCapabilities]:
        return [self.capabilities(m) for m in self.models()]

    # ---- operations (override the supported ones) ------------------------
    def generate_image(self, req: ImageRequest) -> GenerationResult:
        raise self._unsupported("image generation")

    def generate_video(self, req: VideoRequest) -> GenerationResult | JobHandle:
        raise self._unsupported("video generation")

    def generate_speech(self, req: SpeechRequest) -> GenerationResult:
        raise self._unsupported("speech generation")

    def generate_dialogue(self, req: DialogueRequest) -> GenerationResult:
        raise self._unsupported("dialogue generation")

    def generate_music(self, req: MusicRequest) -> GenerationResult:
        raise self._unsupported("music generation")

    def generate_music_plan(self, req: MusicPlanRequest) -> GenerationResult:
        raise self._unsupported("composition-plan generation")

    def generate_sound(self, req: SoundEffectRequest) -> GenerationResult:
        raise self._unsupported("sound-effect generation")

    def get_job(self, ref: JobRef, *, output: Path | None = None) -> JobStatus:
        raise self._unsupported("async jobs")

    def cancel_job(self, ref: JobRef) -> JobStatus:
        raise self._unsupported("job cancellation")

    def _unsupported(self, what: str) -> MediaError:
        return MediaError(
            f"provider {self.name!r} does not support {what}",
            category=ErrorCategory.UNSUPPORTED,
            provider=self.name,
        )
