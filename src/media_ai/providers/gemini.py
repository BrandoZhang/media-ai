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
import re
import time
from pathlib import Path

from ..core.errors import ErrorCategory, MediaError
from ..core.mediaref import read_bytes
from ..core.scene import Scene, derive_scene
from ..core.result import Artifact, GenerationResult, JobHandle, JobStatus
from ..core.types import DialogueRequest, ImageRequest, JobRef, MediaRef, SpeechRequest, VideoRequest
from ..media import ffmpeg, pillow
from ..media.audio import write_pcm_wav
from . import _gemini_files
from ._base import HttpAdapter

# The 30 prebuilt TTS voices (style/tone/pace are directed via the prompt text /
# --instruction, not a parameter). Which models exist, and their aspect-ratio sets,
# live in the binding manifests — see bindings/gemini.toml.
_GEMINI_VOICES = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
    "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib",
    "Rasalgethi", "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird",
    "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)


class GeminiAdapter(HttpAdapter):

    def honoured_flags(self) -> frozenset[str]:
        # `candidateCount` and the Search grounding tool.
        return frozenset({"group_output", "grounding"})

    def supported_scenes(self) -> frozenset[Scene]:
        return frozenset({
            Scene.IMAGE_TEXT_TO_IMAGE, Scene.IMAGE_IMAGE_TO_IMAGE,
            Scene.VIDEO_TEXT_TO_VIDEO, Scene.VIDEO_IMAGE_TO_VIDEO,
            Scene.VIDEO_KEYFRAME_TO_VIDEO, Scene.VIDEO_REFERENCE_TO_VIDEO, Scene.VIDEO_EXTEND,
            Scene.SPEECH_TEXT_TO_SPEECH, Scene.SPEECH_DIALOGUE,
        })

    @property
    def poll_interval(self) -> float:
        return self.float_option("poll_interval", 10.0)

    @property
    def poll_timeout(self) -> float:
        return self.float_option("poll_timeout", 1200.0)

    @property
    def inline_max_bytes(self) -> int:
        """Above this, a local reference is uploaded via the Files API and passed by URI.

        Per binding rather than global: the inline ceiling is a property of the endpoint
        being called, and a deployment behind a proxy with a smaller body limit needs to
        say so without changing anyone else's.
        """
        return self.int_option("inline_max_bytes", 12 * 1024 * 1024)

    def _require_direct_key(self, headers: dict, ref: MediaRef, size: int) -> None:
        """Refuse a Files-API upload this binding's credential cannot authenticate.

        The resumable upload goes to Google's own ``/upload/v1beta/files`` endpoint with
        the API key in a header, and a broker forwards only ``generateContent`` — so a
        brokered binding would send a keyless request and collect a 401 naming nothing the
        caller can act on. It is a deterministic outcome of how the binding is configured,
        so say that up front, with the two things that actually resolve it.
        """
        if not self.brokered(headers):
            return
        raise MediaError(
            f"reference {ref.raw!r} is ~{size // (1024 * 1024)}MB, over this binding's "
            f"{self.inline_max_bytes // (1024 * 1024)}MB inline ceiling, and the Gemini Files API "
            "upload it would need cannot be routed through a credential broker",
            category=ErrorCategory.UNSUPPORTED, code="broker_upload_unsupported", provider=self.name,
            details={"bytes": size, "inline_max_bytes": self.inline_max_bytes},
            hint="pass a smaller reference, or configure this binding with a direct key "
                 "(media-ai bindings add gemini/<model> --credential env://GEMINI_API_KEY)",
        )

    def generate_image(self, req: ImageRequest) -> GenerationResult:
        model = req.model or self.model_id
        return self._native(model, req)

    def _native(self, model: str, req: ImageRequest) -> GenerationResult:
        client, headers = self._prepare()
        parts: list[dict] = [{"text": req.prompt}]
        uploaded = 0
        inline_used = 0
        for r in req.references:
            data, mime = read_bytes(r)  # local-only; rejects remote refs
            if inline_used + len(data) <= self.inline_max_bytes:
                parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}})
                inline_used += len(data)
            else:  # too large to inline safely — upload and reference by URI
                self._require_direct_key(headers, r, len(data))
                uri = _gemini_files.upload_bytes(self.base_url, headers, data, mime, display_name=_ref_name(r))
                parts.append({"fileData": {"mimeType": mime, "fileUri": uri}})
                uploaded += 1
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
            raise _no_media_error(data, self.name, model, "image")
        out = Path(req.output)
        artifacts = [_write_b64(images[0][0], out, "image", source_mime=images[0][1])]
        for i, (b64, mime) in enumerate(images[1:], start=2):
            artifacts.append(_write_b64(b64, out.with_name(f"{out.stem}_{i}{out.suffix}"), "image",
                                        source_mime=mime, role="group"))
        usage = data.get("usageMetadata") or {}
        used_model = data.get("modelVersion") or model  # the model that actually served the request
        self.record(derive_scene(req), model=used_model, kind="image", generated_images=len(images),
                    output_tokens=usage.get("candidatesTokenCount", 0),
                    total_tokens=usage.get("totalTokenCount", 0))
        meta: dict = {"prompt": req.prompt}
        if uploaded:
            meta["uploaded_refs"] = uploaded  # references sent via the Files API
        if data.get("responseId"):
            meta["response_id"] = data["responseId"]  # quote this in a Google support ticket
        text = _extract_text(data)
        if text:
            meta["text"] = text  # the model's caption / grounded summary (TEXT+IMAGE output)
        grounding = _grounding_metadata(data)
        if grounding:
            meta["grounding"] = grounding  # search suggestions (display per ToS) + citations
        return GenerationResult(modality="image", provider=self.name, model=used_model,
                                artifacts=artifacts, usage=usage, meta=meta)

    # ---- speech / dialogue (TTS via generateContent) ---------------------
    def generate_speech(self, req: SpeechRequest) -> GenerationResult:
        model = req.model or self.model_id
        voice = req.voice or "Kore"
        speech_cfg = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}
        return self._tts(model, req.text, speech_cfg, Path(req.output), Scene.SPEECH_TEXT_TO_SPEECH, {"voice": voice})

    def generate_dialogue(self, req: DialogueRequest) -> GenerationResult:
        if not req.turns or not req.cast:
            raise MediaError("dialogue requires turns and a cast", category=ErrorCategory.VALIDATION, provider=self.name)
        model = req.model or self.model_id
        configs = [{"speaker": spk, "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}
                   for spk, voice in req.cast.items()]
        speech_cfg = {"multiSpeakerVoiceConfig": {"speakerVoiceConfigs": configs}}
        script = "\n".join(f"{t.speaker}: {t.text}" for t in req.turns)
        prompt = f"{req.instruction}\n\n{script}" if req.instruction else script
        return self._tts(model, prompt, speech_cfg, Path(req.output), Scene.SPEECH_DIALOGUE,
                         {"voices": req.voices(), "instruction": req.instruction})

    def _tts(self, model: str, prompt: str, speech_cfg: dict, out: Path, scene: Scene, meta: dict) -> GenerationResult:
        client, headers = self._prepare()
        # TTS models produce audio only — responseModalities MUST be ["AUDIO"] (adding
        # "TEXT", as the image path does, makes the model reject the request with a 400).
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": speech_cfg}}
        data = client.request_json("POST", f"/models/{model}:generateContent", body=body, headers=headers)
        audio = _extract_inline_audio(data)
        if not audio:
            raise _no_media_error(data, self.name, model, "audio")
        b64, mime = audio[0]
        write_pcm_wav(out, base64.b64decode(b64), rate=_pcm_rate(mime))  # headerless PCM -> WAV
        usage = data.get("usageMetadata") or {}
        used_model = data.get("modelVersion") or model
        self.record(scene, model=used_model, kind="audio", characters=len(prompt),
                    total_tokens=usage.get("totalTokenCount", 0))
        if data.get("responseId"):
            meta["response_id"] = data["responseId"]
        return GenerationResult(modality="audio", provider=self.name, model=used_model,
                                artifacts=[Artifact.from_path(out, "audio", mime="audio/wav")], usage=usage, meta=meta)

    # ---- video (Veo long-running op) -------------------------------------
    def generate_video(self, req: VideoRequest):
        client, headers = self._prepare()
        model = req.model or self.model_id
        instance: dict = {"prompt": req.prompt}
        if req.first_frame:
            instance["image"] = _veo_media(req.first_frame)
        if req.last_frame:
            instance["lastFrame"] = _veo_media(req.last_frame)
        if req.reference_images:
            # Up to 3 asset references (person/character/product) preserved in the clip.
            instance["referenceImages"] = [{"image": _veo_media(r), "referenceType": "asset"}
                                           for r in req.reference_images]
        # Veo extension continues a previously generated Veo clip, referenced by its URI
        # (valid ~2 days). The API rejects inline video bytes for extension ("Video URI
        # not found"), so a local file cannot be used here.
        #
        # `--continue-from` and `--reference-video` are two scenes (`video.extend` vs
        # `video.reference_to_video`) but one wire field: Veo has a single input for "the
        # clip to carry on from". continue_from is read first, matching derive_scene,
        # which ranks it above references. It used to be read by nothing at all, so a
        # `--continue-from` call passed validation as video.extend and then submitted a
        # plain text-to-video request — a fresh, unrelated clip billed and returned as
        # ok:true under meta.scene "video.extend".
        if req.continue_from is not None and req.reference_videos:
            raise MediaError(
                "Veo takes one clip to carry on from, and --continue-from and --reference-video both "
                "name it — pass only one. (They are different scenes here but the same wire field, so "
                "honouring one would mean silently discarding the other.)",
                category=ErrorCategory.VALIDATION, provider=self.name,
            )
        clip = req.continue_from or (req.reference_videos[0] if req.reference_videos else None)
        if clip is not None:
            if clip.is_local:
                raise MediaError(
                    "Veo video extension needs the URI of a previously generated Veo clip — pass the "
                    "operation's video URI to --continue-from (or --reference-video); the API does not "
                    "accept an inline/local video for extension",
                    category=ErrorCategory.VALIDATION, provider=self.name,
                )
            instance["video"] = {"uri": clip.raw, "mimeType": "video/mp4"}
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
        return self._poll_operation(client, headers, op_name, Path(req.output), model,
                                    seconds=req.duration or 0, scene=derive_scene(req))

    def _poll_operation(self, client, headers, op_name: str, out: Path, model: str, *,
                        seconds: int = 0, scene: Scene | None = None) -> GenerationResult:
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            res = client.request_json("GET", f"/{op_name}", headers=headers)
            if res.get("done"):
                return self._finalize_video(client, headers, op_name, out, model, res,
                                            seconds=seconds, scene=scene)
            time.sleep(self.poll_interval)
        raise MediaError(f"Veo operation {op_name} timed out after {self.poll_timeout}s",
                         category=ErrorCategory.TIMEOUT, provider=self.name)

    def _finalize_video(self, client, headers, op_name: str, out: Path, model: str, res: dict,
                        *, seconds: int = 0, scene: Scene | None = None) -> GenerationResult:
        if res.get("error"):
            raise _operation_error(res["error"], op_name, self.name)
        uri = _veo_video_uri(res)
        if not uri:
            raise MediaError(f"Veo operation {op_name} done but no video uri", category=ErrorCategory.PROVIDER, provider=self.name)
        client.download(uri, out, headers=headers)  # the file URI needs the API key
        # Veo operations return no usage/duration, so bill by the TRUE output length:
        # probe the downloaded clip (this also captures an extension's combined length,
        # which the requested duration would undercount). Fall back to the requested
        # duration only if the probe can't read it (missing ffmpeg / unreadable file).
        secs = int(round(ffmpeg.probe_duration(out))) or seconds
        self.record(scene, model=model, kind="video", seconds=secs)
        return GenerationResult(modality="video", provider=self.name, model=model,
                                artifacts=[Artifact.from_path(out, "video", mime="video/mp4")], usage={},
                                meta={"operation": op_name, "seconds": secs})

    def get_job(self, ref: JobRef, *, output: Path | None = None) -> JobStatus:
        client, headers = self._prepare()
        res = client.request_json("GET", f"/{ref.id}", headers=headers)
        done = bool(res.get("done"))
        if done and res.get("error"):
            # a done-with-error operation is a failure, not a success
            raise _operation_error(res["error"], ref.id, self.name)
        result = None
        if done and output is not None:
            result = self._finalize_video(client, headers, ref.id, Path(output), ref.model or self.model_id, res)
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
        # block surfaces on the 200-OK path (see `_no_media_error`), so an HTTP error
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


def _ref_name(ref: MediaRef) -> str:
    try:
        return ref.path().name or "input"
    except Exception:  # noqa: BLE001
        return "input"


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


def _extract_inline_audio(data: dict) -> list[tuple[str, str]]:
    """Return ``(base64, mimeType)`` for each inline audio part (mimeType carries the
    PCM sample rate, e.g. ``audio/L16;codec=pcm;rate=24000``)."""
    return [(b64, mime) for b64, mime in _extract_inline_images(data) if "audio" in (mime or "").lower()]


def _pcm_rate(mime: str) -> int:
    """Parse the sample rate from a PCM mimeType (default 24000 if absent)."""
    m = re.search(r"rate=(\d+)", mime or "")
    return int(m.group(1)) if m else 24000


def _extract_text(data: dict) -> str:
    """Join the model's non-thought text parts (a caption, or a grounded summary)."""
    chunks = []
    for cand in data.get("candidates") or []:
        for part in ((cand.get("content") or {}).get("parts") or []):
            if not part.get("thought") and part.get("text"):
                chunks.append(part["text"])
    return "\n".join(chunks).strip()


def _grounding_metadata(data: dict) -> dict | None:
    """First candidate's ``groundingMetadata`` if the response was grounded. It carries
    the search-suggestion HTML (``searchEntryPoint``) that the Google Search grounding
    terms require callers to display, plus citation chunks — so surface it, don't drop it."""
    for cand in data.get("candidates") or []:
        gm = cand.get("groundingMetadata") or cand.get("grounding_metadata")
        if gm:
            return gm
    return None


def _no_media_error(data: dict, provider: str, model: str, kind: str = "image") -> MediaError:
    """Turn a 200-OK-but-no-output response into a categorized error (Gemini's
    silent safety drop) instead of writing an empty file."""
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        return MediaError(f"Gemini blocked the prompt: {feedback['blockReason']}",
                          category=ErrorCategory.SAFETY, provider=provider, model=model, details=feedback)
    cands = data.get("candidates") or []
    finish = (cands[0].get("finishReason") if cands else None) or f"NO_{kind.upper()}"
    if any(x in str(finish).upper() for x in ("SAFETY", "PROHIBITED", "RECITATION", "BLOCK", kind.upper())):
        return MediaError(f"Gemini returned no {kind} (finishReason={finish})",
                          category=ErrorCategory.SAFETY, provider=provider, model=model, details={"finishReason": finish})
    return MediaError(f"Gemini returned no {kind} (finishReason={finish})",
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
