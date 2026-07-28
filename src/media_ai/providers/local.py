"""The local backend: bundled ffmpeg, no network, no credential, no cost.

Joining generated clips into one film is the last step of a video run, and it is
work this CLI already does — so it is a binding (``local/ffmpeg``) like any other
rather than a command that quietly sits outside the model. That is what lets the
setup wizard group it correctly ("free, offline, nothing to configure" is derived
from ``auth.kind = "none"``, not declared per skill) and what gives ``concat`` the
same ``meta.binding`` every other result carries.
"""

from __future__ import annotations

from pathlib import Path

from ..core.capabilities import ModelCapabilities, VideoCaps
from ..core.provider import Provider
from ..core.result import Artifact, GenerationResult
from ..core.types import Modality


class LocalProvider(Provider):
    name = "local"
    requires_credentials = False

    def models(self) -> list[str]:
        return ["ffmpeg"]

    def default_model(self, modality: Modality | None = None) -> str:
        return "ffmpeg"

    def capabilities(self, model: str | None = None, modality: Modality | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            provider=self.name, model=model or "ffmpeg", modalities=frozenset({Modality.VIDEO}),
            # No generation operations: this backend transforms video that already
            # exists. Declaring the modality with an empty operation set says exactly
            # that, rather than leaving the caps object inconsistent with itself.
            video=VideoCaps(operations=frozenset(), is_async=False, supports_cancel=False),
            notes=("local ffmpeg: joins clips, normalizing each to a common size first",),
        )

    def concat(self, inputs: list[Path], output: Path, *, width: int, height: int) -> GenerationResult:
        from ..media import ffmpeg

        out = ffmpeg.concat_clips(inputs, Path(output), w=width, h=height)
        return GenerationResult(
            modality="video", operation="video.concat", provider=self.name, model="ffmpeg",
            artifacts=[Artifact.from_path(out, "video", mime="video/mp4")],
            usage={}, meta={"clips": len(inputs)},
        )
