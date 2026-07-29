"""media-ai: provider- and model-agnostic multimodal generation CLI.

Public API for embedding and for writing **custom backends**::

    from media_ai import register_manifest, Adapter, HttpAdapter
    from media_ai import Scene, Modality, Constraints
    from media_ai import GenerationResult, Artifact, JobHandle, MediaError

A backend is a **manifest** (what it can do) plus an **adapter** (how to call it).
Register one in-process::

    register_manifest(ACME_MANIFEST_TOML)

or ship it as a package with a ``media_ai.bindings`` entry point pointing at the
manifest; its ``[provider].adapter`` names the class to import, which may live
anywhere — including a private package wrapping an internal RPC platform. See
docs/EXTENDING.md.
"""

from .core.adapter import Adapter
from .core.binding import BindingSpec, Constraints, ProviderSpec
from .core.errors import ErrorCategory, MediaError
from .core.registry import catalog, register_manifest, unregister_manifest
from .core.retry import retry
from .core.scene import Scene, derive_scene
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
    SoundEffectRequest,
    SpeechRequest,
    VideoRequest,
)
from .core.validate import UnsupportedPolicy, validate_request
from .providers._base import HttpAdapter

__version__ = "0.4.1"

__all__ = [
    "register_manifest",
    "unregister_manifest",
    "catalog",
    "retry",
    "Scene",
    "derive_scene",
    "Adapter",
    "HttpAdapter",
    "BindingSpec",
    "ProviderSpec",
    "Constraints",
    "UnsupportedPolicy",
    "validate_request",
    "Modality",
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
