"""The local backend: bundled ffmpeg, no network, no credential, no cost.

Joining generated clips into one film is the last step of a video run, and it is
work this CLI already does — so it is a binding (``local/ffmpeg``) like any other
rather than a command that quietly sits outside the model. That is what lets the
setup wizard group it correctly ("free, offline, nothing to configure" is derived
from ``auth.kind = "none"``, not declared per skill) and what gives ``concat`` the
same ``meta.binding`` every other result carries.

Animated-image export (``animation.*``) is here for the same reason: it is the last
mile of a render — the artifact that autoplays in a README or a chat message — and it
resolves, validates and reports exactly like a call to a model does. An agent asking
"which binding can give me a transparent WebP?" gets its answer from the same catalog
that answers every other capability question, instead of from prose in a skill.
"""

from __future__ import annotations

from pathlib import Path

from ..core.adapter import Adapter
from ..core.result import Artifact, GenerationResult
from ..core.scene import Scene, derive_scene
from ..core.types import AnimationRequest

#: The mime an animated container is actually served as. APNG is the odd one: it is a
#: PNG to anything that cannot animate it, and `.png` is the extension most consumers
#: expect, so the mime follows the extension the caller chose rather than the encoder.
_ANIMATION_MIME = {"gif": "image/gif", "webp": "image/webp", "apng": "image/apng"}


class LocalAdapter(Adapter):

    def supported_scenes(self) -> frozenset[Scene]:
        return frozenset({Scene.VIDEO_CONCAT, Scene.ANIMATION_FROM_VIDEO, Scene.ANIMATION_FROM_FRAMES})

    def concat(self, inputs: list[Path], output: Path, *, width: int, height: int) -> GenerationResult:
        from ..media import ffmpeg

        out = ffmpeg.concat_clips(inputs, Path(output), w=width, h=height)
        self.record(Scene.VIDEO_CONCAT, kind="video", clips=len(inputs))
        return GenerationResult(
            modality="video", provider=self.name, model="ffmpeg",
            artifacts=[Artifact.from_path(out, "video", mime="video/mp4")],
            usage={}, meta={"clips": len(inputs)},
        )

    def generate_animation(self, req: AnimationRequest) -> GenerationResult:
        from ..media import animation

        container = animation.container_for(Path(req.output), req.output_format)
        transparent = bool(req.transparent)
        rendered = animation.render(req, container, transparent=transparent)
        out = rendered.path
        scene = derive_scene(req)

        produced = animation.probe(out)
        mime = _ANIMATION_MIME[container.name]
        if container.name == "apng" and out.suffix.lower() == ".png":
            mime = "image/png"

        # Free and offline, so there is nothing to bill — but the line still belongs in
        # the ledger: "what did this run produce, and through what?" is the same question
        # for a local encode as for a paid call, and an absent binding makes the answer
        # incomplete rather than cheap.
        self.record(scene, kind="animation", format=container.name,
                    **({"frames": produced["frame_count"]} if "frame_count" in produced else {}))

        meta = {
            "format": container.name,
            "transparent": transparent,
            "loop": "forever" if not req.loop else int(req.loop),
            **produced,
        }
        # What went in, named the way the scene names it. `frame_count` above is what came
        # *out*, read off the finished file — a count of the arguments would have been a
        # different number for the same animation (`--frames dir/` is one argument and
        # twelve frames) and the two are easy to confuse in one field.
        if req.frames:
            meta["frames"] = [f.raw for f in req.frames]
        elif req.source is not None:
            meta["source"] = req.source.raw
        notes = animation.describe(container, route=rendered.route)
        if transparent and container.alpha == "binary":
            notes = [*notes, "keyed with a chroma distance, so a soft or motion-blurred edge "
                             "keeps a fringe; shoot on a flat colour or feed matted frames back "
                             "in through --frames"]
        if notes:
            meta["notes"] = notes
        return GenerationResult(
            modality="image", provider=self.name, model="ffmpeg",
            artifacts=[Artifact.from_path(out, "image", mime=mime)],
            usage={}, meta=meta,
        )
