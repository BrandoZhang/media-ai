"""OpenAI provider — Images API (GPT Image family).

Image generation is **synchronous**: ``POST /v1/images/generations`` (JSON) and
``POST /v1/images/edits`` (multipart, for reference images + an inpaint mask). GPT
Image returns base64-encoded bytes only (never a hosted URL).

This adapter is **GPT-Image-only**. OpenAI's older DALL·E models are intentionally
not supported: the current Images API rejects their ``response_format`` parameter,
and GPT Image supersedes them. OpenAI also exposes no video API here (Sora is not
public), so a ``video generate --provider openai`` request fails the pre-flight
capability check with a deterministic ``unsupported`` error.

Verified against developers.openai.com / platform.openai.com. Auth: ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from ..core.capabilities import GeometryMode, ImageCaps, ModelCapabilities, Operation
from ..core.errors import ErrorCategory, MediaError
from ..core.modelspec import apply_spec
from ..core.logging import get_logger
from ..core.mediaref import guess_mime, read_bytes
from ..core.result import Artifact, GenerationResult
from ..core.types import ImageRequest, Modality
from ..core.usage import record_usage
from ._base import HttpProvider
from ._catalog import OPENAI

# GPT Image accepts a fixed size enum on the pre-gpt-image-2 models; gpt-image-2
# takes an arbitrary size subject to the constraints declared in `capabilities`.
_FIXED_GPT_SIZES = ("1024x1024", "1536x1024", "1024x1536", "auto")

# gpt-image-2 size constraints (developers.openai.com — "Size and quality options").
_GI2_MAX_EDGE = 3840
_GI2_PIXEL_MULTIPLE = 16
_GI2_TOTAL_MIN = 655_360
_GI2_TOTAL_MAX = 8_294_400
_GI2_MAX_EDGE_RATIO = 3.0

# Map an output-file suffix to the GPT Image `output_format` it implies, used only to
# flag a mismatch between the caller's filename and the format the API actually returned.
_SUFFIX_FORMAT = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}


def _is_gpt_image_2(model: str) -> bool:
    """Whether a model takes arbitrary sizes, per the catalogue rather than its name.

    Was ``startswith("gpt-image-2")``, which quietly made every future id a non-match.
    """
    spec = OPENAI.get(model)
    return bool(spec and spec.caps.get("arbitrary_sizes"))


def _supports_input_fidelity(model: str) -> bool:
    """``input_fidelity`` is a knob only some tiers expose — gpt-image-2 processes
    inputs at high fidelity and rejects the parameter, and the mini tier lacks it."""
    spec = OPENAI.get(model)
    return bool(spec and spec.caps.get("input_fidelity"))


class OpenAIProvider(HttpProvider):
    name = "openai"
    catalog = OPENAI
    auth_scheme = "bearer"
    # `dall-e`/`sora` route here only to return a clear unsupported/removal error (the
    # provider is GPT-Image-only); the catalogue marks them removed.
    model_hints = ("gpt-image", "dall-e", "sora")

    def __init__(self, *, credentials=None, config=None) -> None:
        super().__init__(credentials=credentials, config=config)
        self.base_url = (self.config.get("base_url") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.image_model = self.config.get("image_model") or os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-2"

    def _auth(self, cred):
        base, headers = super()._auth(cred)
        if os.getenv("OPENAI_ORG"):
            headers["OpenAI-Organization"] = os.environ["OPENAI_ORG"]
        if os.getenv("OPENAI_PROJECT"):
            headers["OpenAI-Project"] = os.environ["OPENAI_PROJECT"]
        return base, headers

    # ---- discovery -------------------------------------------------------
    def models(self) -> list[str]:
        return OPENAI.discoverable_ids()

    def default_model(self, modality: Modality | None) -> str | None:
        # Image-only provider: no default video model.
        return None if modality == Modality.VIDEO else self.image_model

    def capabilities(self, model: str | None = None, modality: Modality | None = None) -> ModelCapabilities:
        model = model or self.image_model
        spec = OPENAI.require(model)   # raises for a removed model, naming its replacement
        arbitrary = _is_gpt_image_2(model)
        options = ("moderation", "output_compression")
        if _supports_input_fidelity(model):
            options += ("input_fidelity",)
        return apply_spec(ModelCapabilities(
            provider=self.name, model=model, modalities=frozenset({Modality.IMAGE}),
            image=ImageCaps(
                operations=frozenset({Operation.IMAGE_GENERATE, Operation.IMAGE_EDIT}),
                geometry_mode=GeometryMode.BOTH,
                # `_size` maps any aspect ratio (landscape/portrait/square), so don't
                # pre-reject ratios; the pixel size it produces is what's validated.
                aspect_ratios=(),
                named_sizes=(),
                pixel_sizes=() if arbitrary else _FIXED_GPT_SIZES,
                pixel_multiple=_GI2_PIXEL_MULTIPLE if arbitrary else None,
                pixel_max=(_GI2_MAX_EDGE, _GI2_MAX_EDGE) if arbitrary else None,
                pixel_total_min=_GI2_TOTAL_MIN if arbitrary else None,
                pixel_total_max=_GI2_TOTAL_MAX if arbitrary else None,
                max_edge_ratio=_GI2_MAX_EDGE_RATIO if arbitrary else None,
                max_count=10, output_formats=("png", "jpeg", "webp"),
                supports_quality=True,
                supports_transparency=not arbitrary,  # gpt-image-2 rejects transparent
                supports_mask=True, max_references=16,
                options=options,
            ),
        ), spec)

    # ---- size mapping ----------------------------------------------------
    def _size(self, model: str, req: ImageRequest) -> str:
        geo = req.geometry
        if geo and geo.mode == "pixels":
            return f"{geo.width}x{geo.height}"
        if geo and geo.aspect_ratio:
            a, b = (geo.aspect_ratio.split(":", 1) + ["1"])[:2]
            try:
                fa, fb = float(a), float(b)
            except ValueError:
                raise MediaError(
                    f"invalid --aspect-ratio {geo.aspect_ratio!r}; expected W:H like 16:9",
                    category=ErrorCategory.VALIDATION, provider=self.name,
                ) from None
            landscape = fa > fb
            portrait = fa < fb
            if _is_gpt_image_2(model):  # arbitrary sizes → pick a documented tier
                tier = (geo.resolution or "").lower()
                if landscape:
                    return {"4k": "3840x2160", "2k": "2048x1152"}.get(tier, "1536x1024")
                if portrait:
                    return {"4k": "2160x3840", "2k": "1152x2048"}.get(tier, "1024x1536")
                return "2048x2048" if tier in ("2k", "4k") else "1024x1024"
            # pre-gpt-image-2 GPT Image: fixed 1.5-MP sizes only
            return "1536x1024" if landscape else "1024x1536" if portrait else "1024x1024"
        return "auto"

    # ---- images ----------------------------------------------------------
    def generate_image(self, req: ImageRequest) -> GenerationResult:
        model = req.model or self.image_model
        # Refuse a retired model here as well as in capabilities(), so the two agree:
        # a caller must never be able to send a request for something discovery says
        # is gone.
        OPENAI.require(model)
        client, headers = self._prepare()
        if req.operation == Operation.IMAGE_EDIT or req.references or req.mask:
            data = self._edit(client, headers, model, req)
        else:
            data = self._generate(client, headers, model, req)
        items = [d for d in (data.get("data") or []) if d.get("b64_json")]
        if not items:
            raise MediaError("OpenAI image response had no images", category=ErrorCategory.PROVIDER, provider=self.name, model=model)
        # The response echoes the format/size the model *actually* used; trust it over
        # the request (e.g. size:"auto" resolves to a concrete size, and the bytes are
        # whatever output_format the API returned regardless of the output filename).
        fmt = data.get("output_format")
        out = Path(req.output)
        self._warn_suffix_mismatch(out, fmt)
        artifacts = [self._save(items[0], out, fmt)]
        for i, it in enumerate(items[1:], start=2):
            artifacts.append(self._save(it, out.with_name(f"{out.stem}_{i}{out.suffix}"), fmt, role="group"))
        usage = data.get("usage") or {}
        record_usage({"tool": req.operation.value, "operation": req.operation.value, "provider": self.name,
                      "model": model, "kind": "image", "generated_images": len(items),
                      "input_tokens": usage.get("input_tokens", 0),
                      "output_tokens": usage.get("output_tokens", 0), "total_tokens": usage.get("total_tokens", 0)})
        meta = {"prompt": req.prompt, "size": data.get("size") or self._size(model, req)}
        # Surface the settings the API echoed back (what it actually did) for traceability.
        for k in ("output_format", "quality", "background", "created"):
            if data.get(k) is not None:
                meta[k] = data[k]
        return GenerationResult(modality="image", operation=req.operation.value, provider=self.name, model=model,
                                artifacts=artifacts, usage=usage, meta=meta)

    def _common_fields(self, model: str, req: ImageRequest) -> dict:
        fields: dict = {"model": model, "prompt": req.prompt, "n": req.count, "size": self._size(model, req)}
        if req.quality:
            fields["quality"] = req.quality
        if req.background:
            fields["background"] = req.background
        if req.output_format:
            fields["output_format"] = req.output_format
        for k in ("moderation", "output_compression"):
            if k in req.options:
                fields[k] = req.options[k]
        # input_fidelity is a knob only on gpt-image-1 / gpt-image-1.5; never
        # forward it to a model that rejects it (gpt-image-2, mini).
        if "input_fidelity" in req.options and _supports_input_fidelity(model):
            fields["input_fidelity"] = req.options["input_fidelity"]
        return fields

    def _generate(self, client, headers, model: str, req: ImageRequest) -> dict:
        return client.request_json("POST", "/images/generations", body=self._common_fields(model, req), headers=headers)

    def _edit(self, client, headers, model: str, req: ImageRequest) -> dict:
        if not req.references:
            raise MediaError("image edit requires at least one reference image", category=ErrorCategory.VALIDATION, provider=self.name)
        fields = self._common_fields(model, req)
        files = []
        for r in req.references:
            content, mime = read_bytes(r)
            files.append(("image[]", Path(r.raw).name, mime, content))
        if req.mask:
            content, mime = read_bytes(req.mask)
            files.append(("mask", Path(req.mask.raw).name, mime, content))
        return client.request_multipart("POST", "/images/edits", fields=fields, files=files, headers=headers)

    @staticmethod
    def _warn_suffix_mismatch(out: Path, fmt: str | None) -> None:
        """Warn (stderr) when the output filename's extension disagrees with the format
        the API actually returned — the bytes on disk are `fmt`, not what the name implies."""
        want = _SUFFIX_FORMAT.get(out.suffix.lower())
        if fmt and want and want != fmt:
            get_logger().warning("output %s has a %s extension but the API returned %s bytes; wrote them as-is",
                                 out.name, out.suffix, fmt)

    @staticmethod
    def _save(item: dict, out: Path, fmt: str | None, *, role=None) -> Artifact:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(item["b64_json"]))
        # The response's output_format is authoritative for the bytes; fall back to the
        # filename's mime only when the API didn't echo a format. output_format is one of
        # png/jpeg/webp, each of which is a valid `image/<fmt>` IANA subtype.
        mime = f"image/{fmt}" if fmt else guess_mime(out)
        return Artifact.from_path(out, "image", mime=mime, role=role)

    # ---- errors ----------------------------------------------------------
    def _error(self, status: int, body: str) -> MediaError:
        code, extra = _parse_error(body)
        low = body.lower()
        if code == "moderation_blocked" or "content_policy" in low or "moderation_blocked" in low or "safety" in low:
            # image_generation_user_error / moderation_blocked → not retryable; the
            # caller must change the prompt/inputs. Surface the stable `code` and any
            # coarse moderation_details for developer logs.
            return MediaError(f"OpenAI content policy: {body}", category=ErrorCategory.SAFETY,
                              code=code or "moderation_blocked", provider=self.name, details=extra or None)
        cat = {400: ErrorCategory.VALIDATION, 401: ErrorCategory.AUTH, 403: ErrorCategory.AUTH,
               404: ErrorCategory.NOT_FOUND, 429: ErrorCategory.RATE_LIMIT}.get(status, ErrorCategory.PROVIDER)
        if "insufficient_quota" in low:
            cat = ErrorCategory.RATE_LIMIT
        details = {"status": status, **extra}
        return MediaError(f"OpenAI HTTP {status}: {body}", category=cat, code=code, provider=self.name, details=details)


def _parse_error(body: str) -> tuple[str | None, dict]:
    """Best-effort extract of ``error.code`` + coarse ``moderation_details`` from an
    OpenAI error body. The body may be truncated/redacted, so failures degrade to
    ``(None, {})`` and the caller falls back to substring detection."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None, {}
    # A valid-JSON body whose top level isn't an object (e.g. "unauthorized", [1], true)
    # has no .get(); guard so error mapping never crashes into an UNKNOWN fallback.
    err = data.get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return None, {}
    code = err.get("code")
    extra: dict = {}
    if err.get("type"):
        extra["error_type"] = err["type"]
    md = err.get("moderation_details")
    if isinstance(md, dict):
        if md.get("moderation_stage"):
            extra["moderation_stage"] = md["moderation_stage"]
        if md.get("categories"):
            extra["moderation_categories"] = md["categories"]
    return code, extra
