"""Provider-agnostic request types shared by the CLI and every adapter.

The CLI parses argv into an :class:`ImageRequest` / :class:`VideoRequest`; the
registry stamps the resolved ``model`` onto it; the selected provider adapter
translates it to that provider's wire format. Nothing here is provider-specific —
concepts that only one provider understands live in the ``options`` dict and are
capability-gated (see :mod:`media_ai.core.validate`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Modality(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


_REMOTE_PREFIXES = ("http://", "https://", "data:", "asset://", "gs://")


@dataclass(frozen=True)
class MediaRef:
    """One input media reference: a local path, URL, data-URI, ``asset://`` id, or
    ``gs://`` object. Adapters materialize it into whatever form the provider needs
    (multipart bytes, inline base64, a Files-API URI)."""

    raw: str
    role: str | None = None  # reference_image | first_frame | last_frame | continue_from | ...

    @property
    def is_remote(self) -> bool:
        return self.raw.startswith(_REMOTE_PREFIXES)

    @property
    def is_local(self) -> bool:
        return not self.is_remote

    def path(self) -> Path:
        return Path(self.raw)


@dataclass(frozen=True)
class GeometrySpec:
    """Normalized output geometry. Either explicit pixels (``width``+``height``)
    or an aspect-ratio + a named resolution tier. Each adapter resolves this to
    its own wire form and validates it against the model's capabilities."""

    width: int | None = None
    height: int | None = None
    aspect_ratio: str | None = None  # e.g. "16:9"
    resolution: str | None = None  # e.g. "2K" (image) or "1080p" (video)

    @property
    def mode(self) -> str | None:
        if self.width and self.height:
            return "pixels"
        if self.aspect_ratio or self.resolution:
            return "ratio"
        return None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class ImageRequest:
    prompt: str
    output: Path
    references: list[MediaRef] = field(default_factory=list)  # i2i / edit inputs
    geometry: GeometrySpec | None = None
    count: int = 1  # n / sampleCount / candidateCount
    seed: int | None = None
    negative_prompt: str | None = None
    background: str | None = None  # transparent | opaque | auto
    quality: str | None = None  # low | medium | high | auto
    output_format: str | None = None  # png | jpeg | webp
    model: str | None = None  # resolved model id (registry fills this in)
    options: dict = field(default_factory=dict)  # capability-gated provider extras

    @property
    def modality(self) -> Modality:
        return Modality.IMAGE


@dataclass
class VideoRequest:
    prompt: str
    output: Path
    first_frame: MediaRef | None = None
    last_frame: MediaRef | None = None
    reference_images: list[MediaRef] = field(default_factory=list)
    reference_videos: list[MediaRef] = field(default_factory=list)
    reference_audios: list[MediaRef] = field(default_factory=list)
    # Distinct from `reference_videos` on purpose: a reference is material the model
    # draws on, while this is a clip the model continues from its final frame (Veo's
    # extension, which takes the URI of a prior Veo output). Same file type, different
    # role — and the role is what picks the scene. See core/scene.py.
    continue_from: MediaRef | None = None
    geometry: GeometrySpec | None = None
    duration: int | None = None  # seconds
    seed: int | None = None
    audio: bool | None = None  # None = provider/model default
    negative_prompt: str | None = None
    watermark: bool | None = None
    return_last_frame: bool = False
    wait: bool = True  # False = submit and return a JobHandle
    model: str | None = None
    options: dict = field(default_factory=dict)

    @property
    def modality(self) -> Modality:
        return Modality.VIDEO


@dataclass
class SpeechRequest:
    """Single-voice text-to-speech. ``voice`` is the provider's voice id; provider-
    specific voice knobs (stability, similarity_boost, style, …) travel in ``options``
    and are capability-gated. ``timestamps`` requests character-level alignment."""

    text: str
    output: Path
    voice: str | None = None  # provider voice id (falls back to a provider default)
    output_format: str | None = None  # e.g. mp3_44100_128 (provider wire format)
    language_code: str | None = None  # ISO 639-1
    seed: int | None = None
    timestamps: bool = False  # also request per-character timing (sidecar artifact)
    model: str | None = None  # resolved model id (registry fills this in)
    options: dict = field(default_factory=dict)  # capability-gated provider extras

    @property
    def modality(self) -> Modality:
        return Modality.AUDIO


@dataclass(frozen=True)
class DialogueTurn:
    """One line of a multi-voice dialogue: which named speaker says what. The voice
    for a speaker is assigned once via :attr:`DialogueRequest.cast`."""

    speaker: str  # cast name (mapped to a voice by DialogueRequest.cast)
    text: str


@dataclass
class DialogueRequest:
    """Multi-voice dialogue. A ``cast`` maps each speaker name to a provider voice id;
    ``turns`` is the ordered script referencing those speakers. Rendered into one audio
    track. Some providers (e.g. Gemini) accept a global ``instruction`` directing the
    whole performance; provider knobs go in ``options`` (capability-gated)."""

    turns: list[DialogueTurn]
    output: Path
    cast: dict[str, str] = field(default_factory=dict)  # speaker name -> voice id
    instruction: str | None = None  # global director note (provider-gated)
    output_format: str | None = None
    language_code: str | None = None
    seed: int | None = None
    timestamps: bool = False
    model: str | None = None
    options: dict = field(default_factory=dict)

    @property
    def modality(self) -> Modality:
        return Modality.AUDIO

    def speakers(self) -> list[str]:
        """Cast speaker names, in declared order."""
        return list(self.cast.keys())

    def voices(self) -> list[str]:
        """Unique voice ids across the cast, in first-seen order."""
        seen: list[str] = []
        for v in self.cast.values():
            if v not in seen:
                seen.append(v)
        return seen


@dataclass
class MusicRequest:
    """Compose a song from a text ``prompt`` **or** a structured ``composition_plan``
    (exactly one). Provider-specific knobs (force_instrumental, sign_with_c2pa, …)
    travel in ``options``. ``detailed`` also captures the model's plan + song metadata
    as a sidecar artifact."""

    output: Path
    prompt: str | None = None
    composition_plan: dict | None = None  # loaded from a --plan JSON file
    duration_ms: int | None = None  # song length (prompt mode only)
    output_format: str | None = None  # e.g. mp3_44100_128, or "auto"
    seed: int | None = None  # plan mode only
    detailed: bool = False  # also write a <stem>.metadata.json sidecar
    model: str | None = None
    options: dict = field(default_factory=dict)  # capability-gated provider extras

    @property
    def modality(self) -> Modality:
        return Modality.AUDIO


@dataclass
class MusicPlanRequest:
    """Generate a composition plan (JSON) from a prompt — a credit-free helper whose
    output can be edited and fed back into :class:`MusicRequest.composition_plan`."""

    prompt: str
    output: Path  # the plan JSON is written here
    duration_ms: int | None = None
    source_plan: dict | None = None  # optional source composition plan to refine
    model: str | None = None
    options: dict = field(default_factory=dict)

    @property
    def modality(self) -> Modality:
        return Modality.AUDIO


@dataclass
class SoundEffectRequest:
    """Turn ``text`` into a sound effect. ``duration_seconds`` is optional (the model
    guesses when omitted). Provider knobs (loop, prompt_influence) go in ``options``."""

    text: str
    output: Path
    duration_seconds: float | None = None
    output_format: str | None = None
    model: str | None = None
    options: dict = field(default_factory=dict)

    @property
    def modality(self) -> Modality:
        return Modality.AUDIO


@dataclass(frozen=True)
class JobRef:
    """Identifies an async job for ``job query`` / ``job cancel``."""

    provider: str
    id: str
    model: str | None = None
