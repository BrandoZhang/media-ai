"""OpenAI provider — Images API (GPT Image + DALL·E) and experimental Sora video.

Image generation is **synchronous**: ``POST /v1/images/generations`` (JSON) and
``POST /v1/images/edits`` (multipart, for references + inpaint mask). GPT Image
returns base64 only (never a hosted URL); DALL·E can return either. Video (Sora)
is an async job (``POST /v1/videos`` → poll ``GET /v1/videos/{id}``) and is marked
**experimental** — its API surface and availability were not fully confirmable at
build time (see docs/LIMITATIONS.md).

Verified against developers.openai.com / platform.openai.com. Auth: ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

from ..core.capabilities import GeometryMode, ImageCaps, ModelCapabilities, Operation, VideoCaps
from ..core.errors import ErrorCategory, MediaError
from ..core.mediaref import guess_mime, read_bytes
from ..core.result import Artifact, GenerationResult, JobHandle, JobStatus
from ..core.types import ImageRequest, JobRef, Modality, VideoRequest
from ..core.usage import record_usage
from ._base import HttpProvider

_GPT_IMAGE = ("gpt-image-2", "gpt-image-2-2026-04-21", "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini", "chatgpt-image-latest")
_DALLE = ("dall-e-3", "dall-e-2")
_SORA = ("sora-2", "sora-2-pro")
_FIXED_GPT_SIZES = ("1024x1024", "1536x1024", "1024x1536", "auto")
_DALLE3_SIZES = ("1024x1024", "1792x1024", "1024x1792")
_DALLE2_SIZES = ("256x256", "512x512", "1024x1024")


def _family(model: str) -> str:
    m = model.lower()
    if "sora" in m:
        return "sora"
    if "dall-e" in m:
        return "dalle"
    return "gpt-image"


class OpenAIProvider(HttpProvider):
    name = "openai"
    auth_scheme = "bearer"

    def __init__(self, *, credentials=None, config=None) -> None:
        super().__init__(credentials=credentials, config=config)
        self.base_url = (self.config.get("base_url") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
        self.video_model = os.getenv("OPENAI_VIDEO_MODEL", "sora-2")
        self.poll_interval = float(os.getenv("OPENAI_POLL_INTERVAL", "5") or 5)
        self.poll_timeout = float(os.getenv("OPENAI_POLL_TIMEOUT", "900") or 900)

    def _auth(self, cred):
        base, headers = super()._auth(cred)
        if os.getenv("OPENAI_ORG"):
            headers["OpenAI-Organization"] = os.environ["OPENAI_ORG"]
        if os.getenv("OPENAI_PROJECT"):
            headers["OpenAI-Project"] = os.environ["OPENAI_PROJECT"]
        return base, headers

    # ---- discovery -------------------------------------------------------
    def models(self) -> list[str]:
        return ["gpt-image-2", "gpt-image-1", "gpt-image-1-mini", "dall-e-3", "dall-e-2", "sora-2", "sora-2-pro"]

    def default_model(self, modality: Modality | None) -> str:
        return self.video_model if modality == Modality.VIDEO else self.image_model

    def capabilities(self, model: str | None = None, modality: Modality | None = None) -> ModelCapabilities:
        model = model or self.image_model
        fam = _family(model)
        if fam == "sora":
            return ModelCapabilities(
                provider=self.name, model=model, modalities=frozenset({Modality.VIDEO}), experimental=True,
                video=VideoCaps(
                    is_async=True, aspect_ratios=("16:9", "9:16"), resolutions=("720p", "1080p"),
                    durations=(4, 8, 12), supports_cancel=True, options=("size", "remix_video_id"),
                ),
                notes=("experimental: Sora Videos API surface/availability not fully verified",),
            )
        if fam == "dalle":
            is3 = model == "dall-e-3"
            return ModelCapabilities(
                provider=self.name, model=model, modalities=frozenset({Modality.IMAGE}),
                image=ImageCaps(
                    operations=frozenset({Operation.IMAGE_GENERATE} if is3 else {Operation.IMAGE_GENERATE, Operation.IMAGE_EDIT}),
                    geometry_mode=GeometryMode.PIXELS, pixel_sizes=(_DALLE3_SIZES if is3 else _DALLE2_SIZES),
                    max_count=1 if is3 else 10, output_formats=("png",),
                    supports_quality=is3, supports_mask=not is3, max_references=0 if is3 else 1,
                    options=("style",) if is3 else (),
                ),
                notes=("DALL·E is flat-priced (no token usage); dall-e-3 forces n=1",),
            )
        # GPT Image family
        arbitrary = model.startswith("gpt-image-2")
        return ModelCapabilities(
            provider=self.name, model=model, modalities=frozenset({Modality.IMAGE}),
            image=ImageCaps(
                operations=frozenset({Operation.IMAGE_GENERATE, Operation.IMAGE_EDIT}),
                geometry_mode=GeometryMode.BOTH,
                aspect_ratios=("1:1", "3:2", "2:3"),
                named_sizes=(),
                pixel_sizes=() if arbitrary else _FIXED_GPT_SIZES,
                pixel_multiple=16 if arbitrary else None,
                pixel_max=(3840, 2160) if arbitrary else None,
                max_count=10, output_formats=("png", "jpeg", "webp"),
                supports_quality=True,
                supports_transparency=not model.startswith("gpt-image-2"),  # gpt-image-2 rejects transparent
                supports_mask=True, max_references=16, supports_streaming=True,
                options=("moderation", "output_compression") + (() if model.endswith("mini") else ("input_fidelity",)),
            ),
            notes=(("gpt-image-2 does not support transparent backgrounds; arbitrary sizes must be /16, ratio 1:3–3:1, ≤3840x2160",)
                   if arbitrary else ("token-billed; base64 output only",)),
        )

    # ---- size mapping ----------------------------------------------------
    def _size(self, model: str, req: ImageRequest) -> str:
        geo = req.geometry
        fam = _family(model)
        if geo and geo.mode == "pixels":
            return f"{geo.width}x{geo.height}"
        if geo and geo.aspect_ratio:
            a, b = (geo.aspect_ratio.split(":", 1) + ["1"])[:2]
            landscape = float(a) > float(b)
            portrait = float(a) < float(b)
            if fam == "dalle" and model == "dall-e-3":
                return "1792x1024" if landscape else "1024x1792" if portrait else "1024x1024"
            if fam == "gpt-image":
                return "1536x1024" if landscape else "1024x1536" if portrait else "1024x1024"
        return "auto" if fam == "gpt-image" else "1024x1024"

    # ---- images ----------------------------------------------------------
    def generate_image(self, req: ImageRequest) -> GenerationResult:
        client, headers = self._prepare()
        model = req.model or self.image_model
        if req.operation == Operation.IMAGE_EDIT or req.references or req.mask:
            data = self._edit(client, headers, model, req)
        else:
            data = self._generate(client, headers, model, req)
        items = [d for d in (data.get("data") or []) if d.get("b64_json") or d.get("url")]
        if not items:
            raise MediaError("OpenAI image response had no images", category=ErrorCategory.PROVIDER, provider=self.name, model=model)
        out = Path(req.output)
        artifacts = [self._save(items[0], out, client)]
        for i, it in enumerate(items[1:], start=2):
            artifacts.append(self._save(it, out.with_name(f"{out.stem}_{i}{out.suffix}"), client, role="group"))
        usage = data.get("usage") or {}
        record_usage({"tool": req.operation.value, "operation": req.operation.value, "provider": self.name,
                      "model": model, "kind": "image", "generated_images": len(items),
                      "output_tokens": usage.get("output_tokens", 0), "total_tokens": usage.get("total_tokens", 0)})
        return GenerationResult(modality="image", operation=req.operation.value, provider=self.name, model=model,
                                artifacts=artifacts, usage=usage, meta={"prompt": req.prompt, "size": self._size(model, req)})

    def _common_fields(self, model: str, req: ImageRequest) -> dict:
        fam = _family(model)
        fields: dict = {"model": model, "prompt": req.prompt, "n": req.count, "size": self._size(model, req)}
        if req.quality:
            fields["quality"] = req.quality
        if fam == "gpt-image":
            if req.background:
                fields["background"] = req.background
            if req.output_format:
                fields["output_format"] = req.output_format
            for k in ("moderation", "output_compression", "input_fidelity"):
                if k in req.options:
                    fields[k] = req.options[k]
        if fam == "dalle":
            fields["response_format"] = "b64_json"  # unify on bytes
            if model == "dall-e-3" and "style" in req.options:
                fields["style"] = req.options["style"]
        return fields

    def _generate(self, client, headers, model: str, req: ImageRequest) -> dict:
        return client.request_json("POST", "/images/generations", body=self._common_fields(model, req), headers=headers)

    def _edit(self, client, headers, model: str, req: ImageRequest) -> dict:
        if not req.references:
            raise MediaError("image edit requires at least one reference image", category=ErrorCategory.VALIDATION, provider=self.name)
        fields = self._common_fields(model, req)
        fields.pop("response_format", None) if _family(model) == "gpt-image" else None
        files = []
        for r in req.references:
            content, mime = read_bytes(r)
            files.append(("image[]", Path(r.raw).name, mime, content))
        if req.mask:
            content, mime = read_bytes(req.mask)
            files.append(("mask", Path(req.mask.raw).name, mime, content))
        return client.request_multipart("POST", "/images/edits", fields=fields, files=files, headers=headers)

    @staticmethod
    def _save(item: dict, out: Path, client, *, role=None) -> Artifact:
        out.parent.mkdir(parents=True, exist_ok=True)
        if item.get("b64_json"):
            out.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            client.download(item["url"], out)
        return Artifact.from_path(out, "image", mime=guess_mime(out), role=role)

    # ---- video (Sora, experimental) --------------------------------------
    def generate_video(self, req: VideoRequest):
        client, headers = self._prepare()
        model = req.model or self.video_model
        body: dict = {"model": model, "prompt": req.prompt}
        if req.duration:
            body["seconds"] = str(req.duration)
        if "size" in req.options:
            body["size"] = req.options["size"]
        if "remix_video_id" in req.options:
            body["remix_video_id"] = req.options["remix_video_id"]
        data = client.request_json("POST", "/videos", body=body, headers=headers)
        job_id = data.get("id")
        if not job_id:
            raise MediaError("Sora create returned no video id", category=ErrorCategory.PROVIDER, provider=self.name)
        if not req.wait:
            return JobHandle(provider=self.name, model=model, id=job_id, output=str(req.output))
        return self._poll_video(client, headers, job_id, Path(req.output), model)

    def _poll_video(self, client, headers, job_id: str, out: Path, model: str) -> GenerationResult:
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            res = client.request_json("GET", f"/videos/{job_id}", headers=headers)
            status = str(res.get("status", "")).lower()
            if status == "completed":
                return self._finalize_video(client, headers, job_id, out, model, res)
            if status in ("failed", "cancelled", "canceled"):
                raise MediaError(f"Sora video {job_id} {status}", category=ErrorCategory.PROVIDER, provider=self.name)
            time.sleep(self.poll_interval)
        raise MediaError(f"Sora video {job_id} timed out after {self.poll_timeout}s", category=ErrorCategory.TIMEOUT, provider=self.name)

    def _finalize_video(self, client, headers, job_id: str, out: Path, model: str, res: dict) -> GenerationResult:
        content = client.request_bytes("GET", f"/videos/{job_id}/content", headers=headers)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        record_usage({"tool": "video.generate", "operation": "video.generate", "provider": self.name,
                      "model": model, "kind": "video", "seconds": res.get("seconds", 0)})
        return GenerationResult(modality="video", operation="video.generate", provider=self.name, model=model,
                                artifacts=[Artifact.from_path(out, "video", mime="video/mp4")],
                                usage={}, meta={"video_id": job_id})

    def get_job(self, ref: JobRef, *, output: Path | None = None) -> JobStatus:
        client, headers = self._prepare()
        res = client.request_json("GET", f"/videos/{ref.id}", headers=headers)
        status = str(res.get("status", "")).lower()
        result = None
        if output is not None and status == "completed":
            result = self._finalize_video(client, headers, ref.id, Path(output), res.get("model") or self.video_model, res)
        return JobStatus(provider=self.name, model=res.get("model"), id=ref.id, status=status or "unknown", op="query", result=result)

    def cancel_job(self, ref: JobRef) -> JobStatus:
        client, headers = self._prepare()
        client.request_json("DELETE", f"/videos/{ref.id}", headers=headers)
        return JobStatus(provider=self.name, model=None, id=ref.id, status="cancelled", op="cancel")

    # ---- errors ----------------------------------------------------------
    def _error(self, status: int, body: str) -> MediaError:
        low = body.lower()
        if "content_policy" in low or "moderation_blocked" in low or "safety" in low:
            return MediaError(f"OpenAI content policy: {body}", category=ErrorCategory.SAFETY, provider=self.name)
        cat = {400: ErrorCategory.VALIDATION, 401: ErrorCategory.AUTH, 403: ErrorCategory.AUTH,
               404: ErrorCategory.NOT_FOUND, 429: ErrorCategory.RATE_LIMIT}.get(status, ErrorCategory.PROVIDER)
        if "insufficient_quota" in low:
            cat = ErrorCategory.RATE_LIMIT
        return MediaError(f"OpenAI HTTP {status}: {body}", category=cat, provider=self.name, details={"status": status})
