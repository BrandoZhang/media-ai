"""media-ai: standalone multimodal generation CLIs.

A self-contained toolkit (no uni-agent dependency) exposing text/image/video
generation as CLI commands, with an offline mock backend and a real
Volcengine Ark backend, plus per-call token-cost accounting.

Public API: ``from media_ai import mediakit``.
"""

from media_ai import mediakit

__version__ = "0.1.0"
__all__ = ["mediakit", "__version__"]
