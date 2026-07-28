"""media-ai: provider- and model-agnostic multimodal generation CLI.

Public API for embedding and for writing **custom providers**::

    from media_ai import register_provider, Provider, HttpProvider
    from media_ai import ModelCapabilities, ImageCaps, VideoCaps, Operation, Modality
    from media_ai import GenerationResult, Artifact, JobHandle, MediaError

Register a custom backend in-process::

    register_provider("acme", lambda **kw: AcmeProvider(**kw), model_hints=("acme-",))

or ship it as a package with a ``media_ai.providers`` entry point. See
docs/EXTENDING.md.
"""

from .core.capabilities import (
    AudioCaps,
    GeometryMode,
    ImageCaps,
    ModelCapabilities,
    UnsupportedPolicy,
    VideoCaps,
    validate_request,
)
from .core.errors import ErrorCategory, MediaError
from .core.provider import Provider
from .core.registry import provider_names, register_provider, unregister_provider
from .core.retry import retry
from .core.result import Artifact, GenerationResult, JobHandle, JobStatus
from .core.types import (
    DialogueRequest,
    DialogueTurn,
    GeometrySpec,
    ImageRequest,
    JobRef,
    MediaRef,
    Modality,
    MusicPlanRequest,
    MusicRequest,
    Operation,
    SoundEffectRequest,
    SpeechRequest,
    VideoRequest,
)
from .providers._base import HttpProvider

__version__ = "0.3.0"

__all__ = [
    "register_provider",
    "unregister_provider",
    "provider_names",
    "retry",
    "Provider",
    "HttpProvider",
    "ModelCapabilities",
    "ImageCaps",
    "VideoCaps",
    "AudioCaps",
    "GeometryMode",
    "UnsupportedPolicy",
    "validate_request",
    "Modality",
    "Operation",
    "ImageRequest",
    "VideoRequest",
    "SpeechRequest",
    "DialogueRequest",
    "DialogueTurn",
    "MusicRequest",
    "MusicPlanRequest",
    "SoundEffectRequest",
    "MediaRef",
    "GeometrySpec",
    "JobRef",
    "GenerationResult",
    "Artifact",
    "JobHandle",
    "JobStatus",
    "MediaError",
    "ErrorCategory",
    "__version__",
]
