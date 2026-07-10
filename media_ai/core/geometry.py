"""Geometry helpers: parse/normalize sizes and map a :class:`GeometrySpec` to the
concrete pixels a mock/cost path needs.

Providers express output geometry three different ways (pixel ``WxH``,
aspect-ratio + tier, resolution tier + ratio). Adapters translate a normalized
:class:`~media_ai.core.types.GeometrySpec` to their own wire format; this module
holds the shared primitives (parsing, the video resolution table used for cost
accounting, and a best-effort pixel resolver for the mock backend).
"""

from __future__ import annotations

from .errors import ErrorCategory, MediaError
from .types import GeometrySpec

# Video resolution/ratio -> (w, h). Used by the mock renderer and cost accounting
# (tokens ~ pixels). Kept from the original Volc implementation.
VIDEO_DIMS: dict[str, dict[str, tuple[int, int]]] = {
    "480p": {
        "16:9": (864, 480), "9:16": (480, 864), "1:1": (640, 640),
        "4:3": (736, 544), "3:4": (544, 736), "21:9": (960, 416),
    },
    "720p": {
        "16:9": (1280, 720), "9:16": (720, 1280), "1:1": (960, 960),
        "4:3": (1120, 832), "3:4": (832, 1120), "21:9": (1504, 640),
    },
    "1080p": {
        "16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1440, 1440),
        "4:3": (1664, 1248), "3:4": (1248, 1664), "21:9": (2176, 928),
    },
}

# Named image tiers -> nominal (w, h) at 1:1, used only for mock rendering / cost.
IMAGE_TIER_PX: dict[str, int] = {"512": 512, "1K": 1024, "2K": 2048, "4K": 4096}


def parse_size(text: str) -> tuple[int, int]:
    """Parse ``"1024x768"`` (or ``"1024X768"``) into ``(1024, 768)``."""
    try:
        w, h = (int(p) for p in text.lower().replace(" ", "").split("x", 1))
        if w <= 0 or h <= 0:
            raise ValueError
        return w, h
    except (ValueError, TypeError):
        raise MediaError(
            f"invalid --size {text!r}; expected WIDTHxHEIGHT like 1024x768",
            category=ErrorCategory.VALIDATION,
        ) from None


def normalize_ratio(ratio: str | None) -> str | None:
    if not ratio:
        return None
    ratio = ratio.strip().lower()
    if ratio == "adaptive":
        return "adaptive"
    return ratio.replace(" ", "")


def video_dims(resolution: str, ratio: str) -> tuple[int, int]:
    """Best-effort ``(w, h)`` for a resolution+ratio (cost accounting / mock).

    Resolution/ratio are normalized (case, whitespace) so ``480P`` or `` 480p ``
    don't silently fall back to the 720p default and mis-bill the clip.
    """
    res = VIDEO_DIMS.get((resolution or "").strip().lower(), VIDEO_DIMS["720p"])
    r = normalize_ratio(ratio) or "16:9"
    if r == "adaptive":
        r = "16:9"
    return res.get(r, res["16:9"])


def ratio_to_wh(ratio: str, long_side: int) -> tuple[int, int]:
    """Turn ``"16:9"`` + a long side into even ``(w, h)``."""
    try:
        a, b = (int(x) for x in ratio.split(":", 1))
    except (ValueError, AttributeError):
        a, b = 1, 1
    if a >= b:
        w, h = long_side, round(long_side * b / a)
    else:
        w, h = round(long_side * a / b), long_side
    return max(2, (w // 2) * 2), max(2, (h // 2) * 2)


def resolve_image_pixels(geo: GeometrySpec | None, default: tuple[int, int]) -> tuple[int, int]:
    """Best-effort pixels for an image request (used by the mock renderer)."""
    if geo is None:
        return default
    if geo.mode == "pixels":
        return geo.width, geo.height  # type: ignore[return-value]
    tier_px = IMAGE_TIER_PX.get((geo.resolution or "").upper().replace("K", "K"), 1024)
    ratio = normalize_ratio(geo.aspect_ratio) or "1:1"
    if ratio == "adaptive":
        ratio = "1:1"
    return ratio_to_wh(ratio, tier_px)


def resolve_video_pixels(geo: GeometrySpec | None, default_res: str = "720p") -> tuple[int, int]:
    """Best-effort pixels for a video request (mock render + cost)."""
    res = (geo.resolution if geo else None) or default_res
    ratio = (geo.aspect_ratio if geo else None) or "16:9"
    return video_dims(res, ratio)
