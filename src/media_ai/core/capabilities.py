"""Capability descriptors + request validation.

Each model declares a :class:`ModelCapabilities`. It drives two things:

* **Discovery** — ``media-ai capabilities`` serializes these so an Agent Skill can
  learn what a provider/model supports *before* asking for it.
* **Validation** — every request is checked against the resolved model's
  capabilities *before* any network call. Unsupported operations/options/geometry
  fail deterministically (``UNSUPPORTED``/``VALIDATION`` → exit 3) instead of being
  silently dropped, unless the caller relaxes the policy with ``--on-unsupported``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

from .errors import ErrorCategory, MediaError
from .types import (
    DialogueRequest,
    GeometrySpec,
    ImageRequest,
    Modality,
    MusicPlanRequest,
    MusicRequest,
    Operation,
    SoundEffectRequest,
    SpeechRequest,
    VideoRequest,
)


class GeometryMode(str, Enum):
    PIXELS = "pixels"  # WxH only
    ASPECT_RATIO = "aspect_ratio"  # ratio (+ optional tier) only
    BOTH = "both"
    NONE = "none"  # geometry not configurable


class UnsupportedPolicy(str, Enum):
    ERROR = "error"
    WARN = "warn"
    IGNORE = "ignore"


@dataclass
class ImageCaps:
    operations: frozenset[Operation] = frozenset({Operation.IMAGE_GENERATE})
    geometry_mode: GeometryMode = GeometryMode.PIXELS
    aspect_ratios: tuple[str, ...] = ()
    named_sizes: tuple[str, ...] = ()  # e.g. "1K", "2K"
    pixel_sizes: tuple[str, ...] = ()  # exact allowed WxH, if fixed-enum
    pixel_max: tuple[int, int] | None = None  # per-edge max (applies to width *and* height)
    pixel_multiple: int | None = None
    pixel_total_min: int | None = None  # min width*height (e.g. gpt-image-2: 655360)
    pixel_total_max: int | None = None  # max width*height (e.g. gpt-image-2: 8294400)
    max_edge_ratio: float | None = None  # max long-edge:short-edge ratio (e.g. 3.0 for 3:1)
    max_count: int = 1
    output_formats: tuple[str, ...] = ("png",)
    supports_seed: bool = False
    supports_negative_prompt: bool = False
    supports_transparency: bool = False
    supports_quality: bool = False
    supports_mask: bool = False
    max_references: int = 0
    supports_streaming: bool = False
    options: tuple[str, ...] = ()


@dataclass
class VideoCaps:
    operations: frozenset[Operation] = frozenset({Operation.VIDEO_GENERATE})
    is_async: bool = True
    aspect_ratios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    durations: tuple[int, ...] = ()
    supports_first_frame: bool = False
    supports_last_frame: bool = False
    supports_reference_images: bool = False
    supports_reference_videos: bool = False
    supports_reference_audios: bool = False
    supports_seed: bool = False
    supports_negative_prompt: bool = False
    supports_audio: bool = False
    audio_default: bool | None = None
    supports_watermark_control: bool = False
    supports_return_last_frame: bool = False
    supports_cancel: bool = True
    options: tuple[str, ...] = ()


@dataclass
class AudioCaps:
    operations: frozenset[Operation] = frozenset({Operation.SPEECH_GENERATE})
    # --- speech / dialogue (speech.generate, speech.dialogue) ---
    voices: tuple[str, ...] = ()  # known/preset voice ids (informational)
    default_voice: str | None = None
    output_formats: tuple[str, ...] = ("mp3_44100_128",)  # speech codec_samplerate_bitrate
    supports_seed: bool = False
    supports_language_code: bool = False
    supports_timestamps: bool = False  # per-character alignment sidecar
    supports_dialogue: bool = False  # multi-voice (speech.dialogue)
    supports_instruction: bool = False  # global director note for dialogue
    max_dialogue_voices: int = 0  # 0 = dialogue unsupported
    max_characters: int | None = None  # per-request text budget
    options: tuple[str, ...] = ()  # speech options
    # --- music (music.generate, music.plan) ---
    supports_music: bool = False
    supports_composition_plan: bool = False  # compose from / generate a plan
    music_models: tuple[str, ...] = ()
    music_output_formats: tuple[str, ...] = ()
    music_min_ms: int | None = None
    music_max_ms: int | None = None
    music_options: tuple[str, ...] = ()
    # --- sound effects (sound.generate) ---
    supports_sound: bool = False
    # Mirrors ``music_models``: sound.generate runs on its own model, distinct from the
    # TTS model ``models()`` enumerates, so discovery needs it named here to be complete.
    sound_models: tuple[str, ...] = ()
    sound_output_formats: tuple[str, ...] = ()
    sound_min_seconds: float | None = None
    sound_max_seconds: float | None = None
    sound_options: tuple[str, ...] = ()


@dataclass
class ModelCapabilities:
    provider: str
    model: str
    modalities: frozenset[Modality]
    image: ImageCaps | None = None
    video: VideoCaps | None = None
    audio: AudioCaps | None = None
    notes: tuple[str, ...] = ()
    experimental: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        def enc(v):
            if isinstance(v, Enum):
                return v.value
            if isinstance(v, frozenset):
                return sorted(x.value if isinstance(x, Enum) else x for x in v)
            return v

        d = asdict(self)
        d["modalities"] = sorted(m.value for m in self.modalities)
        for key in ("image", "video", "audio"):
            if d[key] is not None:
                sub = d[key]
                sub["operations"] = sorted(o.value for o in getattr(self, key).operations)
                if key == "image":
                    sub["geometry_mode"] = self.image.geometry_mode.value  # type: ignore[union-attr]
                d[key] = {k: enc(v) for k, v in sub.items()}
        d["notes"] = list(self.notes)
        d["aliases"] = list(self.aliases)
        return d


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


class _Issues(list):
    """Collected validation problems. Each item is ``(field, reason)``."""

    def add(self, field_: str, reason: str) -> None:
        self.append((field_, reason))


def _check_geometry(geo: GeometrySpec | None, caps: ImageCaps | VideoCaps, issues: _Issues) -> None:
    if geo is None or geo.mode is None:
        return
    mode = getattr(caps, "geometry_mode", None)
    if isinstance(caps, ImageCaps):
        if mode == GeometryMode.NONE:
            issues.add("geometry", "this model does not accept a configurable size")
            return
        if geo.mode == "pixels" and mode == GeometryMode.ASPECT_RATIO:
            issues.add("size", "this model takes --aspect-ratio, not pixel --size")
        if geo.mode == "ratio" and mode == GeometryMode.PIXELS:
            issues.add("aspect-ratio", "this model takes pixel --size, not --aspect-ratio")
        if geo.aspect_ratio and caps.aspect_ratios and geo.aspect_ratio not in caps.aspect_ratios:
            issues.add("aspect-ratio", f"unsupported ratio {geo.aspect_ratio!r}; allowed: {', '.join(caps.aspect_ratios)}")
        if geo.resolution and caps.named_sizes and geo.resolution not in caps.named_sizes:
            issues.add("resolution", f"unsupported size tier {geo.resolution!r}; allowed: {', '.join(caps.named_sizes)}")
        if geo.mode == "pixels" and caps.pixel_multiple and (geo.width % caps.pixel_multiple or geo.height % caps.pixel_multiple):  # type: ignore[operator]
            issues.add("size", f"width & height must be multiples of {caps.pixel_multiple}")
        if geo.mode == "pixels" and caps.pixel_max and (geo.width > caps.pixel_max[0] or geo.height > caps.pixel_max[1]):  # type: ignore[operator]
            issues.add("size", f"exceeds max edge {caps.pixel_max[0]}x{caps.pixel_max[1]}")
        if geo.mode == "pixels" and (caps.pixel_total_min or caps.pixel_total_max):
            total = geo.width * geo.height  # type: ignore[operator]
            if caps.pixel_total_min and total < caps.pixel_total_min:
                issues.add("size", f"total pixels {total} below minimum {caps.pixel_total_min}")
            if caps.pixel_total_max and total > caps.pixel_total_max:
                issues.add("size", f"total pixels {total} exceeds maximum {caps.pixel_total_max}")
        if geo.mode == "pixels" and caps.max_edge_ratio:
            long_e, short_e = max(geo.width, geo.height), min(geo.width, geo.height)  # type: ignore[type-var]
            if short_e and long_e / short_e > caps.max_edge_ratio + 1e-9:
                issues.add("size", f"edge ratio {long_e / short_e:.2f}:1 exceeds max {caps.max_edge_ratio:g}:1")
        if geo.mode == "pixels" and caps.pixel_sizes and f"{geo.width}x{geo.height}" not in caps.pixel_sizes:
            issues.add("size", f"unsupported size {geo.width}x{geo.height}; allowed: {', '.join(caps.pixel_sizes)}")
    else:  # VideoCaps
        if geo.aspect_ratio and geo.aspect_ratio != "adaptive" and caps.aspect_ratios and geo.aspect_ratio not in caps.aspect_ratios:
            issues.add("aspect-ratio", f"unsupported ratio {geo.aspect_ratio!r}; allowed: {', '.join(caps.aspect_ratios)}")
        if geo.resolution and caps.resolutions and geo.resolution not in caps.resolutions:
            issues.add("resolution", f"unsupported resolution {geo.resolution!r}; allowed: {', '.join(caps.resolutions)}")


def validate_request(req, caps: ModelCapabilities, policy: UnsupportedPolicy = UnsupportedPolicy.ERROR) -> list[str]:
    """Validate a request against a model's capabilities.

    Returns a list of human-readable warnings (empty if clean). With
    ``policy=ERROR`` (the default) any issue raises :class:`MediaError`. With
    ``WARN`` the issues are returned to be logged; with ``IGNORE`` they are dropped.
    """
    issues = _Issues()
    if isinstance(req, ImageRequest):
        _validate_image(req, caps, issues)
    elif isinstance(req, VideoRequest):
        _validate_video(req, caps, issues)
    elif isinstance(req, SpeechRequest):
        _validate_speech(req, caps, issues)
    elif isinstance(req, DialogueRequest):
        _validate_dialogue(req, caps, issues)
    elif isinstance(req, MusicRequest):
        _validate_music(req, caps, issues)
    elif isinstance(req, MusicPlanRequest):
        _validate_music_plan(req, caps, issues)
    elif isinstance(req, SoundEffectRequest):
        _validate_sound(req, caps, issues)

    if not issues:
        return []
    messages = [f"{f}: {r}" for f, r in issues]
    if policy == UnsupportedPolicy.ERROR:
        raise MediaError(
            f"request not supported by {caps.provider}/{caps.model}: " + "; ".join(messages),
            category=ErrorCategory.UNSUPPORTED,
            provider=caps.provider,
            model=caps.model,
            details={"unsupported": [{"field": f, "reason": r} for f, r in issues]},
        )
    if policy == UnsupportedPolicy.IGNORE:
        return []  # drop silently
    return messages  # WARN: hand back to be logged


def _validate_image(req: ImageRequest, caps: ModelCapabilities, issues: _Issues) -> None:
    ic = caps.image
    if ic is None:
        issues.add("modality", "model does not support image generation")
        return
    if req.operation not in ic.operations:
        issues.add("operation", f"{req.operation.value} not supported; allowed: {', '.join(o.value for o in ic.operations)}")
    _check_geometry(req.geometry, ic, issues)
    if req.count > ic.max_count:
        issues.add("count", f"max {ic.max_count} image(s) per request")
    if req.seed is not None and not ic.supports_seed:
        issues.add("seed", "model does not accept a seed")
    if req.negative_prompt and not ic.supports_negative_prompt:
        issues.add("negative-prompt", "model does not accept a negative prompt")
    if req.background and req.background != "auto" and not ic.supports_transparency and req.background == "transparent":
        issues.add("background", "model does not support transparent backgrounds")
    if req.quality and not ic.supports_quality:
        issues.add("quality", "model does not expose quality tiers")
    if req.mask and not ic.supports_mask:
        issues.add("mask", "model does not support masked edits")
    if req.references and ic.max_references == 0:
        issues.add("reference", "model does not accept reference images")
    if req.references and len(req.references) > ic.max_references > 0:
        issues.add("reference", f"max {ic.max_references} reference image(s)")
    if req.output_format and req.output_format not in ic.output_formats:
        issues.add("format", f"unsupported output format {req.output_format!r}; allowed: {', '.join(ic.output_formats)}")
    _check_options(req.options, ic.options, issues)


def _validate_video(req: VideoRequest, caps: ModelCapabilities, issues: _Issues) -> None:
    vc = caps.video
    if vc is None:
        issues.add("modality", "model does not support video generation")
        return
    if req.operation not in vc.operations:
        issues.add("operation", f"{req.operation.value} not supported")
    _check_geometry(req.geometry, vc, issues)
    if req.duration is not None and vc.durations and req.duration not in vc.durations:
        issues.add("duration", f"unsupported duration {req.duration}s; allowed: {', '.join(map(str, vc.durations))}")
    if req.first_frame and not vc.supports_first_frame:
        issues.add("first-frame", "model does not support a first-frame image")
    if req.last_frame and not vc.supports_last_frame:
        issues.add("last-frame", "model does not support a last-frame image")
    if req.reference_images and not vc.supports_reference_images:
        issues.add("reference-image", "model does not support reference images")
    if req.reference_videos and not vc.supports_reference_videos:
        issues.add("reference-video", "model does not support reference videos")
    if req.reference_audios and not vc.supports_reference_audios:
        issues.add("reference-audio", "model does not support reference audio")
    if req.seed is not None and not vc.supports_seed:
        issues.add("seed", "model does not accept a seed")
    if req.negative_prompt and not vc.supports_negative_prompt:
        issues.add("negative-prompt", "model does not accept a negative prompt")
    if req.audio is not None and not vc.supports_audio:
        issues.add("audio", "model does not support audio control")
    if req.watermark is not None and not vc.supports_watermark_control:
        issues.add("watermark", "model does not support watermark control")
    if req.return_last_frame and not vc.supports_return_last_frame:
        issues.add("return-last-frame", "model cannot return the output's last frame")
    _check_options(req.options, vc.options, issues)


def _validate_speech(req: SpeechRequest, caps: ModelCapabilities, issues: _Issues) -> None:
    ac = caps.audio
    if ac is None:
        issues.add("modality", "model does not support speech generation")
        return
    if req.operation not in ac.operations:
        issues.add("operation", f"{req.operation.value} not supported; allowed: {', '.join(o.value for o in ac.operations)}")
    if req.output_format and ac.output_formats and req.output_format not in ac.output_formats:
        issues.add("output-format", f"unsupported output format {req.output_format!r}; allowed: {', '.join(ac.output_formats)}")
    if req.seed is not None and not ac.supports_seed:
        issues.add("seed", "model does not accept a seed")
    if req.language_code and not ac.supports_language_code:
        issues.add("language-code", "model does not accept a language code")
    if req.timestamps and not ac.supports_timestamps:
        issues.add("timestamps", "model does not support character timestamps")
    if ac.max_characters is not None and len(req.text) > ac.max_characters:
        issues.add("text", f"exceeds max {ac.max_characters} characters")
    _check_options(req.options, ac.options, issues)


def _validate_dialogue(req: DialogueRequest, caps: ModelCapabilities, issues: _Issues) -> None:
    ac = caps.audio
    if ac is None or not ac.supports_dialogue:
        issues.add("modality", "model does not support dialogue generation")
        return
    if req.operation not in ac.operations:
        issues.add("operation", f"{req.operation.value} not supported; allowed: {', '.join(o.value for o in ac.operations)}")
    if not req.turns:
        issues.add("turns", "dialogue requires at least one turn")
    if not req.cast:
        issues.add("cast", "dialogue requires a cast mapping speaker names to voices")
    unknown = sorted({t.speaker for t in req.turns} - set(req.cast))
    if unknown:
        issues.add("turns", f"speaker(s) not in cast: {', '.join(unknown)}")
    if ac.max_dialogue_voices and len(req.voices()) > ac.max_dialogue_voices:
        issues.add("cast", f"max {ac.max_dialogue_voices} unique voices per dialogue")
    if req.instruction and not ac.supports_instruction:
        issues.add("instruction", "model does not support a global dialogue instruction")
    if req.output_format and ac.output_formats and req.output_format not in ac.output_formats:
        issues.add("output-format", f"unsupported output format {req.output_format!r}; allowed: {', '.join(ac.output_formats)}")
    if req.seed is not None and not ac.supports_seed:
        issues.add("seed", "model does not accept a seed")
    if req.language_code and not ac.supports_language_code:
        issues.add("language-code", "model does not accept a language code")
    if req.timestamps and not ac.supports_timestamps:
        issues.add("timestamps", "model does not support character timestamps")
    if ac.max_characters is not None and sum(len(t.text) for t in req.turns) > ac.max_characters:
        issues.add("text", f"total dialogue text exceeds max {ac.max_characters} characters")
    _check_options(req.options, ac.options, issues)


def _validate_music(req: MusicRequest, caps: ModelCapabilities, issues: _Issues) -> None:
    ac = caps.audio
    if ac is None or not ac.supports_music:
        issues.add("modality", "model does not support music generation")
        return
    if req.operation not in ac.operations:
        issues.add("operation", f"{req.operation.value} not supported; allowed: {', '.join(o.value for o in ac.operations)}")
    if bool(req.prompt) == bool(req.composition_plan):
        issues.add("prompt", "provide exactly one of a prompt or a composition plan")
    if req.composition_plan and not ac.supports_composition_plan:
        issues.add("composition-plan", "model does not support a composition plan")
    if req.seed is not None and req.prompt:
        issues.add("seed", "seed cannot be used with a prompt (composition-plan mode only)")
    if req.duration_ms is not None:
        if ac.music_min_ms is not None and req.duration_ms < ac.music_min_ms:
            issues.add("duration-ms", f"must be at least {ac.music_min_ms}ms")
        if ac.music_max_ms is not None and req.duration_ms > ac.music_max_ms:
            issues.add("duration-ms", f"must be at most {ac.music_max_ms}ms")
    if req.output_format and ac.music_output_formats and req.output_format not in ac.music_output_formats:
        issues.add("output-format", f"unsupported output format {req.output_format!r}; allowed: {', '.join(ac.music_output_formats)}")
    _check_options(req.options, ac.music_options, issues)


def _validate_music_plan(req: MusicPlanRequest, caps: ModelCapabilities, issues: _Issues) -> None:
    ac = caps.audio
    if ac is None or not ac.supports_composition_plan:
        issues.add("modality", "model does not support composition plans")
        return
    if req.operation not in ac.operations:
        issues.add("operation", f"{req.operation.value} not supported; allowed: {', '.join(o.value for o in ac.operations)}")
    if not req.prompt:
        issues.add("prompt", "a composition plan requires a prompt")
    if req.duration_ms is not None:
        if ac.music_min_ms is not None and req.duration_ms < ac.music_min_ms:
            issues.add("duration-ms", f"must be at least {ac.music_min_ms}ms")
        if ac.music_max_ms is not None and req.duration_ms > ac.music_max_ms:
            issues.add("duration-ms", f"must be at most {ac.music_max_ms}ms")
    _check_options(req.options, ac.music_options, issues)


def _validate_sound(req: SoundEffectRequest, caps: ModelCapabilities, issues: _Issues) -> None:
    ac = caps.audio
    if ac is None or not ac.supports_sound:
        issues.add("modality", "model does not support sound-effect generation")
        return
    if req.operation not in ac.operations:
        issues.add("operation", f"{req.operation.value} not supported; allowed: {', '.join(o.value for o in ac.operations)}")
    if not req.text:
        issues.add("text", "sound effect requires text")
    if req.duration_seconds is not None:
        if ac.sound_min_seconds is not None and req.duration_seconds < ac.sound_min_seconds:
            issues.add("duration-seconds", f"must be at least {ac.sound_min_seconds}s")
        if ac.sound_max_seconds is not None and req.duration_seconds > ac.sound_max_seconds:
            issues.add("duration-seconds", f"must be at most {ac.sound_max_seconds}s")
    if req.output_format and ac.sound_output_formats and req.output_format not in ac.sound_output_formats:
        issues.add("output-format", f"unsupported output format {req.output_format!r}; allowed: {', '.join(ac.sound_output_formats)}")
    _check_options(req.options, ac.sound_options, issues)


def _check_options(options: dict, allowed: tuple[str, ...], issues: _Issues) -> None:
    for key in options:
        if key not in allowed:
            hint = f"; allowed options: {', '.join(allowed)}" if allowed else ""
            issues.add(f"option:{key}", f"unknown provider option{hint}")
