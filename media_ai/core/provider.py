"""The internal :class:`Provider` interface every adapter implements.

The CLI never touches credentials: it selects a provider by name; the registry
constructs the adapter with a bound :class:`CredentialProvider`; the adapter
resolves its :class:`Credential` lazily (only when a call actually needs the
network) and reveals the value only inside its HTTP request builder.
"""

from __future__ import annotations

from pathlib import Path

from ..credentials.resolver import CredentialProvider, default_chain
from ..credentials.secret import Credential
from .capabilities import ModelCapabilities
from .errors import ErrorCategory, MediaError
from .result import GenerationResult, JobHandle, JobStatus
from .types import ImageRequest, JobRef, Modality, VideoRequest


class Provider:
    """Base class for a generation backend. Adapters override the operations they
    support and declare capabilities per model; unsupported operations raise a
    deterministic ``UNSUPPORTED`` error."""

    name: str = "base"
    requires_credentials: bool = True

    def __init__(self, *, credentials: CredentialProvider | None = None, config: dict | None = None) -> None:
        self._credentials = credentials or default_chain()
        self.config = config or {}

    # ---- credentials -----------------------------------------------------
    def credential(self) -> Credential:
        """Resolve this provider's credential (re-resolved per call for rotation)."""
        return self._credentials.resolve(self.name)

    # ---- discovery -------------------------------------------------------
    def models(self) -> list[str]:  # pragma: no cover - interface
        raise NotImplementedError

    def default_model(self, modality: Modality) -> str | None:  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model: str | None = None) -> ModelCapabilities:  # pragma: no cover
        raise NotImplementedError

    def all_capabilities(self) -> list[ModelCapabilities]:
        return [self.capabilities(m) for m in self.models()]

    # ---- operations (override the supported ones) ------------------------
    def generate_image(self, req: ImageRequest) -> GenerationResult:
        raise self._unsupported("image generation")

    def generate_video(self, req: VideoRequest) -> GenerationResult | JobHandle:
        raise self._unsupported("video generation")

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
