"""Google Gemini provider — native image (``generateContent``) and Veo video
(``:predictLongRunning``).

Two wire shapes behind one adapter:
  * **Nano Banana native image** (``gemini-3.1-flash-image`` / ``-flash-lite-image``
    / ``gemini-3-pro-image`` / legacy ``gemini-2.5-flash-image``): conversational,
    multimodal edit/compose with up to 14 reference images, optional Google Search
    grounding, and (3.1 Flash) ``thinking_level`` control. Base64 image out inline;
    synchronous.
  * **Veo** (``veo-3.1-*``): async long-running operation → poll → **download the
    file URI with the API key**. First/last frame, up to 3 reference images, and
    video extension on Veo 3.1.

Notable Gemini quirks handled here: a 200-OK response that carries *no image* is a
silent safety drop (surfaced as a ``SAFETY`` error, not an empty file); SynthID
watermarking is unconditional; ``generateAudio`` is unreliable on the Developer API
(Veo 3.x audio is native). Auth: ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from ..core.capabilities import GeometryMode, ImageCaps, ModelCapabilities, Operation, VideoCaps
from ..core.errors import ErrorCategory, MediaError
from ..core.mediaref import read_bytes
from ..core.result import Artifact, GenerationResult, JobHandle, JobStatus
from ..core.types import ImageRequest, JobRef, MediaRef, Modality, VideoRequest
from ..core.usage import record_usage
from ..media import pillow
from ._base import HttpProvider

# The 10 aspect ratios every Nano Banana model shares. Gemini 3.1 Flash Image adds
# the four extreme banner ratios; Pro / Lite / 2.5 stay on the standard set.
_STD_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
_ULTRAWIDE_RATIOS = ("1:4", "4:1", "1:8", "8:1")
_FLASH_RATIOS = _STD_RATIOS + _ULTRAWIDE_RATIOS


def _family(model: str) -> str:
    m = model.lower()
    if m.startswith("veo"):
        return "veo"
    if m.startswith("imagen"):
        return "imagen"  # dropped; routed here only to return a clear removal error
    return "native"


def _native_tier(model: str) -> str:
    """Classify a Nano Banana model id into its capability tier.

    ``gemini-3.1-flash-lite-image`` → ``lite``; ``gemini-3-pro-image`` → ``pro``;
    ``gemini-2.5-flash-image`` → ``legacy``; everything else (``gemini-3.1-flash-image``
    and future defaults) → ``flash``.
    """
    m = model.lower()
    if "lite" in m:
        return "lite"
    if "pro" in m:
        return "pro"
    if "2.5" in m:
        return "legacy"
    return "flash"


class GeminiProvider(HttpProvider):
    name = "gemini"
    auth_scheme = "x-goog"

    def __init__(self, *, credentials=None, config=None) -> None:
        super().__init__(credentials=credentials, config=config)
        self.base_url = (self.config.get("base_url") or os.getenv("GEMINI_BASE_URL")
                         or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        # Nano Banana 2 (gemini-3.1-flash-image) is Google's recommended go-to image
        # model; Veo 3.1 supersedes the deprecated Veo 2/3.0 line.
        self.image_model = os.getenv("GEMINI_IMAGE_MODEL") or "gemini-3.1-flash-image"
        self.video_model = os.getenv("GEMINI_VIDEO_MODEL") or "veo-3.1-generate-preview"
        self.poll_interval = float(os.getenv("GEMINI_POLL_INTERVAL", "10") or 10)
        self.poll_timeout = float(os.getenv("GEMINI_POLL_TIMEOUT", "1200") or 1200)

    # ---- discovery -------------------------------------------------------
    def models(self) -> list[str]:
        # Deprecated snapshots (veo-2.0 / veo-3.0) still resolve via --model and
        # capabilities(), but are omitted from discovery in favour of the current
        # Nano Banana + Veo 3.1 lineup.
        return ["gemini-3.1-flash-image", "gemini-3.1-flash-lite-image", "gemini-3-pro-image",
                "gemini-2.5-flash-image", "veo-3.1-generate-preview",
                "veo-3.1-fast-generate-preview", "veo-3.1-lite-generate-preview"]

    def default_model(self, modality: Modality | None) -> str:
        return self.video_model if modality == Modality.VIDEO else self.image_model

    def capabilities(self, model: str | None = None, modality: Modality | None = None) -> ModelCapabilities:
        model = model or self.image_model
        fam = _family(model)
        if fam == "veo":
            return self._veo_caps(model)
        if fam == "imagen":
            raise _imagen_removed(model)
        return self._native_caps(model)

    def _veo_caps(self, model: str) -> ModelCapabilities:
        m = model.lower()
        v31 = m.startswith("veo-3.1")
        lite = "lite" in m
        v2 = m.startswith("veo-2")
        full_31 = v31 and not lite  # Veo 3.1 & 3.1 Fast: references, extension, 4K
        if v2:
            resolutions, durations, audio = ("720p",), (5, 6, 7, 8), False
        elif lite:
            resolutions, durations, audio = ("720p", "1080p"), (4, 6, 8), True
        else:  # Veo 3.x (incl. deprecated 3.0) & Fast
            resolutions, durations, audio = ("720p", "1080p", "4k"), (4, 6, 8), True
        if full_31:
            note = ("Veo 3.1: first/last frame, up to 3 reference images (--reference-image), and video "
                    "extension (--reference-video continues a Veo clip ≤141s); durationSeconds must be 8 "
                    "for extension/reference-images/1080p/4K")
        elif lite:
            note = "Veo 3.1 Lite: text/image-to-video, 720p/1080p; no reference images, extension, or 4K"
        else:
            note = "deprecated snapshot — prefer veo-3.1-generate-preview"
        return ModelCapabilities(
            provider=self.name, model=model, modalities=frozenset({Modality.VIDEO}),
            experimental=m.endswith("preview"),
            video=VideoCaps(
                is_async=True, aspect_ratios=("16:9", "9:16"),
                resolutions=resolutions, durations=durations,
                supports_first_frame=True, supports_last_frame=v31,
                supports_reference_images=full_31, supports_reference_videos=full_31,
                supports_seed=True, supports_negative_prompt=True,
                supports_audio=audio, audio_default=audio or None,
                supports_cancel=False, options=("person_generation",),
            ),
            notes=(note, "SynthID watermark is unconditional; on the Developer API generateAudio is unreliable "
                   "(Veo 3.x audio is native, Veo 2 is silent); jobs cannot be cancelled"),
        )

    def _native_caps(self, model: str) -> ModelCapabilities:
        tier = _native_tier(model)
        if tier == "lite":
            ratios, sizes, max_refs, options = _STD_RATIOS, ("1K",), 14, ()
            note = "Nano Banana 2 Lite: 1K only, no grounding; tuned for speed and scale"
        elif tier == "pro":
            ratios, sizes, max_refs, options = _STD_RATIOS, ("1K", "2K", "4K"), 14, ("grounding",)
            note = ("Nano Banana Pro: 1K/2K/4K, Google Search grounding, interleaved output; "
                    "thinking is always on")
        elif tier == "legacy":
            ratios, sizes, max_refs, options = _STD_RATIOS, ("1K",), 3, ()
            note = "Nano Banana (2.5, legacy): imageSize fixed at 1K, up to 3 refs; prefer gemini-3.1-flash-image"
        else:  # flash — Nano Banana 2
            ratios, sizes, max_refs = _FLASH_RATIOS, ("512", "1K", "2K", "4K"), 14
            options = ("grounding", "thinking_level")
            note = ("Nano Banana 2: 512px/1K/2K/4K, Google Search grounding, "
                    "thinking_level minimal|high, video-to-image")
        return ModelCapabilities(
            provider=self.name, model=model, modalities=frozenset({Modality.IMAGE}),
            image=ImageCaps(
                operations=frozenset({Operation.IMAGE_GENERATE, Operation.IMAGE_EDIT}),
                geometry_mode=GeometryMode.ASPECT_RATIO, aspect_ratios=ratios,
                named_sizes=sizes, max_count=4, output_formats=("png", "jpeg", "webp"),
                max_references=max_refs, options=options,
            ),
            notes=(note, "native conversational image gen/edit; SynthID watermark is unconditional"),
        )

    # ---- images ----------------------------------------------------------
    def generate_image(self, req: ImageRequest) -> GenerationResult:
        model = req.model or self.image_model
        if _family(model) == "imagen":
            raise _imagen_removed(model)
        return self._native(model, req)

    def _native(self, model: str, req: ImageRequest) -> GenerationResult:
        client, headers = self._prepare()
        parts: list[dict] = [{"text": req.prompt}]
        for r in req.references:
            b64, mime = _inline_media(r)
            parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        gen_cfg: dict = {"responseModalities": ["TEXT", "IMAGE"]}
        if req.count > 1:
            gen_cfg["candidateCount"] = req.count
        if req.options.get("thinking_level"):
            # 3.1 Flash Image: minimal (default) | high — trade latency for quality.
            gen_cfg["thinkingConfig"] = {"thinkingLevel": req.options["thinking_level"]}
        img_cfg = {}
        if req.geometry and req.geometry.aspect_ratio:
            img_cfg["aspectRatio"] = req.geometry.aspect_ratio
        if req.geometry and req.geometry.resolution:
            img_cfg["imageSize"] = req.geometry.resolution
        if img_cfg:
            gen_cfg["imageConfig"] = img_cfg
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}
        tools = _grounding_tools(req)
        if tools:
            body["tools"] = tools
        data = client.request_json("POST", f"/models/{model}:generateContent", body=body, headers=headers)
        images = _extract_inline_images(data)
        if not images:
            raise _no_image_error(data, self.name, model)
        out = Path(req.output)
        artifacts = [_write_b64(images[0][0], out, "image", source_mime=images[0][1])]
        for i, (b64, mime) in enumerate(images[1:], start=2):
            artifacts.append(_write_b64(b64, out.with_name(f"{out.stem}_{i}{out.suffix}"), "image",
                                        source_mime=mime, role="group"))
        usage = data.get("usageMetadata") or {}
        record_usage({"tool": req.operation.value, "operation": req.operation.value, "provider": self.name,
                      "model": model, "kind": "image", "generated_images": len(images),
                      "output_tokens": usage.get("candidatesTokenCount", 0), "total_tokens": usage.get("totalTokenCount", 0)})
        return GenerationResult(modality="image", operation=req.operation.value, provider=self.name, model=model,
                                artifacts=artifacts, usage=usage, meta={"prompt": req.prompt})

    # ---- video (Veo long-running op) -------------------------------------
    def generate_video(self, req: VideoRequest):
        client, headers = self._prepare()
        model = req.model or self.video_model
        instance: dict = {"prompt": req.prompt}
        if req.first_frame:
            instance["image"] = _veo_media(req.first_frame)
        if req.last_frame:
            instance["lastFrame"] = _veo_media(req.last_frame)
        if req.reference_images:
            # Up to 3 asset references (person/character/product) preserved in the clip.
            instance["referenceImages"] = [{"image": _veo_media(r), "referenceType": "asset"}
                                           for r in req.reference_images]
        if req.reference_videos:
            # Veo extension continues a previously generated Veo clip, referenced by
            # its URI (valid ~2 days). The API rejects inline video bytes for
            # extension ("Video URI not found"), so a local file cannot be used here.
            ref = req.reference_videos[0]
            if ref.is_local:
                raise MediaError(
                    "Veo video extension needs the URI of a previously generated Veo clip — pass the "
                    "operation's video URI as --reference-video; the API does not accept an inline/local "
                    "video for extension",
                    category=ErrorCategory.VALIDATION, provider=self.name,
                )
            instance["video"] = {"uri": ref.raw, "mimeType": "video/mp4"}
        params: dict = {}
        if req.geometry and req.geometry.aspect_ratio and req.geometry.aspect_ratio != "adaptive":
            params["aspectRatio"] = req.geometry.aspect_ratio
        if req.geometry and req.geometry.resolution:
            params["resolution"] = req.geometry.resolution
        if req.duration:
            params["durationSeconds"] = req.duration
        if req.negative_prompt:
            params["negativePrompt"] = req.negative_prompt
        if req.seed is not None and req.seed >= 0:
            params["seed"] = req.seed
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
            raise _operation_error(res["error"], op_name, self.name)
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
        if done and res.get("error"):
            # a done-with-error operation is a failure, not a success
            raise _operation_error(res["error"], ref.id, self.name)
        result = None
        if done and output is not None:
            result = self._finalize_video(client, headers, ref.id, Path(output), ref.model or self.video_model, res)
        return JobStatus(provider=self.name, model=ref.model, id=ref.id,
                         status="succeeded" if done else "running", op="query", result=result)

    def cancel_job(self, ref: JobRef) -> JobStatus:
        raise MediaError("Veo long-running operations cannot be cancelled on the Gemini Developer API",
                         category=ErrorCategory.UNSUPPORTED, provider=self.name)

    # ---- errors ----------------------------------------------------------
    def _error(self, status: int, body: str) -> MediaError:
        """Map a Gemini HTTP error to the shared taxonomy.

        Prefers Google's structured ``{"error": {"code","message","status"}}`` body
        (so ``FAILED_PRECONDITION``, ``RESOURCE_EXHAUSTED``, ``DEADLINE_EXCEEDED`` …
        classify precisely) and falls back to the HTTP status when it can't parse.
        """
        # The HTTP status/canonical status is authoritative here: a Gemini content
        # block surfaces on the 200-OK path (see `_no_image_error`), so an HTTP error
        # is a transport/validation failure and a message that merely mentions
        # "safety"/"blocked" (e.g. a malformed `safetySettings` field) stays as its
        # status category rather than being miscast as a safety block.
        gstatus, message, _ = _parse_error(body)
        cat, hint = _categorize(gstatus, message, status)
        label = gstatus or f"HTTP {status}"
        msg = f"Gemini {label}: {message}"
        if hint:
            msg += f" — {hint}"
        details = {"status": status}
        if gstatus:
            details["google_status"] = gstatus
        return MediaError(msg, category=cat, code=gstatus or None, provider=self.name, details=details)

    def retry_classifier(self, status: int, body: str) -> bool:
        """Veto a pointless 429 retry. Per-minute (RPM/TPM) limits clear on a short
        backoff and stay retryable; a per-day or spend/billing cap will not reset for
        hours, so don't burn the retry budget on it."""
        if status != 429:
            return True
        low = body.lower()
        return not any(k in low for k in ("per day", "per_day", "perday", "requests per day",
                                          "daily limit", "free_tier", "free tier", "billing"))


# --------------------------------------------------------------------------
# request builders
# --------------------------------------------------------------------------


# The Gemini Developer API caps a single request's inline payload at ~20 MB. The
# Files-API upload path for larger media is not wired up, so we reject oversized
# inline media with an actionable error rather than let the API fail opaquely.
_INLINE_LIMIT = 20 * 1024 * 1024


def _inline_media(ref: MediaRef) -> tuple[str, str]:
    """Read a local ref as ``(base64, mime)``, rejecting media over the inline cap."""
    data, mime = read_bytes(ref)
    if len(data) > _INLINE_LIMIT:
        raise MediaError(
            f"inline media {ref.raw!r} is ~{len(data) // (1024 * 1024)}MB, over Gemini's "
            "~20MB inline-request limit — use a smaller file (Files API upload for large "
            "media is not yet supported)",
            category=ErrorCategory.VALIDATION, provider="gemini",
        )
    return base64.b64encode(data).decode("ascii"), mime


def _veo_media(ref: MediaRef) -> dict:
    """Inline a local image/video ref for a Veo ``:predictLongRunning`` instance."""
    b64, mime = _inline_media(ref)
    return {"bytesBase64Encoded": b64, "mimeType": mime}


def _grounding_tools(req: ImageRequest) -> list[dict]:
    """Build the Google Search tool for a grounded image request, if requested.

    ``--option grounding=true`` enables Google Search grounding (web + image search
    is handled server-side). Supported on the Flash and Pro models.
    """
    return [{"google_search": {}}] if req.options.get("grounding") else []


def _imagen_removed(model: str) -> MediaError:
    """Imagen was dropped (deprecated by Google); point callers at Nano Banana."""
    return MediaError(
        f"Imagen model {model!r} is no longer supported (deprecated by Google); "
        "use a Nano Banana model such as gemini-3.1-flash-image",
        category=ErrorCategory.UNSUPPORTED, provider="gemini", model=model,
    )


# --------------------------------------------------------------------------
# error classification
# --------------------------------------------------------------------------

# Google's canonical error `status` string -> shared taxonomy. Mirrors the
# backend error-code table from the Gemini troubleshooting guide.
_STATUS_CATEGORY = {
    "INVALID_ARGUMENT": ErrorCategory.VALIDATION,
    "FAILED_PRECONDITION": ErrorCategory.VALIDATION,  # free-tier/region/billing (400)
    "OUT_OF_RANGE": ErrorCategory.VALIDATION,
    "UNAUTHENTICATED": ErrorCategory.AUTH,
    "PERMISSION_DENIED": ErrorCategory.AUTH,
    "NOT_FOUND": ErrorCategory.NOT_FOUND,
    "RESOURCE_EXHAUSTED": ErrorCategory.RATE_LIMIT,  # RPM/TPM/RPD/spend (429)
    "CANCELLED": ErrorCategory.TIMEOUT,  # client closed the connection (499)
    "DEADLINE_EXCEEDED": ErrorCategory.TIMEOUT,  # server couldn't finish in time (504)
    "ABORTED": ErrorCategory.PROVIDER,
    "INTERNAL": ErrorCategory.PROVIDER,  # unexpected server error (500)
    "UNAVAILABLE": ErrorCategory.PROVIDER,  # temporarily overloaded/down (503)
    "UNKNOWN": ErrorCategory.PROVIDER,
}

# Long-running Veo operations report failures as a google.rpc.Status with a
# numeric gRPC `code` rather than the string `status`; map it back.
_GRPC_STATUS = {
    1: "CANCELLED", 3: "INVALID_ARGUMENT", 4: "DEADLINE_EXCEEDED", 5: "NOT_FOUND",
    7: "PERMISSION_DENIED", 8: "RESOURCE_EXHAUSTED", 9: "FAILED_PRECONDITION",
    10: "ABORTED", 13: "INTERNAL", 14: "UNAVAILABLE", 16: "UNAUTHENTICATED",
}

# Fallback when the body carries no canonical status (429/408/504 are transient).
_HTTP_CATEGORY = {
    400: ErrorCategory.VALIDATION, 401: ErrorCategory.AUTH, 403: ErrorCategory.AUTH,
    404: ErrorCategory.NOT_FOUND, 408: ErrorCategory.TIMEOUT, 429: ErrorCategory.RATE_LIMIT,
    499: ErrorCategory.TIMEOUT, 504: ErrorCategory.TIMEOUT,
}


def _parse_error(body: str) -> tuple[str, str, int | None]:
    """Extract ``(status_string, message, code)`` from a Gemini error body.

    Tolerant of the ``{"error": {"code","message","status"}}`` shape (HTTP errors
    and google.rpc.Status alike) and of plain text. ``code`` is the numeric field as
    sent (an HTTP status for REST errors, a gRPC code for operation errors)."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return "", body, None
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, str):
        return "", err, None
    if not isinstance(err, dict):
        return "", body if isinstance(body, str) else str(body), None
    code = err.get("code")
    status = str(err.get("status") or "")
    if not status and isinstance(code, int) and code < 100:
        status = _GRPC_STATUS.get(code, "")
    return status, err.get("message") or body, code if isinstance(code, int) else None


def _is_safety(message: str) -> bool:
    return any(k in (message or "").lower() for k in ("safety", "blocked", "prohibited"))


def _categorize(gstatus: str, message: str, http_status: int | None) -> tuple[ErrorCategory, str | None]:
    """Map a Google status (or HTTP status) to ``(category, hint)``.

    The canonical status is authoritative — this does *not* keyword-guess safety, so
    an ``INVALID_ARGUMENT`` stays ``VALIDATION`` even if its message mentions
    "safety"/"blocked" (callers that see real block reasons opt into that via
    :func:`_is_safety`). A reported-leaked key is the one unambiguous message signal.
    """
    if "leaked" in (message or "").lower():
        return ErrorCategory.AUTH, "key reported as leaked — generate a new one in Google AI Studio"
    cat = _STATUS_CATEGORY.get(gstatus)
    if cat is None:
        cat = _HTTP_CATEGORY.get(http_status, ErrorCategory.PROVIDER) if http_status else ErrorCategory.PROVIDER
    hint = None
    if gstatus == "FAILED_PRECONDITION":
        hint = "free tier may be unavailable in your region — enable billing in Google AI Studio"
    elif cat == ErrorCategory.RATE_LIMIT:
        hint = "rate/quota limit — back off and retry, or request a higher limit"
    elif cat == ErrorCategory.TIMEOUT:
        hint = "increase the client timeout (GEMINI_POLL_TIMEOUT for video) or reduce input size"
    return cat, hint


def _operation_error(err, op_name: str, provider: str) -> MediaError:
    """Classify a terminal Veo long-running-operation failure (``res['error']``).

    Unlike an HTTP request error, a finished-operation failure carries its real
    reason in the message, so a safety-worded failure is a genuine content block.
    """
    if isinstance(err, dict):
        code = err.get("code")
        message = err.get("message") or str(err)
        gstatus = str(err.get("status") or "") or (_GRPC_STATUS.get(code, "") if isinstance(code, int) else "")
    else:
        code, gstatus, message = None, "", str(err)
    if _is_safety(message):
        cat, hint = ErrorCategory.SAFETY, None
    else:
        cat, hint = _categorize(gstatus, message, None)
    label = gstatus or (f"code {code}" if code is not None else "error")
    msg = f"Veo operation {op_name} failed: [{label}] {message}"[:400]
    if hint:
        msg += f" — {hint}"
    details = {"operation": op_name}
    if gstatus:
        details["google_status"] = gstatus
    if code is not None:
        details["code"] = code
    return MediaError(msg, category=cat, code=gstatus or None, provider=provider, details=details)


# --------------------------------------------------------------------------
# response parsing helpers
# --------------------------------------------------------------------------


def _extract_inline_images(data: dict) -> list[tuple[str, str]]:
    """Return ``(base64, mimeType)`` for each output image, in order."""
    out: list[tuple[str, str]] = []
    for cand in data.get("candidates") or []:
        for part in ((cand.get("content") or {}).get("parts") or []):
            # Thinking models emit interim "thought" images; they are not the final
            # output and must not be saved as artifacts.
            if part.get("thought"):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                out.append((inline["data"], inline.get("mimeType") or inline.get("mime_type") or ""))
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


def _write_b64(b64: str, out: Path, kind: str, *, source_mime: str = "", role=None) -> Artifact:
    # Gemini 3.x image models return JPEG by default; honor the caller's output
    # extension (transcoding if needed) so the file and its reported mime match.
    mime = pillow.save_image_bytes(base64.b64decode(b64), out, source_mime=source_mime)
    return Artifact.from_path(out, kind, mime=mime, role=role)
