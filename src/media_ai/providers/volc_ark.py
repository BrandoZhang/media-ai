"""Volcengine **Ark** provider (Doubao Seedream/Seedance). API-Key (Bearer) auth.

Image generation is synchronous (``/images/generations``); video generation is an
async task (``/contents/generations/tasks`` create → poll → optional cancel).
Migrated from the original ``VolcBackend`` onto the provider-agnostic core.

Ark calls its account-specific wire value an endpoint id (``ep-…``). A config binding
stores it as ``endpoint_id`` beside the credential that can reach it; the adapter
sends that value in Ark's required ``model`` request field rather than reading an
environment variable behind the config's back.
Refs: image https://www.volcengine.com/docs/82379/1541523 ; video create
https://www.volcengine.com/docs/82379/1520757 ; query 1521309 ; cancel 1521720.
"""

from __future__ import annotations

import base64
import signal
import time
from pathlib import Path

from ..core.errors import ErrorCategory, MediaError
from ..core.geometry import normalize_ratio
from ..core.mediaref import to_data_uri
from ..core.result import Artifact, GenerationResult, JobHandle, JobStatus
from ..core.scene import Scene, derive_scene
from ..core.types import ImageRequest, JobRef, VideoRequest
from ._base import HttpAdapter
from ._volc_ark_errors import classify, parse_error_body, task_failure_error, to_media_error

_DEFAULT_TIER = "2K"  # what a request with no geometry asks Ark for
_TERMINAL = {"succeeded", "failed", "cancelled", "canceled", "expired"}


class VolcArkAdapter(HttpAdapter):
    """Ark's wire: images are synchronous, video is a task you create, poll and cancel.

    The binding says which of those this is — its declared scenes do — so nothing here
    guesses a modality from the model id. That guess is what made an ``ep-…`` custom
    endpoint ambiguous: the same id claimed image editing when asked about images and
    video generation when asked about video, because the only input was the caller's
    question. A binding answers it before the call.
    """

    def honoured_flags(self) -> frozenset[str]:
        # Both become body fields below: `sequential_image_generation` and `stream`.
        return frozenset({"group_output", "streaming"})

    def supported_scenes(self) -> frozenset[Scene]:
        return frozenset({
            Scene.IMAGE_TEXT_TO_IMAGE, Scene.IMAGE_IMAGE_TO_IMAGE,
            Scene.VIDEO_TEXT_TO_VIDEO, Scene.VIDEO_IMAGE_TO_VIDEO,
            Scene.VIDEO_KEYFRAME_TO_VIDEO, Scene.VIDEO_REFERENCE_TO_VIDEO,
        })

    @property
    def poll_interval(self) -> float:
        return self.float_option("poll_interval", 5.0)

    @property
    def poll_timeout(self) -> float:
        return self.float_option("poll_timeout", 900.0)

    # ---- images ----------------------------------------------------------
    def _image_size(self, req: ImageRequest) -> str:
        """Pixels when the model accepts them, otherwise a named tier.

        The floor comes from the binding rather than one constant for all of Ark:
        Seedream 4.5 falls back to its 2K preset below 2560x1440, while 5.0 pro accepts
        down to 1280x720. One number for both would silently coarsen a valid request.
        """
        geo = req.geometry
        floor = self.constraints.geometry.pixel_total_min or 0
        if geo and geo.mode == "pixels":
            if geo.width * geo.height >= floor:  # type: ignore[operator]
                return f"{geo.width}x{geo.height}"
            return _DEFAULT_TIER
        # Ark's `size` is a pixel pair or a named tier — there is no field for a bare
        # aspect ratio. Silently falling through to the default tier would bill the
        # caller for a 2K square they did not ask for, which is the same silent geometry
        # substitution the video path was fixed for.
        #
        # Raised here rather than declared as a constraint on purpose: a validator issue
        # is suppressible with `--on-unsupported ignore`, and suppressing this one puts
        # the silent substitution straight back. The wire simply cannot carry the
        # request, so there is nothing to downgrade. Still before any network call, so
        # nothing is charged.
        if geo and geo.aspect_ratio:
            raise MediaError(
                f"{self.binding.id} has no wire field for a bare --aspect-ratio; "
                f"give pixels with --size WxH, or a tier with --resolution",
                category=ErrorCategory.UNSUPPORTED, code="geometry_not_expressible",
                provider=self.name, model=req.model or self.model_id,
                details={"aspect_ratio": geo.aspect_ratio,
                         "named_sizes": list(self.constraints.geometry.named_sizes or ())},
            )
        if geo and geo.resolution:
            return geo.resolution
        return _DEFAULT_TIER

    def generate_image(self, req: ImageRequest) -> GenerationResult:
        client, headers = self._prepare()
        model_id = req.model or self.model_id
        response_format = str(req.options.get("response_format", "url")).lower()
        if response_format not in {"url", "b64_json"}:
            raise MediaError(
                "Ark response_format must be 'url' or 'b64_json'",
                category=ErrorCategory.VALIDATION,
                provider=self.name,
                model=model_id,
            )
        stream_requested = "stream" in req.options
        stream = req.options.get("stream", False)
        if stream_requested and not isinstance(stream, bool):
            raise MediaError(
                "Ark stream must be a boolean",
                category=ErrorCategory.VALIDATION,
                provider=self.name,
                model=model_id,
            )
        body: dict = {
            "model": model_id, "prompt": req.prompt, "size": self._image_size(req),
            "response_format": response_format, "watermark": bool(req.options.get("watermark", False)),
        }
        if req.seed is not None and req.seed >= 0:
            body["seed"] = req.seed
        if req.references:
            enc = [to_data_uri(r, "image") for r in req.references]
            body["image"] = enc if len(enc) > 1 else enc[0]
        # Gated like every other optional wire field: only a binding declaring the
        # formats may receive one. An unrequested parameter is exactly what Ark rejects
        # with InvalidParameter, and only the 5.0 bindings have been run live.
        if req.output_format is not None and self.constraints.output.formats:
            body["output_format"] = req.output_format
        if stream_requested:
            body["stream"] = stream
        # This is a Seedream group-output control, not a harmless global default:
        # Seedream 5.0 Pro rejects it even when set to "disabled".  Only bindings
        # that declare group output may receive either form of the parameter.
        if self.constraints.supports_flag("group_output"):
            if req.count > 1:
                body["sequential_image_generation"] = "auto"
                body["sequential_image_generation_options"] = {"max_images": req.count}
            else:
                body["sequential_image_generation"] = "disabled"

        if stream:
            data = self._streamed_image_response(
                client.request_sse_json("POST", "/images/generations", body=body, headers=headers)
            )
        else:
            data = client.request_json("POST", "/images/generations", body=body, headers=headers)
        items = [d for d in (data.get("data") or []) if d.get("url") or d.get("b64_json")]
        if not items:
            raise MediaError("Ark image response had no images", category=ErrorCategory.PROVIDER,
                             provider=self.name, model=model_id)
        out = Path(req.output)
        artifacts = [self._save_image(items[0], out, client, "image")]
        for i, it in enumerate(items[1:], start=2):
            p = out.with_name(f"{out.stem}_{i}{out.suffix}")
            artifacts.append(self._save_image(it, p, client, "image", role="group"))
        usage = data.get("usage") or {}
        used_model = data.get("model") or model_id
        self.record(derive_scene(req), model=used_model, kind="image",
                    generated_images=usage.get("generated_images", len(items)),
                    output_tokens=usage.get("output_tokens", 0), total_tokens=usage.get("total_tokens", 0))
        return GenerationResult(
            modality="image", provider=self.name, model=used_model,
            artifacts=artifacts, usage=usage, meta={"prompt": req.prompt, "size": body["size"]},
        )

    def _streamed_image_response(self, events: list[dict]) -> dict:
        """Normalize Ark's image SSE events into the ordinary image response shape.

        An instance method so the error it raises can name the binding's provider from
        the binding, like every other error. A literal here would be a second source of
        truth for something that already has one, and it lands in output rather than in
        a failing import.
        """
        images: list[tuple[int, int, dict]] = []
        usage: dict = {}
        model = None
        for position, event in enumerate(events):
            if event.get("error"):
                raise MediaError(
                    f"Ark streamed image generation failed: {event['error']}",
                    category=ErrorCategory.PROVIDER,
                    provider=self.name,
                )
            if event.get("model"):
                model = event["model"]
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            if event.get("url") or event.get("b64_json"):
                index = event.get("image_index")
                images.append((index if isinstance(index, int) else position, position,
                               {k: event[k] for k in ("url", "b64_json") if event.get(k)}))
        images.sort(key=lambda item: (item[0], item[1]))
        return {"data": [image for _, _, image in images], "usage": usage, "model": model}

    @staticmethod
    def _save_image(item: dict, out: Path, client, kind: str, *, role=None) -> Artifact:
        """Write one returned image, inline or downloaded.

        Takes no auth headers on purpose: Ark returns a **pre-signed** URL, and sending
        the Bearer token to it would leak the key to whatever CDN serves it. The
        parameter used to be threaded in and ignored, which read at the call site as
        "downloads are authenticated".
        """
        out.parent.mkdir(parents=True, exist_ok=True)
        if item.get("b64_json"):
            out.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            client.download(item["url"], out)
        return Artifact.from_path(out, kind, mime=VolcArkAdapter._image_mime(out), role=role)

    @staticmethod
    def _image_mime(path: Path) -> str | None:
        """Report the bytes Ark returned, rather than trusting the caller's suffix.

        ``response_format=url`` has historically returned JPEG by default.  Newer
        Seedream 5 bindings honour ``output_format``, but an artifact must still
        describe its actual bytes if a deployment ignores that optional parameter.
        """
        with path.open("rb") as image:
            header = image.read(12)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
            return "image/gif"
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "image/webp"
        return None

    # ---- video (async task) ----------------------------------------------
    def _build_content(self, req: VideoRequest) -> list[dict]:
        content: list[dict] = []
        # Ark's published examples lead with the text instruction, then enumerate
        # Image 1/2, Video 1, Audio 1 in reference order.  Keep that order so prompts
        # can unambiguously refer to those numbered inputs.
        if req.prompt:
            content.append({"type": "text", "text": req.prompt})
        if req.first_frame:
            content.append(
                {"type": "image_url", "image_url": {"url": to_data_uri(req.first_frame, "image")}, "role": "first_frame"}
            )
        if req.last_frame:
            content.append(
                {"type": "image_url", "image_url": {"url": to_data_uri(req.last_frame, "image")}, "role": "last_frame"}
            )
        for r in req.reference_images:
            content.append({"type": "image_url", "image_url": {"url": to_data_uri(r, "image")}, "role": "reference_image"})
        for r in req.reference_videos:
            content.append({"type": "video_url", "video_url": {"url": to_data_uri(r, "video")}, "role": "reference_video"})
        for r in req.reference_audios:
            content.append({"type": "audio_url", "audio_url": {"url": to_data_uri(r, "audio")}, "role": "reference_audio"})
        if not content:
            raise MediaError(
                "video generation needs a prompt or at least one reference",
                category=ErrorCategory.VALIDATION,
                provider=self.name,
            )
        return content

    def _create_task(self, req: VideoRequest, client, headers) -> str:
        geo = req.geometry
        body: dict = {
            "model": req.model or self.model_id,
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
            return JobHandle(provider=self.name, model=req.model or self.model_id, id=task_id, output=str(req.output))
        return self._poll(task_id, Path(req.output), client, headers, scene=derive_scene(req))

    def _finalize(self, res: dict, out: Path, client, headers, *, task_id: str,
                  scene: Scene | None) -> GenerationResult:
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
        used_model = res.get("model") or self.model_id
        self.record(scene, model=used_model, kind="video", seconds=res.get("duration", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0))
        return GenerationResult(
            modality="video", provider=self.name, model=used_model, artifacts=artifacts,
            usage=usage, meta={"task_id": task_id, "seconds": res.get("duration"), "resolution": res.get("resolution")},
        )

    def _cancel(self, task_id: str, client, headers) -> None:
        try:
            client.request_json("DELETE", f"/contents/generations/tasks/{task_id}", headers=headers)
        except Exception:  # noqa: BLE001 - cancellation is best-effort
            pass

    def _poll(self, task_id: str, out: Path, client, headers, *, scene: Scene | None) -> GenerationResult:
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
                    return self._finalize(res, out, client, headers, task_id=task_id, scene=scene)
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
            result = self._finalize(res, Path(output), client, headers, task_id=ref.id, scene=None)
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
