"""Provider-agnostic core: requests, scenes, binding declarations, geometry, errors,
the usage ledger, and the registry. Never imports :mod:`media_ai.providers`.
"""

from .adapter import Adapter
from .binding import BindingSpec, Constraints, ProviderSpec
from .errors import ErrorCategory, MediaError
from .result import Artifact, GenerationResult, JobHandle, JobStatus
from .scene import Scene, derive_scene
from .types import (
    GeometrySpec,
    ImageRequest,
    JobRef,
    MediaRef,
    Modality,
    Operation,
    VideoRequest,
)
from .validate import UnsupportedPolicy, validate_request

__all__ = [
    "MediaError",
    "ErrorCategory",
    "Adapter",
    "Scene",
    "derive_scene",
    "BindingSpec",
    "ProviderSpec",
    "Constraints",
    "Modality",
    "Operation",
    "MediaRef",
    "GeometrySpec",
    "ImageRequest",
    "VideoRequest",
    "JobRef",
    "GenerationResult",
    "Artifact",
    "JobHandle",
    "JobStatus",
    "UnsupportedPolicy",
    "validate_request",
]
