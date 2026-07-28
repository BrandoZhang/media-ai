"""Volcengine **Ark** provider (Doubao Seedream/Seedance). API-Key (Bearer) auth.

Image generation is synchronous (``/images/generations``); video generation is an
async task (``/contents/generations/tasks`` create → poll → optional cancel).
Migrated from the original ``VolcBackend`` onto the provider-agnostic core.

Model IDs are account-specific and must be enabled in the console; set them via
config/env (``ARK_IMAGE_MODEL`` / ``ARK_VIDEO_MODEL``) or per call with ``--model``.
Refs: image https://www.volcengine.com/docs/82379/1541523 ; video create
https://www.volcengine.com/docs/82379/1520757 ; query 1521309 ; cancel 1521720.
"""

from __future__ import annotations

import base64
import os
import signal
import time
from dataclasses import replace
from pathlib import Path

from ..core.capabilities import GeometryMode, ImageCaps, ModelCapabilities, Operation, VideoCaps
from ..core.errors import ErrorCategory, MediaError
from ..core.geometry import normalize_ratio
from ..core.modelspec import apply_spec
from ..core.mediaref import to_data_uri
from ..core.result import Artifact, GenerationResult, JobHandle, JobStatus
from ..core.types import ImageRequest, JobRef, Modality, VideoRequest
from ..core.usage import record_usage
from ._base import HttpProvider
from ._catalog import VOLC
from ._volc_errors import classify, parse_error_body, task_failure_error, to_media_error

_ARK_MIN_IMAGE_PIXELS = 2560 * 1440  # Seedream method-2 floor
_TERMINAL = {"succeeded", "failed", "cancelled", "canceled", "expired"}


class VolcProvider(HttpProvider):
    name = "volc-ark"
    catalog = VOLC
    auth_scheme = "bearer"

    def __init__(self, *, credentials=None, config=None) -> None:
        super().__init__(credentials=credentials, config=config)
        # `or` (not getenv defaults) so a set-but-empty env var (e.g. an unset CI
        # secret, which GitHub materializes as "") falls back to the default.
        self.base_url = (self.config.get("base_url") or os.getenv("ARK_BASE_URL")
                         or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
        self.image_model = self.config.get("image_model") or os.getenv("ARK_IMAGE_MODEL") or "doubao-seedream-4-5-251128"
        self.video_model = self.config.get("video_model") or os.getenv("ARK_VIDEO_MODEL") or "doubao-seedance-2-0-260128"
        self.poll_interval = float(os.getenv("ARK_POLL_INTERVAL", "5") or 5)
        self.poll_timeout = float(os.getenv("ARK_POLL_TIMEOUT", "900") or 900)

    # ---- discovery -------------------------------------------------------
    def models(self) -> list[str]:
        return [self.image_model, self.video_model]

    def default_model(self, modality: Modality | None) -> str:
        return self.video_model if modality == Modality.VIDEO else self.image_model

    @staticmethod
    def _is_endpoint(model: str) -> bool:
        # Ark custom inference endpoint IDs look like ``ep-20260214051115-zrbtw``
        # and encode neither modality nor geometry constraints.
        return model.lower().startswith("ep-")

    def _is_video_model(self, model: str, modality: Modality | None = None) -> bool:
        backing = self.backing_model(model)
        if backing != model:
            # A mapped endpoint is classified by the model it actually serves, so the
            # answer no longer depends on what the caller happened to ask for. Same
            # test as the unmapped path below — a configured video model that is not in
            # the catalogue must be recognised through either route.
            return self._catalog_is_video(backing) or backing == self.video_model
        # Trust the modality the command declared; only guess from the name during
        # discovery (modality is None), which is best-effort.
        if modality is not None:
            return modality == Modality.VIDEO
        return self._catalog_is_video(model) or model == self.video_model

    @staticmethod
    def _catalog_is_video(model: str) -> bool:
        """Modality from the catalogue rather than a `"seedance" in name` test."""
        spec = VOLC.get(model)
        return bool(spec and spec.caps.get("modality") == "video")

    def capabilities(self, model: str | None = None, modality: Modality | None = None) -> ModelCapabilities:
        model = model or self.image_model
        # An endpoint id names a *deployment*, not a model, so it carries no capability
        # information of its own. If the user mapped it (see Provider.backing_model)
        # the real model's constraints apply; otherwise we can't know the underlying
        # version, so leave geometry unconstrained and let the Ark API be the authority
        # rather than pre-rejecting a valid request (fail open, not closed).
        backing = self.backing_model(model)
        endpoint = self._is_endpoint(model) and backing == model
        note = (
            "custom endpoint ID: modality is taken from the requested command and "
            "geometry is validated by the Ark API, not pre-checked"
            if endpoint else None
        )
        if backing != model:
            # Classify by the real model *name*, not the requested modality: the whole
            # point of the mapping is that we no longer have to take the caller's word
            # for what an opaque id can do.
            resolved = self._capabilities_for(backing, None)
            # Inherit the backing model's lifecycle too. Mapping an endpoint onto a
            # deprecated model is exactly when someone needs to be told, and reporting
            # the endpoint as `ga` would hide it behind the indirection.
            apply_spec(resolved, VOLC.get(backing))
            return replace(resolved, model=model, notes=resolved.notes + (f"custom endpoint for {backing}",))
        return apply_spec(self._capabilities_for(model, modality, endpoint=endpoint, note=note),
                          VOLC.get(model))

    def _capabilities_for(
        self, model: str, modality: Modality | None = None, *, endpoint: bool = False, note: str | None = None
    ) -> ModelCapabilities:
        if self._is_video_model(model, modality):
            return ModelCapabilities(
                provider=self.name, model=model, modalities=frozenset({Modality.VIDEO}),
                video=VideoCaps(
                    is_async=True,
                    aspect_ratios=() if endpoint else ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"),
                    resolutions=() if endpoint else ("480p", "720p", "1080p"),
                    durations=(),  # model-version specific; left unconstrained
                    supports_first_frame=True, supports_last_frame=True,
                    supports_reference_images=True, supports_reference_videos=True,
                    supports_reference_audios=True, supports_seed=True, supports_audio=True,
                    supports_watermark_control=True, supports_return_last_frame=True,
                    options=("camera_fixed",),
                ),
                # Notes for a catalogued model come from its spec via apply_spec;
                # repeating them here printed every one twice.
                notes=(note,) if note else (),
            )
        return ModelCapabilities(
            provider=self.name, model=model, modalities=frozenset({Modality.IMAGE}),
            image=ImageCaps(
                operations=frozenset({Operation.IMAGE_GENERATE, Operation.IMAGE_EDIT}),
                geometry_mode=GeometryMode.BOTH,
                aspect_ratios=() if endpoint else ("1:1", "16:9", "9:16", "4:3", "3:4", "21:9"),
                named_sizes=() if endpoint else ("1K", "2K", "4K"),
                pixel_max=None if endpoint else (4096, 4096),
                max_count=15, output_formats=("png",),
                supports_seed=True, max_references=9, options=("watermark",),
            ),
            notes=(note,) if note else (),
        )

    # ---- images ----------------------------------------------------------
    def _image_size(self, req: ImageRequest) -> str:
        override = os.getenv("ARK_IMAGE_SIZE")
        if override:
            return override
        geo = req.geometry
        if geo and geo.mode == "pixels":
            if geo.width * geo.height >= _ARK_MIN_IMAGE_PIXELS:  # type: ignore[operator]
                return f"{geo.width}x{geo.height}"
            return "2K"
        if geo and geo.resolution:
            return geo.resolution
        return "2K"

    def generate_image(self, req: ImageRequest) -> GenerationResult:
        client, headers = self._prepare()
        model_id = req.model or self.image_model
        body: dict = {
            "model": model_id, "prompt": req.prompt, "size": self._image_size(req),
            "response_format": "url", "watermark": bool(req.options.get("watermark", False)),
        }
        if req.seed is not None and req.seed >= 0:
            body["seed"] = req.seed
        if req.references:
            enc = [to_data_uri(r, "image") for r in req.references]
            body["image"] = enc if len(enc) > 1 else enc[0]
        if req.count > 1:
            body["sequential_image_generation"] = "auto"
            body["sequential_image_generation_options"] = {"max_images": req.count}
        else:
            body["sequential_image_generation"] = "disabled"

        data = client.request_json("POST", "/images/generations", body=body, headers=headers)
        items = [d for d in (data.get("data") or []) if d.get("url") or d.get("b64_json")]
        if not items:
            raise MediaError("Ark image response had no images", category=ErrorCategory.PROVIDER,
                             provider=self.name, model=model_id)
        out = Path(req.output)
        artifacts = [self._save_image(items[0], out, client, headers, "image")]
        for i, it in enumerate(items[1:], start=2):
            p = out.with_name(f"{out.stem}_{i}{out.suffix}")
            artifacts.append(self._save_image(it, p, client, headers, "image", role="group"))
        usage = data.get("usage") or {}
        used_model = data.get("model") or model_id
        record_usage({"tool": req.operation.value, "operation": req.operation.value, "provider": self.name,
                      "model": used_model, "kind": "image",
                      "generated_images": usage.get("generated_images", len(items)),
                      "output_tokens": usage.get("output_tokens", 0), "total_tokens": usage.get("total_tokens", 0)})
        return GenerationResult(
            modality="image", operation=req.operation.value, provider=self.name, model=used_model,
            artifacts=artifacts, usage=usage, meta={"prompt": req.prompt, "size": body["size"]},
        )

    @staticmethod
    def _save_image(item: dict, out: Path, client, headers, kind: str, *, role=None) -> Artifact:
        out.parent.mkdir(parents=True, exist_ok=True)
        if item.get("b64_json"):
            out.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            client.download(item["url"], out)
        return Artifact.from_path(out, kind, mime="image/png", role=role)

    # ---- video (async task) ----------------------------------------------
    def _build_content(self, req: VideoRequest) -> list[dict]:
        content: list[dict] = []
        if req.first_frame:
            content.append({"type": "image_url", "image_url": {"url": to_data_uri(req.first_frame, "image")}, "role": "first_frame"})
        if req.last_frame:
            content.append({"type": "image_url", "image_url": {"url": to_data_uri(req.last_frame, "image")}, "role": "last_frame"})
        for r in req.reference_images:
            content.append({"type": "image_url", "image_url": {"url": to_data_uri(r, "image")}, "role": "reference_image"})
        for r in req.reference_videos:
            content.append({"type": "video_url", "video_url": {"url": to_data_uri(r, "video")}, "role": "reference_video"})
        for r in req.reference_audios:
            content.append({"type": "audio_url", "audio_url": {"url": to_data_uri(r, "audio")}, "role": "reference_audio"})
        if not content and not req.prompt:
            raise MediaError("video generation needs a prompt or at least one reference", category=ErrorCategory.VALIDATION, provider=self.name)
        if req.prompt:
            content.append({"type": "text", "text": req.prompt})
        return content

    def _create_task(self, req: VideoRequest, client, headers) -> str:
        geo = req.geometry
        body: dict = {
            "model": req.model or self.video_model,
            "content": self._build_content(req),
            "resolution": (geo.resolution if geo else None) or "720p",
            "ratio": normalize_ratio(geo.aspect_ratio if geo else None) or "adaptive",
            "duration": req.duration or 5,
            "watermark": bool(req.watermark) if req.watermark is not None else False,
        }
        # camera_fixed is only sent when the caller explicitly set it via
        # `--option camera_fixed=...`; some models reject the parameter otherwise
        # (InvalidParameter camera_fixed). Don't force a default.
        if "camera_fixed" in req.options:
            body["camera_fixed"] = bool(req.options["camera_fixed"])
        if req.seed is not None and req.seed >= 0:
            body["seed"] = req.seed
        if req.audio is not None:
            body["generate_audio"] = req.audio
        if req.return_last_frame:
            body["return_last_frame"] = True
        data = client.request_json("POST", "/contents/generations/tasks", body=body, headers=headers)
        task_id = data.get("id")
        if not task_id:
            raise MediaError("Ark video create returned no task id", category=ErrorCategory.PROVIDER, provider=self.name)
        return task_id

    def generate_video(self, req: VideoRequest):
        client, headers = self._prepare()
        task_id = self._create_task(req, client, headers)
        if not req.wait:
            return JobHandle(provider=self.name, model=req.model or self.video_model, id=task_id, output=str(req.output))
        return self._poll(task_id, Path(req.output), client, headers, operation=req.operation.value)

    def _finalize(self, res: dict, out: Path, client, headers, *, task_id: str, operation: str) -> GenerationResult:
        content = res.get("content") or {}
        if not content.get("video_url"):
            raise MediaError(f"Ark task {task_id} succeeded but returned no video_url",
                             category=ErrorCategory.PROVIDER, provider=self.name)
        client.download(content["video_url"], out, headers=None)
        artifacts = [Artifact.from_path(out, "video", mime="video/mp4")]
        if content.get("last_frame_url"):
            lf = out.with_name(f"{out.stem}_lastframe.png")
            client.download(content["last_frame_url"], lf)
            artifacts.append(Artifact.from_path(lf, "frame", mime="image/png", role="last_frame"))
        usage = res.get("usage") or {}
        used_model = res.get("model") or self.video_model
        record_usage({"tool": operation, "operation": operation, "provider": self.name, "model": used_model,
                      "kind": "video", "seconds": res.get("duration", 0),
                      "completion_tokens": usage.get("completion_tokens", 0), "total_tokens": usage.get("total_tokens", 0)})
        return GenerationResult(
            modality="video", operation=operation, provider=self.name, model=used_model, artifacts=artifacts,
            usage=usage, meta={"task_id": task_id, "seconds": res.get("duration"), "resolution": res.get("resolution")},
        )

    def _cancel(self, task_id: str, client, headers) -> None:
        try:
            client.request_json("DELETE", f"/contents/generations/tasks/{task_id}", headers=headers)
        except Exception:  # noqa: BLE001 - cancellation is best-effort
            pass

    def _poll(self, task_id: str, out: Path, client, headers, *, operation: str) -> GenerationResult:
        # The harness may kill this (blocking) tool at its action_timeout; cancel
        # the billed task on signal/timeout so a killed wait doesn't orphan it.
        def _on_signal(signum, _frame):
            self._cancel(task_id, client, headers)
            raise MediaError(f"Ark video task {task_id} interrupted (signal {signum}); task cancelled",
                             category=ErrorCategory.TIMEOUT, provider=self.name)

        prev = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                prev[sig] = signal.signal(sig, _on_signal)
            except (ValueError, OSError):
                pass
        try:
            deadline = time.monotonic() + self.poll_timeout
            while time.monotonic() < deadline:
                res = client.request_json("GET", f"/contents/generations/tasks/{task_id}", headers=headers)
                status = str(res.get("status", "")).lower()
                if status == "succeeded":
                    return self._finalize(res, out, client, headers, task_id=task_id, operation=operation)
                if status in _TERMINAL:
                    raise task_failure_error(res, self.name, task_id)
                time.sleep(self.poll_interval)
            self._cancel(task_id, client, headers)
            raise MediaError(f"Ark video task {task_id} timed out after {self.poll_timeout}s (cancelled)",
                             category=ErrorCategory.TIMEOUT, provider=self.name)
        finally:
            for sig, handler in prev.items():
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):
                    pass

    # ---- jobs ------------------------------------------------------------
    def get_job(self, ref: JobRef, *, output: Path | None = None) -> JobStatus:
        client, headers = self._prepare()
        res = client.request_json("GET", f"/contents/generations/tasks/{ref.id}", headers=headers)
        status = str(res.get("status", "")).lower()
        if status in ("failed", "expired"):
            # A terminal failure (e.g. output-safety block) is reported as the
            # matching categorized error + exit code, consistent with the blocking path.
            raise task_failure_error(res, self.name, ref.id)
        result = None
        if output is not None and status == "succeeded":
            result = self._finalize(res, Path(output), client, headers, task_id=ref.id, operation="video.generate")
        return JobStatus(provider=self.name, model=res.get("model"), id=ref.id, status=status or "unknown",
                         op="query", result=result, raw={k: v for k, v in res.items() if k in ("id", "usage", "error")})

    def cancel_job(self, ref: JobRef) -> JobStatus:
        client, headers = self._prepare()
        client.request_json("DELETE", f"/contents/generations/tasks/{ref.id}", headers=headers)
        return JobStatus(provider=self.name, model=None, id=ref.id, status="cancelled", op="cancel",
                         raw={"note": "cancel/delete requested"})

    # ---- errors ----------------------------------------------------------
    def _error(self, status: int, body: str) -> MediaError:
        return to_media_error(status, body, self.name)

    def retry_classifier(self, status: int, body: str) -> bool:
        """Veto a pointless retry: a 429 QuotaExceeded/SetLimitExceeded is a hard
        cap (don't retry), while a transient RPM/TPM 429 stays retryable."""
        code, message, _ = parse_error_body(body)
        return classify(code or "", status, message)[1] is not False
