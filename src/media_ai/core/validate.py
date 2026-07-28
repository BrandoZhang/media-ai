"""Pre-flight validation against a binding's declared constraints.

The same manifest that ``media-ai capabilities`` prints is what gates the request, so
discovery and enforcement cannot drift: there is one declaration, read twice. Anything
a binding does not declare is rejected **before the network call** — no key needed, no
money spent, a machine-readable reason.

Two rules shape what is checked here:

* **Only what is declared.** An absent constraint is an absent constraint, not a
  permissive default and not a guess — a binding that publishes no ceiling gets no
  ceiling enforced, and the API stays the authority on what it never told us.
* **The caller's policy decides severity.** ``--on-unsupported warn`` exists because
  a vendor sometimes ships a capability before its docs, and a user who knows that
  should not be blocked by our copy of yesterday's limits.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from .binding import Constraints
from .errors import ErrorCategory, MediaError
from .scene import Scene
from .types import (
    DialogueRequest,
    GeometrySpec,
    ImageRequest,
    MediaRef,
    MusicPlanRequest,
    MusicRequest,
    SoundEffectRequest,
    SpeechRequest,
    VideoRequest,
)

__all__ = ["UnsupportedPolicy", "validate_request"]

#: Markers that mean a prompt is doing coordinate-directed editing (Seedream 5.0 pro).
#: Sent to a model that cannot read them they are not an error — they are read as
#: prose, and the result is a quietly wrong image. That is the failure worth catching.
_EDIT_MARKERS = ("<bbox>", "<point>")


class UnsupportedPolicy(str, Enum):
    ERROR = "error"
    WARN = "warn"
    IGNORE = "ignore"


class _Issues(list):
    """Collected problems. Each item is ``(field, reason)``."""

    def add(self, field: str, reason: str) -> None:
        self.append((field, reason))

    def one_of(self, field: str, value, allowed, *, label: str) -> None:
        """Reject a value outside a declared set — and say nothing when none is declared."""
        if value is not None and allowed and value not in allowed:
            self.add(field, f"unsupported {label} {value!r}; allowed: {', '.join(map(str, allowed))}")

    def at_most(self, field: str, count: int, limit: int | None, *, label: str) -> None:
        # `is not None`, not truthiness: a declared limit of 0 means "none allowed",
        # and treating it as absent would skip the one check it exists to make.
        if limit is not None and count > limit:
            self.add(field, f"{count} {label} exceeds the maximum of {limit}")

    def needs(self, field: str, requested, c: Constraints, flag: str, *, label: str) -> None:
        if requested and not c.supports_flag(flag):
            self.add(field, f"this binding does not support {label}")

    def in_range(self, field: str, value, bounds, *, label: str, unit: str = "") -> None:
        if value is None or not bounds:
            return
        low, high = bounds
        if value < low or value > high:
            self.add(field, f"{label} {value}{unit} is outside {low}{unit}–{high}{unit}")


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def _check_geometry(geo: GeometrySpec | None, c: Constraints, issues: _Issues, *, video: bool) -> None:
    if geo is None or geo.mode is None:
        return
    g = c.geometry

    if video:
        # "adaptive" is Ark asking the model to choose, not a ratio to check.
        if geo.aspect_ratio != "adaptive":
            issues.one_of("aspect-ratio", geo.aspect_ratio, g.aspect_ratios, label="ratio")
        issues.one_of("resolution", geo.resolution, g.resolutions, label="resolution")
        return

    if g.mode == "none":
        issues.add("geometry", "this binding does not accept a configurable size")
        return
    if geo.mode == "pixels" and g.mode == "aspect_ratio":
        issues.add("size", "this binding takes --aspect-ratio, not pixel --size")
    if geo.mode == "ratio" and g.mode == "pixels":
        issues.add("aspect-ratio", "this binding takes pixel --size, not --aspect-ratio")

    issues.one_of("aspect-ratio", geo.aspect_ratio, g.aspect_ratios, label="ratio")
    issues.one_of("resolution", geo.resolution, g.named_sizes or g.resolutions, label="size tier")

    if geo.mode != "pixels":
        return
    width, height = geo.width or 0, geo.height or 0
    if g.pixel_sizes and f"{width}x{height}" not in g.pixel_sizes:
        issues.add("size", f"unsupported size {width}x{height}; allowed: {', '.join(g.pixel_sizes)}")
    if g.pixel_multiple and (width % g.pixel_multiple or height % g.pixel_multiple):
        issues.add("size", f"width & height must be multiples of {g.pixel_multiple}")
    if g.pixel_max_edge and max(width, height) > g.pixel_max_edge:
        issues.add("size", f"exceeds the maximum edge of {g.pixel_max_edge}px")
    total = width * height
    if g.pixel_total_min and total < g.pixel_total_min:
        issues.add("size", f"total pixels {total} is below the minimum {g.pixel_total_min}")
    if g.pixel_total_max and total > g.pixel_total_max:
        issues.add("size", f"total pixels {total} exceeds the maximum {g.pixel_total_max}")
    if width and height:
        long_edge, short_edge = max(width, height), min(width, height)
        if g.max_edge_ratio and long_edge / short_edge > g.max_edge_ratio + 1e-9:
            issues.add("size", f"edge ratio {long_edge / short_edge:.2f}:1 exceeds {g.max_edge_ratio:g}:1")
        if g.ratio_range and not (g.ratio_range[0] <= width / height <= g.ratio_range[1]):
            issues.add("size", f"aspect ratio {width / height:.3f} is outside {g.ratio_range[0]}–{g.ratio_range[1]}")


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------


def _check_references(refs: list[MediaRef], c: Constraints, issues: _Issues) -> None:
    """What goes *in*, checked before it is uploaded.

    Output limits were always declared; input limits were not, so an oversized
    reference cost a round trip and a 400 to discover. Only local files are inspected —
    a URL is the provider's to fetch and judge.
    """
    r = c.references
    issues.at_most("reference", len(refs), r.max, label="reference(s)")
    for ref in refs:
        if not ref.is_local:
            continue
        path = ref.path()
        if r.formats:
            suffix = path.suffix.lower().lstrip(".")
            normalized = {"jpg": "jpeg", "tif": "tiff"}.get(suffix, suffix)
            if suffix and normalized not in r.formats:
                issues.add("reference", f"{path.name}: {suffix} is not accepted; allowed: {', '.join(r.formats)}")
        if r.max_bytes and path.is_file() and path.stat().st_size > r.max_bytes:
            size_mb = path.stat().st_size / 1_048_576
            issues.add("reference", f"{path.name}: {size_mb:.1f} MB exceeds the {r.max_bytes / 1_048_576:.0f} MB limit")


def _check_prompt_markers(prompt: str | None, c: Constraints, issues: _Issues) -> None:
    if not prompt or c.supports_flag("interactive_edit"):
        return
    if any(marker in prompt for marker in _EDIT_MARKERS):
        issues.add(
            "prompt",
            "contains <bbox>/<point> coordinates, which this binding cannot read — it "
            "would treat them as literal text and quietly generate the wrong image",
        )


# --------------------------------------------------------------------------
# per-request checks
# --------------------------------------------------------------------------


def _validate_image(req: ImageRequest, c: Constraints, issues: _Issues) -> None:
    _check_geometry(req.geometry, c, issues, video=False)
    _check_references(req.references, c, issues)
    _check_prompt_markers(req.prompt, c, issues)
    issues.needs("seed", req.seed is not None, c, "seed", label="--seed")
    issues.needs("negative-prompt", req.negative_prompt, c, "negative_prompt", label="--negative-prompt")
    issues.needs("background", req.background == "transparent", c, "transparency", label="a transparent background")
    issues.needs("quality", req.quality, c, "quality", label="--quality")
    issues.one_of("format", req.output_format, c.output.formats, label="output format")
    issues.at_most("count", req.count, c.output.max_count, label="image(s)")
    if c.output.max_total_images and req.count + len(req.references) > c.output.max_total_images:
        issues.add(
            "count",
            f"{len(req.references)} reference(s) + {req.count} output(s) exceeds the joint "
            f"limit of {c.output.max_total_images}",
        )


def _validate_video(req: VideoRequest, c: Constraints, issues: _Issues) -> None:
    _check_geometry(req.geometry, c, issues, video=True)
    issues.one_of("duration", req.duration, c.video.durations, label="duration")
    issues.needs("seed", req.seed is not None, c, "seed", label="--seed")
    issues.needs("negative-prompt", req.negative_prompt, c, "negative_prompt", label="--negative-prompt")
    issues.needs("audio", req.audio is not None, c, "audio", label="--audio")
    issues.needs("watermark", req.watermark is not None, c, "watermark_control", label="--watermark")
    issues.needs("return-last-frame", req.return_last_frame, c, "return_last_frame", label="--return-last-frame")
    issues.at_most("reference-image", len(req.reference_images), c.references.max, label="reference image(s)")


def _validate_speech(req: SpeechRequest, c: Constraints, issues: _Issues) -> None:
    issues.one_of("output-format", req.output_format, c.audio.formats, label="output format")
    issues.one_of("voice", req.voice, c.audio.voices, label="voice")
    issues.needs("seed", req.seed is not None, c, "seed", label="--seed")
    issues.needs("language-code", req.language_code, c, "language_code", label="--language-code")
    issues.needs("timestamps", req.timestamps, c, "timestamps", label="--timestamps")
    if c.audio.max_characters and len(req.text) > c.audio.max_characters:
        issues.add("text", f"{len(req.text)} characters exceeds the limit of {c.audio.max_characters}")


def _validate_dialogue(req: DialogueRequest, c: Constraints, issues: _Issues) -> None:
    issues.one_of("output-format", req.output_format, c.audio.formats, label="output format")
    issues.needs("instruction", req.instruction, c, "instruction", label="a global --instruction")
    issues.needs("seed", req.seed is not None, c, "seed", label="--seed")
    issues.needs("timestamps", req.timestamps, c, "timestamps", label="--timestamps")
    issues.at_most("speaker", len(set(req.cast.values())), c.audio.max_dialogue_voices, label="distinct voice(s)")
    for name, voice in sorted(req.cast.items()):
        issues.one_of(f"speaker:{name}", voice, c.audio.voices, label="voice")


def _validate_music(req: MusicRequest, c: Constraints, issues: _Issues) -> None:
    issues.one_of("output-format", req.output_format, c.audio.formats, label="output format")
    issues.needs("plan", req.composition_plan, c, "composition_plan", label="a composition plan")
    issues.needs("seed", req.seed is not None, c, "seed", label="--seed")
    issues.in_range("duration-ms", req.duration_ms, c.audio.duration_ms, label="duration", unit="ms")


def _validate_music_plan(req: MusicPlanRequest, c: Constraints, issues: _Issues) -> None:
    issues.needs("plan", True, c, "composition_plan", label="composition plans")
    issues.in_range("duration-ms", req.duration_ms, c.audio.duration_ms, label="duration", unit="ms")


def _validate_sound(req: SoundEffectRequest, c: Constraints, issues: _Issues) -> None:
    issues.one_of("output-format", req.output_format, c.audio.formats, label="output format")
    issues.in_range("duration-seconds", req.duration_seconds, c.audio.duration_s, label="duration", unit="s")


_VALIDATORS = (
    (ImageRequest, _validate_image),
    (VideoRequest, _validate_video),
    (DialogueRequest, _validate_dialogue),  # before SpeechRequest: not a subclass, but order documents intent
    (SpeechRequest, _validate_speech),
    (MusicPlanRequest, _validate_music_plan),
    (MusicRequest, _validate_music),
    (SoundEffectRequest, _validate_sound),
)


def validate_request(
    req,
    constraints: Constraints,
    policy: UnsupportedPolicy = UnsupportedPolicy.ERROR,
    *,
    binding: str = "",
    scene: Scene | None = None,
) -> list[str]:
    """Check a request against what its binding declares.

    Returns human-readable warnings (empty when clean). ``ERROR`` — the default —
    raises instead; ``WARN`` hands them back to be logged; ``IGNORE`` drops them.
    """
    issues = _Issues()
    for request_type, validator in _VALIDATORS:
        if isinstance(req, request_type):
            validator(req, constraints, issues)
            break

    _check_options(getattr(req, "options", {}) or {}, constraints.options, issues)

    if not issues:
        return []
    messages = [f"{field}: {reason}" for field, reason in issues]
    if policy is UnsupportedPolicy.IGNORE:
        return []
    if policy is UnsupportedPolicy.WARN:
        return messages
    raise MediaError(
        f"request not supported by {binding or 'this binding'}: " + "; ".join(messages),
        category=ErrorCategory.UNSUPPORTED,
        code="request_not_supported",
        details={
            "binding": binding or None,
            "scene": scene.value if scene else None,
            "unsupported": [{"field": field, "reason": reason} for field, reason in issues],
        },
        hint="media-ai capabilities --binding " + binding if binding else "media-ai capabilities",
    )


def _check_options(options: dict, allowed: tuple[str, ...], issues: _Issues) -> None:
    """Provider-specific knobs are opt-in per binding, so an unknown one is a typo.

    Passing it through would send a parameter the API may silently ignore — the caller
    would see a result that looks fine and does not reflect what they asked for.
    """
    for key in sorted(set(options) - set(allowed)):
        allowed_text = ", ".join(allowed) if allowed else "none"
        issues.add(f"option:{key}", f"not supported by this binding; allowed: {allowed_text}")


def _paths(refs: list[MediaRef]) -> list[Path]:  # pragma: no cover - debugging aid
    return [r.path() for r in refs if r.is_local]
