"""Google Gemini provider — native image (``generateContent``), Imagen
(``:predict``), and Veo video (``:predictLongRunning``).

Three wire shapes behind one adapter:
  * **Gemini native image** (gemini-2.5-flash-image / gemini-3-pro-image):
    conversational, multimodal edit/compose; base64 image out inline. Synchronous.
  * **Imagen** (imagen-4.0-*): dedicated text→image ``:predict``; base64 out.
  * **Veo** (veo-*): async long-running operation → poll → **download the file URI
    with the API key**.

Notable Gemini quirks handled here: a 200-OK response that carries *no image* is a
silent safety drop (surfaced as a ``SAFETY`` error, not an empty file); SynthID
watermarking is unconditional; ``generateAudio`` is unreliable on the Developer API
(Veo 3.x audio is native). Auth: ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

from ..core.capabilities import GeometryMode, ImageCaps, ModelCapabilities, Operation, VideoCaps
from ..core.errors import ErrorCategory, MediaError
from ..core.mediaref import to_base64
from ..core.result import Artifact, GenerationResult, JobHandle, JobStatus
from ..core.types import ImageRequest, JobRef, Modality, VideoRequest
from ..core.usage import record_usage
from ._base import HttpProvider

_NATIVE_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
_PRO_RATIOS = _NATIVE_RATIOS + ("1:4", "4:1", "1:8", "8:1")
_IMAGEN_RATIOS = ("1:1", "3:4", "4:3", "9:16", "16:9")


def _family(model: str) -> str:
    m = model.lower()
    if m.startswith("veo"):
        return "veo"
    if m.startswith("imagen"):
        return "imagen"
    return "native"


class GeminiProvider(HttpProvider):
    name = "gemini"
    auth_scheme = "x-goog"

    def __init__(self, *, credentials=None, config=None) -> None:
        super().__init__(credentials=credentials, config=config)
        self.base_url = (self.config.get("base_url") or os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")).rstrip("/")
        self.image_model = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
        self.video_model = os.getenv("GEMINI_VIDEO_MODEL", "veo-3.0-generate-001")
        self.poll_interval = float(os.getenv("GEMINI_POLL_INTERVAL", "10") or 10)
        self.poll_timeout = float(os.getenv("GEMINI_POLL_TIMEOUT", "1200") or 1200)

    # ---- discovery -------------------------------------------------------
    def models(self) -> list[str]:
        return ["gemini-2.5-flash-image", "gemini-3-pro-image", "imagen-4.0-generate-001",
                "imagen-4.0-ultra-generate-001", "imagen-4.0-fast-generate-001",
                "veo-3.1-generate-preview", "veo-3.0-generate-001", "veo-2.0-generate-001"]

    def default_model(self, modality: Modality | None) -> str:
        return self.video_model if modality == Modality.VIDEO else self.image_model

    def capabilities(self, model: str | None = None, modality: Modality | None = None) -> ModelCapabilities:
        model = model or self.image_model
        fam = _family(model)
        if fam == "veo":
            v3 = model.startswith("veo-3")
            v31 = model.startswith("veo-3.1")
            return ModelCapabilities(
                provider=self.name, model=model, modalities=frozenset({Modality.VIDEO}),
                experimental=model.endswith("preview"),
                video=VideoCaps(
                    is_async=True, aspect_ratios=("16:9", "9:16"),
                    resolutions=("720p", "1080p") if v3 else ("720p",),
                    durations=(4, 6, 8) if v3 else (5, 6, 7, 8),
                    supports_first_frame=True, supports_last_frame=v31, supports_reference_images=v31,
                    supports_seed=True, supports_negative_prompt=True, supports_audio=v3, audio_default=v3 or None,
                    supports_cancel=False, options=("person_generation",),
                ),
                notes=("SynthID watermark is unconditional; on the Developer API generateAudio is unreliable "
                       "(Veo 3.x audio is native, Veo 2 is silent)",),
            )
        if fam == "imagen":
            ultra = "ultra" in model
            return ModelCapabilities(
                provider=self.name, model=model, modalities=frozenset({Modality.IMAGE}),
                image=ImageCaps(
                    operations=frozenset({Operation.IMAGE_GENERATE}),
                    geometry_mode=GeometryMode.ASPECT_RATIO, aspect_ratios=_IMAGEN_RATIOS,
                    named_sizes=("1K", "2K"), max_count=1 if ultra else 4, output_formats=("png", "jpeg"),
                    supports_seed=True, supports_negative_prompt=True, max_references=0,
                    options=("person_generation", "guidance_scale", "language"),
                ),
                notes=("Imagen :predict; SynthID unconditional; no image editing (use a gemini-*-image model)",),
            )
        pro = "pro" in model
        return ModelCapabilities(
            provider=self.name, model=model, modalities=frozenset({Modality.IMAGE}),
            image=ImageCaps(
                operations=frozenset({Operation.IMAGE_GENERATE, Operation.IMAGE_EDIT}),
                geometry_mode=GeometryMode.ASPECT_RATIO,
                aspect_ratios=_PRO_RATIOS if pro else _NATIVE_RATIOS,
                named_sizes=("512", "1K", "2K", "4K") if pro else ("1K",),
                max_count=4, output_formats=("png",), max_references=9,
            ),
            notes=("native conversational image gen/edit; SynthID unconditional"
                   + ("" if pro else "; imageSize fixed at 1K"),),
        )

    # ---- images ----------------------------------------------------------
    def generate_image(self, req: ImageRequest) -> GenerationResult:
        model = req.model or self.image_model
        if _family(model) == "imagen":
            return self._imagen(model, req)
        return self._native(model, req)

    def _native(self, model: str, req: ImageRequest) -> GenerationResult:
        client, headers = self._prepare()
        parts: list[dict] = [{"text": req.prompt}]
        for r in req.references:
            b64, mime = to_base64(r)
            parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        gen_cfg: dict = {"responseModalities": ["TEXT", "IMAGE"]}
        if req.count > 1:
            gen_cfg["candidateCount"] = req.count
        img_cfg = {}
        if req.geometry and req.geometry.aspect_ratio:
            img_cfg["aspectRatio"] = req.geometry.aspect_ratio
        if req.geometry and req.geometry.resolution:
            img_cfg["imageSize"] = req.geometry.resolution
        if img_cfg:
            gen_cfg["imageConfig"] = img_cfg
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}
        data = client.request_json("POST", f"/models/{model}:generateContent", body=body, headers=headers)
        images = _extract_inline_images(data)
        if not images:
            raise _no_image_error(data, self.name, model)
        out = Path(req.output)
        artifacts = [_write_b64(images[0], out, "image")]
        for i, im in enumerate(images[1:], start=2):
            artifacts.append(_write_b64(im, out.with_name(f"{out.stem}_{i}{out.suffix}"), "image", role="group"))
        usage = data.get("usageMetadata") or {}
        record_usage({"tool": req.operation.value, "operation": req.operation.value, "provider": self.name,
                      "model": model, "kind": "image", "generated_images": len(images),
                      "output_tokens": usage.get("candidatesTokenCount", 0), "total_tokens": usage.get("totalTokenCount", 0)})
        return GenerationResult(modality="image", operation=req.operation.value, provider=self.name, model=model,
                                artifacts=artifacts, usage=usage, meta={"prompt": req.prompt})

    def _imagen(self, model: str, req: ImageRequest) -> GenerationResult:
        client, headers = self._prepare()
        params: dict = {"sampleCount": req.count}
        if req.geometry and req.geometry.aspect_ratio:
            params["aspectRatio"] = req.geometry.aspect_ratio
        if req.geometry and req.geometry.resolution:
            params["sampleImageSize"] = req.geometry.resolution
        if req.negative_prompt:
            params["negativePrompt"] = req.negative_prompt
        if req.seed is not None and req.seed >= 0:
            params["seed"] = req.seed
        for k in ("person_generation", "guidance_scale", "language"):
            if k in req.options:
                params[{"person_generation": "personGeneration", "guidance_scale": "guidanceScale", "language": "language"}[k]] = req.options[k]
        body = {"instances": [{"prompt": req.prompt}], "parameters": params}
        data = client.request_json("POST", f"/models/{model}:predict", body=body, headers=headers)
        preds = [p for p in (data.get("predictions") or []) if p.get("bytesBase64Encoded")]
        if not preds:
            raise MediaError(f"Imagen returned no images (filtered by safety?): {str(data)[:200]}",
                             category=ErrorCategory.SAFETY, provider=self.name, model=model)
        out = Path(req.output)
        artifacts = [_write_b64(preds[0]["bytesBase64Encoded"], out, "image", mime=preds[0].get("mimeType"))]
        for i, p in enumerate(preds[1:], start=2):
            artifacts.append(_write_b64(p["bytesBase64Encoded"], out.with_name(f"{out.stem}_{i}{out.suffix}"), "image",
                                        mime=p.get("mimeType"), role="group"))
        record_usage({"tool": req.operation.value, "operation": req.operation.value, "provider": self.name,
                      "model": model, "kind": "image", "generated_images": len(preds)})
        return GenerationResult(modality="image", operation=req.operation.value, provider=self.name, model=model,
                                artifacts=artifacts, usage={}, meta={"prompt": req.prompt})

    # ---- video (Veo long-running op) -------------------------------------
    def generate_video(self, req: VideoRequest):
        client, headers = self._prepare()
        model = req.model or self.video_model
        instance: dict = {"prompt": req.prompt}
        if req.first_frame:
            b64, mime = to_base64(req.first_frame)
            instance["image"] = {"bytesBase64Encoded": b64, "mimeType": mime}
        if req.last_frame:
            b64, mime = to_base64(req.last_frame)
            instance["lastFrame"] = {"bytesBase64Encoded": b64, "mimeType": mime}
        params: dict = {}
        if req.geometry and req.geometry.aspect_ratio and req.geometry.aspect_ratio != "adaptive":
            params["aspectRatio"] = req.geometry.aspect_ratio
        if req.geometry and req.geometry.resolution:
            params["resolution"] = req.geometry.resolution
        if req.duration:
            params["durationSeconds"] = req.duration
        if req.negative_prompt:
            params["negativePrompt"] = req.negative_prompt
        if req.audio is not None:
            params["generateAudio"] = req.audio
        if "person_generation" in req.options:
            params["personGeneration"] = req.options["person_generation"]
        body = {"instances": [instance], "parameters": params}
        data = client.request_json("POST", f"/models/{model}:predictLongRunning", body=body, headers=headers)
        op_name = data.get("name")
        if not op_name:
            raise MediaError("Veo returned no operation name", category=ErrorCategory.PROVIDER, provider=self.name)
        if not req.wait:
            return JobHandle(provider=self.name, model=model, id=op_name, output=str(req.output))
        return self._poll_operation(client, headers, op_name, Path(req.output), model)

    def _poll_operation(self, client, headers, op_name: str, out: Path, model: str) -> GenerationResult:
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            res = client.request_json("GET", f"/{op_name}", headers=headers)
            if res.get("done"):
                return self._finalize_video(client, headers, op_name, out, model, res)
            time.sleep(self.poll_interval)
        raise MediaError(f"Veo operation {op_name} timed out after {self.poll_timeout}s",
                         category=ErrorCategory.TIMEOUT, provider=self.name)

    def _finalize_video(self, client, headers, op_name: str, out: Path, model: str, res: dict) -> GenerationResult:
        if res.get("error"):
            raise MediaError(f"Veo operation failed: {str(res['error'])[:200]}", category=ErrorCategory.PROVIDER, provider=self.name)
        uri = _veo_video_uri(res)
        if not uri:
            raise MediaError(f"Veo operation {op_name} done but no video uri", category=ErrorCategory.PROVIDER, provider=self.name)
        client.download(uri, out, headers=headers)  # the file URI needs the API key
        record_usage({"tool": "video.generate", "operation": "video.generate", "provider": self.name,
                      "model": model, "kind": "video"})
        return GenerationResult(modality="video", operation="video.generate", provider=self.name, model=model,
                                artifacts=[Artifact.from_path(out, "video", mime="video/mp4")], usage={},
                                meta={"operation": op_name})

    def get_job(self, ref: JobRef, *, output: Path | None = None) -> JobStatus:
        client, headers = self._prepare()
        res = client.request_json("GET", f"/{ref.id}", headers=headers)
        done = bool(res.get("done"))
        result = None
        if done and output is not None and not res.get("error"):
            result = self._finalize_video(client, headers, ref.id, Path(output), ref.model or self.video_model, res)
        return JobStatus(provider=self.name, model=ref.model, id=ref.id,
                         status="succeeded" if done else "running", op="query", result=result)

    def cancel_job(self, ref: JobRef) -> JobStatus:
        raise MediaError("Veo long-running operations cannot be cancelled on the Gemini Developer API",
                         category=ErrorCategory.UNSUPPORTED, provider=self.name)

    # ---- errors ----------------------------------------------------------
    def _error(self, status: int, body: str) -> MediaError:
        low = body.lower()
        if "safety" in low or "blocked" in low or "prohibited" in low:
            return MediaError(f"Gemini safety block: {body}", category=ErrorCategory.SAFETY, provider=self.name)
        cat = {400: ErrorCategory.VALIDATION, 401: ErrorCategory.AUTH, 403: ErrorCategory.AUTH,
               404: ErrorCategory.NOT_FOUND, 429: ErrorCategory.RATE_LIMIT}.get(status, ErrorCategory.PROVIDER)
        return MediaError(f"Gemini HTTP {status}: {body}", category=cat, provider=self.name, details={"status": status})


# --------------------------------------------------------------------------
# response parsing helpers
# --------------------------------------------------------------------------


def _extract_inline_images(data: dict) -> list[str]:
    out: list[str] = []
    for cand in data.get("candidates") or []:
        for part in ((cand.get("content") or {}).get("parts") or []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                out.append(inline["data"])
    return out


def _no_image_error(data: dict, provider: str, model: str) -> MediaError:
    """Turn a 200-OK-but-no-image response into a categorized error (Gemini's
    silent safety drop) instead of writing an empty file."""
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        return MediaError(f"Gemini blocked the prompt: {feedback['blockReason']}",
                          category=ErrorCategory.SAFETY, provider=provider, model=model, details=feedback)
    cands = data.get("candidates") or []
    finish = (cands[0].get("finishReason") if cands else None) or "NO_IMAGE"
    if any(x in str(finish).upper() for x in ("SAFETY", "PROHIBITED", "RECITATION", "IMAGE")):
        return MediaError(f"Gemini returned no image (finishReason={finish})",
                          category=ErrorCategory.SAFETY, provider=provider, model=model, details={"finishReason": finish})
    return MediaError(f"Gemini returned no image (finishReason={finish})",
                      category=ErrorCategory.PROVIDER, provider=provider, model=model, details={"finishReason": finish})


def _veo_video_uri(res: dict) -> str | None:
    resp = res.get("response") or {}
    gvr = resp.get("generateVideoResponse") or {}
    for sample in gvr.get("generatedSamples") or []:
        video = sample.get("video") or {}
        if video.get("uri"):
            return video["uri"]
    return None


def _write_b64(b64: str, out: Path, kind: str, *, mime: str | None = None, role=None) -> Artifact:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(b64))
    return Artifact.from_path(out, kind, mime=mime or "image/png", role=role)
