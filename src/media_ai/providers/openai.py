"""OpenAI provider — Images API (GPT Image family).

Image generation is **synchronous**: ``POST /v1/images/generations`` (JSON) and
``POST /v1/images/edits`` (multipart, for reference images). GPT
Image returns base64-encoded bytes only (never a hosted URL).

This adapter is **GPT-Image-only**. OpenAI's older DALL·E models are intentionally
not supported: the current Images API rejects their ``response_format`` parameter,
and GPT Image supersedes them. OpenAI also exposes no video API here (Sora is not
public), so a ``video generate --provider openai`` request fails the pre-flight
capability check with a deterministic ``unsupported`` error.

Verified against developers.openai.com / platform.openai.com.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from ..core.errors import ErrorCategory, MediaError
from ..core.logging import get_logger
from ..core.mediaref import guess_mime, read_bytes
from ..core.result import Artifact, GenerationResult
from ..core.scene import Scene, derive_scene
from ..core.types import ImageRequest
from ._base import HttpAdapter

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

# shape -> tier -> documented gpt-image-2 size; "" is the tier a request did not name.
# One table for both halves of the question (which shape, how big) so a tier cannot be
# read in one branch and dropped in another.
_TIER_SIZES = {
    "landscape": {"": "1536x1024", "2k": "2048x1152", "4k": "3840x2160"},
    "portrait": {"": "1024x1536", "2k": "1152x2048", "4k": "2160x3840"},
    "square": {"": "1024x1024", "2k": "2048x2048", "4k": "2048x2048"},
}
# The older GPT Image tiers take a fixed 1.5-MP enum, with no tier to choose.
_FIXED_SIZES = {"landscape": "1536x1024", "portrait": "1024x1536", "square": "1024x1024"}


class OpenAIAdapter(HttpAdapter):

    def honoured_flags(self) -> frozenset[str]:
        # `n` on the Images API.
        return frozenset({"group_output"})

    def supported_scenes(self) -> frozenset[Scene]:
        return frozenset({Scene.IMAGE_TEXT_TO_IMAGE, Scene.IMAGE_IMAGE_TO_IMAGE})

    def _scoping_headers(self) -> dict:
        """Optional org/project scoping, declared on the binding rather than the shell.

        Two accounts billing different projects are two bindings, so the header belongs
        to the binding — read from the environment it was global to the process and
        could not differ between them.
        """
        return {
            header: value
            for header, key in (("OpenAI-Organization", "org"), ("OpenAI-Project", "project"))
            if (value := self.option(key))
        }

    @property
    def arbitrary_sizes(self) -> bool:
        """Whether this tier takes any size, or only a fixed enum.

        Read from the binding's declared geometry rather than from the model id: the
        old test was ``startswith("gpt-image-2")``, which quietly made every future id
        a non-match.
        """
        return not self.constraints.geometry.pixel_sizes

    def _auth(self, cred):
        base, headers = super()._auth(cred)
        return base, {**headers, **self._scoping_headers()}

    # ---- size mapping ----------------------------------------------------
    def _size(self, req: ImageRequest) -> str:
        """The ``size`` field for this request: pixels, a mapped shape+tier, or ``auto``.

        ``auto`` is only for a request that asked for no geometry at all. A ``--resolution``
        with no ``--aspect-ratio`` used to land there too, which dropped it: the API then
        chose its own size, ``meta.size`` echoed that choice back, and nothing said the
        tier had been ignored. A tier is a request about how *big*, so it is answered —
        as a square, the same size ``--aspect-ratio 1:1 --resolution <tier>`` produces.
        """
        geo = req.geometry
        if geo is None:
            return "auto"
        if geo.mode == "pixels":
            return f"{geo.width}x{geo.height}"
        if not geo.aspect_ratio and not geo.resolution:
            return "auto"
        shape = self._shape(geo.aspect_ratio) if geo.aspect_ratio else "square"
        if not self.arbitrary_sizes:  # pre-gpt-image-2 GPT Image: fixed 1.5-MP sizes only
            return _FIXED_SIZES[shape]
        sizes = _TIER_SIZES[shape]
        return sizes.get((geo.resolution or "").lower(), sizes[""])

    def _shape(self, ratio: str) -> str:
        a, b = (ratio.split(":", 1) + ["1"])[:2]
        try:
            fa, fb = float(a), float(b)
        except ValueError:
            raise MediaError(
                f"invalid --aspect-ratio {ratio!r}; expected W:H like 16:9",
                category=ErrorCategory.VALIDATION, provider=self.name,
            ) from None
        return "landscape" if fa > fb else "portrait" if fa < fb else "square"

    # ---- images ----------------------------------------------------------
    def generate_image(self, req: ImageRequest) -> GenerationResult:
        model = req.model or self.model_id
        client, headers = self._prepare()
        scene = derive_scene(req)
        if req.references:
            data = self._edit(client, headers, model, req)
        else:
            data = self._generate(client, headers, model, req)
        items = [d for d in (data.get("data") or []) if d.get("b64_json")]
        if not items:
            raise MediaError(
                "OpenAI image response had no images", category=ErrorCategory.PROVIDER, provider=self.name, model=model
            )
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
        self.record(scene, model=model, kind="image", generated_images=len(items),
                    input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0))
        meta = {"prompt": req.prompt, "size": data.get("size") or self._size(req)}
        # Surface the settings the API echoed back (what it actually did) for traceability.
        for k in ("output_format", "quality", "background", "created"):
            if data.get(k) is not None:
                meta[k] = data[k]
        return GenerationResult(modality="image", provider=self.name, model=model,
                                artifacts=artifacts, usage=usage, meta=meta)

    def _common_fields(self, model: str, req: ImageRequest) -> dict:
        fields: dict = {"model": model, "prompt": req.prompt, "n": req.count, "size": self._size(req)}
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
        if "input_fidelity" in req.options and "input_fidelity" in self.constraints.options:
            fields["input_fidelity"] = req.options["input_fidelity"]
        return fields

    def _generate(self, client, headers, model: str, req: ImageRequest) -> dict:
        return client.request_json("POST", "/images/generations", body=self._common_fields(model, req), headers=headers)

    def _edit(self, client, headers, model: str, req: ImageRequest) -> dict:
        if not req.references:
            raise MediaError(
                "image edit requires at least one reference image", category=ErrorCategory.VALIDATION, provider=self.name
            )
        fields = self._common_fields(model, req)
        files = []
        for r in req.references:
            content, mime = read_bytes(r)
            files.append(("image[]", Path(r.raw).name, mime, content))
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
