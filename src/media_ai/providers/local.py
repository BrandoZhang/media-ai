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

from ..core.adapter import Adapter
from ..core.scene import Scene
from ..core.result import Artifact, GenerationResult


class LocalAdapter(Adapter):

    def supported_scenes(self) -> frozenset[Scene]:
        return frozenset({Scene.VIDEO_CONCAT})

    def concat(self, inputs: list[Path], output: Path, *, width: int, height: int) -> GenerationResult:
        from ..media import ffmpeg

        out = ffmpeg.concat_clips(inputs, Path(output), w=width, h=height)
        return GenerationResult(
            modality="video", operation="video.concat", provider=self.name, model="ffmpeg",
            artifacts=[Artifact.from_path(out, "video", mime="video/mp4")],
            usage={}, meta={"clips": len(inputs)},
        )
