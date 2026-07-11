"""Provider-agnostic core: request/response types, capability model, geometry,
errors, usage ledger, registry. Never imports :mod:`media_ai.providers`.
"""

from .capabilities import (
    GeometryMode,
    ImageCaps,
    ModelCapabilities,
    UnsupportedPolicy,
    VideoCaps,
    validate_request,
)
from .errors import ErrorCategory, MediaError
from .provider import Provider
from .result import Artifact, GenerationResult, JobHandle, JobStatus
from .types import (
    GeometrySpec,
    ImageRequest,
    JobRef,
    MediaRef,
    Modality,
    Operation,
    VideoRequest,
)

__all__ = [
    "MediaError",
    "ErrorCategory",
    "Provider",
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
    "ModelCapabilities",
    "ImageCaps",
    "VideoCaps",
    "GeometryMode",
    "UnsupportedPolicy",
    "validate_request",
]
