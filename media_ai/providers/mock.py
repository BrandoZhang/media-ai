"""Offline mock provider — the default, deterministic, credential-free backend.

Draws placeholder images with Pillow (the prompt baked into the frame) and turns
them into short clips with ffmpeg. Deterministic given ``(prompt, seed)``; costs
nothing. Token counts are synthesized with the same formulas the real APIs
document, so the cost-tracking path is exercised offline.

Async is simulated statelessly: ``video generate --wait false`` returns a
:class:`JobHandle` whose id encodes the request, and ``job query --output`` decodes
it and renders deterministically — so the whole submit→poll→finalize path is
testable without a network.
"""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

from ..core.capabilities import (
    GeometryMode,
    ImageCaps,
    ModelCapabilities,
    Operation,
    VideoCaps,
)
from ..core.errors import ErrorCategory, MediaError
from ..core.geometry import resolve_image_pixels, resolve_video_pixels
from ..core.provider import Provider
from ..core.result import Artifact, GenerationResult, JobHandle, JobStatus
from ..core.types import ImageRequest, JobRef, Modality, VideoRequest
from ..core.usage import record_usage
from ..media import ffmpeg, pillow

_DEFAULT_IMG = (768, 432)
_MOCK_RENDER_H = 360  # render small (fast); bill at requested resolution


def _image_tokens(w: int, h: int, n: int) -> dict:
    per = (w * h) // 256  # matches Ark: output_tokens = images * floor(w*h/256)
    return {"generated_images": n, "output_tokens": per * n, "total_tokens": per * n}


def _video_tokens(w: int, h: int, seconds: int) -> dict:
    tok = (w * h * seconds) // 1024
    return {"completion_tokens": tok, "total_tokens": tok}


class MockProvider(Provider):
    name = "mock"
    requires_credentials = False

    def models(self) -> list[str]:
        return ["mock"]

    def default_model(self, modality: Modality | None) -> str:
        return "mock"

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            provider=self.name,
            model="mock",
            modalities=frozenset({Modality.IMAGE, Modality.VIDEO}),
            image=ImageCaps(
                operations=frozenset({Operation.IMAGE_GENERATE, Operation.IMAGE_EDIT}),
                geometry_mode=GeometryMode.BOTH,
                aspect_ratios=("1:1", "16:9", "9:16", "4:3", "3:4", "21:9"),
                named_sizes=("512", "1K", "2K", "4K"),
                max_count=8,
                output_formats=("png",),
                supports_seed=True,
                supports_negative_prompt=True,
                supports_transparency=True,
                supports_quality=True,
                supports_mask=True,
                max_references=9,
            ),
            video=VideoCaps(
                operations=frozenset({Operation.VIDEO_GENERATE}),
                is_async=True,
                aspect_ratios=("16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"),
                resolutions=("480p", "720p", "1080p"),
                durations=tuple(range(1, 13)),
                supports_first_frame=True,
                supports_last_frame=True,
                supports_reference_images=True,
                supports_reference_videos=True,
                supports_reference_audios=True,
                supports_seed=True,
                supports_negative_prompt=True,
                supports_audio=True,
                supports_watermark_control=True,
                supports_return_last_frame=True,
            ),
            notes=("offline placeholder generator; deterministic given (prompt, seed)",),
        )

    # ---- images ----------------------------------------------------------
    def generate_image(self, req: ImageRequest) -> GenerationResult:
        base = None
        if req.references:
            ref0 = req.references[0]
            if ref0.is_local:
                if not ref0.path().is_file():
                    raise MediaError(f"reference image not found: {ref0.raw}", category=ErrorCategory.IO)
                base = ref0.path()
        w, h = resolve_image_pixels(req.geometry, _DEFAULT_IMG)
        out = Path(req.output)
        pillow.ensure_parent(out)
        title = f"mock {req.operation.value}"
        pillow.draw_caption_image(out, title=title, prompt=req.prompt, w=w, h=h,
                                  rgb=pillow.palette(req.prompt, req.seed), base_image=base)
        artifacts = [Artifact.from_path(out, "image", mime="image/png")]
        for i in range(2, req.count + 1):
            p = out.with_name(f"{out.stem}_{i}{out.suffix}")
            pillow.draw_caption_image(p, title=f"{title} ({i}/{req.count})", prompt=req.prompt, w=w, h=h,
                                      rgb=pillow.palette(req.prompt + str(i), req.seed), base_image=base)
            artifacts.append(Artifact.from_path(p, "image", mime="image/png", role="group"))
        usage = _image_tokens(w, h, req.count)
        record_usage({"tool": req.operation.value, "operation": req.operation.value, "provider": self.name,
                      "model": "mock", "kind": "image", "generated_images": req.count, **usage})
        return GenerationResult(
            modality="image", operation=req.operation.value, provider=self.name, model="mock",
            artifacts=artifacts, usage=usage,
            meta={"prompt": req.prompt, "seed": req.seed, "size": [w, h],
                  "refs": [r.raw for r in req.references]},
        )

    # ---- video -----------------------------------------------------------
    def generate_video(self, req: VideoRequest):
        if not req.wait:
            return JobHandle(provider=self.name, model="mock", id=_encode_job(req), output=str(req.output),
                             meta={"note": "mock async; poll with `media-ai job query`"})
        return self._render_video(req, str(req.output))

    def _render_video(self, req: VideoRequest, output: str) -> GenerationResult:
        out = Path(output)
        ffmpeg.ensure_parent(out)
        bw, bh = resolve_video_pixels(req.geometry)  # billed dims
        rh = min(_MOCK_RENDER_H, bh)
        rw = max(2, (bw * rh // bh) // 2 * 2)
        base = None
        if req.first_frame and req.first_frame.is_local and req.first_frame.path().is_file():
            base = req.first_frame.path()
        elif req.reference_images:
            r0 = req.reference_images[0]
            base = r0.path() if (r0.is_local and r0.path().is_file()) else None
        tag = _ref_tag(req)
        with tempfile.TemporaryDirectory() as td:
            frame = Path(td) / "frame.png"
            pillow.draw_caption_image(frame, title=f"mock {req.operation.value}", prompt=req.prompt + tag,
                                      w=rw, h=rh, rgb=pillow.palette(req.prompt, req.seed), base_image=base)
            ffmpeg.image_to_clip(frame, out, seconds=req.duration or 5, fps=ffmpeg.DEFAULT_FPS, w=rw, h=rh)
        artifacts = [Artifact.from_path(out, "video", mime="video/mp4")]
        if req.return_last_frame:
            lf = out.with_name(f"{out.stem}_lastframe.png")
            pillow.draw_caption_image(lf, title="mock · last frame", prompt=req.prompt, w=rw, h=rh,
                                      rgb=pillow.palette(req.prompt, req.seed), base_image=base)
            artifacts.append(Artifact.from_path(lf, "frame", mime="image/png", role="last_frame"))
        seconds = req.duration or 5
        usage = _video_tokens(bw, bh, seconds)
        record_usage({"tool": req.operation.value, "operation": req.operation.value, "provider": self.name,
                      "model": "mock", "kind": "video", "seconds": seconds, **usage})
        return GenerationResult(
            modality="video", operation=req.operation.value, provider=self.name, model="mock",
            artifacts=artifacts, usage=usage,
            meta={"prompt": req.prompt, "seconds": seconds, "seed": req.seed, "render_size": [rw, rh]},
        )

    # ---- jobs ------------------------------------------------------------
    def get_job(self, ref: JobRef, *, output: Path | None = None) -> JobStatus:
        req = _decode_job(ref.id)
        result = None
        if output is not None:
            result = self._render_video(req, str(output))
        return JobStatus(provider=self.name, model="mock", id=ref.id, status="succeeded", op="query", result=result)

    def cancel_job(self, ref: JobRef) -> JobStatus:
        return JobStatus(provider=self.name, model="mock", id=ref.id, status="cancelled", op="cancel",
                         raw={"note": "mock generates synchronously; nothing to cancel"})


def _ref_tag(req: VideoRequest) -> str:
    n = len(req.reference_images) + len(req.reference_videos) + len(req.reference_audios)
    parts = []
    if req.first_frame:
        parts.append("first")
    if req.last_frame:
        parts.append("last")
    if n:
        parts.append(f"refs:{n}")
    return f"  [{', '.join(parts)}]" if parts else ""


def _encode_job(req: VideoRequest) -> str:
    payload = {"prompt": req.prompt, "duration": req.duration, "seed": req.seed,
               "geometry": req.geometry.as_dict() if req.geometry else {},
               "operation": req.operation.value, "return_last_frame": req.return_last_frame}
    return "mock-" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_job(job_id: str) -> VideoRequest:
    from ..core.types import GeometrySpec

    if not job_id.startswith("mock-"):
        raise MediaError(f"not a mock job id: {job_id!r}", category=ErrorCategory.NOT_FOUND, provider="mock")
    try:
        payload = json.loads(base64.urlsafe_b64decode(job_id[len("mock-"):]).decode())
    except Exception as exc:  # noqa: BLE001
        raise MediaError(f"corrupt mock job id: {job_id!r}", category=ErrorCategory.NOT_FOUND, provider="mock") from exc
    geo = payload.get("geometry") or {}
    return VideoRequest(
        prompt=payload.get("prompt", ""), output=Path("unused"),
        duration=payload.get("duration"), seed=payload.get("seed"),
        geometry=GeometrySpec(**geo) if geo else None,
        return_last_frame=payload.get("return_last_frame", False),
    )
