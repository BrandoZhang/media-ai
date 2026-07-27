"""The model catalogue: every model each built-in provider knows about.

One file on purpose. "What do we support, what is on its way out, and what have we
actually checked?" is a question about the lineup as a whole, and it cannot be
answered by reading four adapters. Retiring a model is an edit here, not a new branch
in a capability method.

The ``caps`` dict on each spec carries only the parameters that *vary between models*
of the same provider. Everything shared stays in the adapter, which is what assembles
the ``ModelCapabilities`` — the shape is provider-specific, the data is not.

``verified`` dates come from ``docs/LIVE_TESTS.md`` and name only models that log
records being called against the real API. Everything else stays ``None`` — inventing
a date would turn "unknown" into "confirmed", the one thing this field must never do.
Note what that exposes: ``veo-3.1-generate-preview`` is the *default* video model and
has never been live-tested, while its lite and fast variants have. See ``docs/MODELS.md``.
"""

from __future__ import annotations

from ..core.modelspec import Catalog, ModelSpec, ModelStatus

# --------------------------------------------------------------------------
# shared vocabulary
# --------------------------------------------------------------------------

# Defined here rather than in the adapter so the catalogue and the code that reads it
# cannot drift apart — duplicating them once already produced a wrong Nano Banana 2
# ratio list that only the tests caught.
_STD_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
_ULTRAWIDE_RATIOS = ("1:4", "4:1", "1:8", "8:1")
_FLASH_RATIOS = _STD_RATIOS + _ULTRAWIDE_RATIOS

# --------------------------------------------------------------------------
# Google Gemini
# --------------------------------------------------------------------------

_NANO_BANANA_2 = {
    "tier": "flash",
    "aspect_ratios": _FLASH_RATIOS,
    "named_sizes": ("512", "1K", "2K", "4K"),
    "max_references": 14,
    "options": ("grounding", "thinking_level"),
}

GEMINI = Catalog(
    "gemini",
    (
        # -- images (Nano Banana) ----------------------------------------
        ModelSpec(
            id="gemini-3.1-flash-image",
            verified="2026-07-12",
            notes=("Nano Banana 2: 512px/1K/2K/4K, Google Search grounding, "
                   "thinking_level minimal|high, video-to-image",),
            caps=_NANO_BANANA_2,
        ),
        ModelSpec(
            id="gemini-3.1-flash-lite-image",
            verified="2026-07-11",
            notes=("Nano Banana 2 Lite: 1K only, no grounding; tuned for speed and scale",),
            caps={"tier": "lite", "aspect_ratios": _STD_RATIOS, "named_sizes": ("1K",),
                  "max_references": 14, "options": ()},
        ),
        ModelSpec(
            id="gemini-3-pro-image",
            verified="2026-07-11",
            notes=("Nano Banana Pro: 1K/2K/4K, Google Search grounding, interleaved output; "
                   "thinking is always on",),
            caps={"tier": "pro", "aspect_ratios": _STD_RATIOS, "named_sizes": ("1K", "2K", "4K"),
                  "max_references": 14, "options": ("grounding",)},
        ),
        ModelSpec(
            id="gemini-2.5-flash-image",
            verified="2026-07-11",
            status=ModelStatus.DEPRECATED,
            replacement="gemini-3.1-flash-image",
            reason="superseded by Nano Banana 2",
            notes=("Nano Banana (2.5, legacy): imageSize fixed at 1K, up to 3 refs",),
            caps={"tier": "legacy", "aspect_ratios": _STD_RATIOS, "named_sizes": ("1K",),
                  "max_references": 3, "options": ()},
        ),
        # Any other gemini-*-image id: treat as the current flash tier rather than
        # guessing. Declared last so the specific ids above win.
        ModelSpec(
            id="gemini-image-unknown",
            synthetic=True,
            matches=("gemini-",),
            discoverable=False,
            notes=("unrecognised Gemini image model — described as the current Nano Banana "
                   "tier; the API is the authority",),
            caps=_NANO_BANANA_2,
        ),

        # -- video (Veo) --------------------------------------------------
        ModelSpec(
            id="veo-3.1-generate-preview",
            status=ModelStatus.PREVIEW,
            notes=("Veo 3.1: first/last frame, up to 3 reference images (--reference-image), and "
                   "video extension (--reference-video continues a Veo clip ≤141s); durationSeconds "
                   "must be 8 for extension/reference-images/1080p/4K",),
            caps={"resolutions": ("720p", "1080p", "4k"), "durations": (4, 6, 8), "audio": True,
                  "last_frame": True, "references": True},
        ),
        ModelSpec(
            id="veo-3.1-fast-generate-preview",
            verified="2026-07-11",
            status=ModelStatus.PREVIEW,
            notes=("Veo 3.1 Fast: same feature set as Veo 3.1, tuned for latency",),
            caps={"resolutions": ("720p", "1080p", "4k"), "durations": (4, 6, 8), "audio": True,
                  "last_frame": True, "references": True},
        ),
        ModelSpec(
            id="veo-3.1-lite-generate-preview",
            verified="2026-07-11",
            status=ModelStatus.PREVIEW,
            notes=("Veo 3.1 Lite: text/image-to-video, 720p/1080p; no reference images, "
                   "extension, or 4K",),
            caps={"resolutions": ("720p", "1080p"), "durations": (4, 6, 8), "audio": True,
                  "last_frame": True, "references": False},
        ),
        ModelSpec(
            id="veo-3.0-generate-preview",
            status=ModelStatus.DEPRECATED,
            replacement="veo-3.1-generate-preview",
            matches=("veo-3.0",),
            discoverable=False,
            caps={"resolutions": ("720p", "1080p", "4k"), "durations": (4, 6, 8), "audio": True,
                  "last_frame": False, "references": False},
        ),
        ModelSpec(
            id="veo-2.0-generate-001",
            status=ModelStatus.DEPRECATED,
            replacement="veo-3.1-generate-preview",
            reason="Veo 2 is silent and 720p-only",
            matches=("veo-2",),
            discoverable=False,
            caps={"resolutions": ("720p",), "durations": (5, 6, 7, 8), "audio": False,
                  "last_frame": False, "references": False},
        ),
        ModelSpec(
            id="veo-unknown",
            synthetic=True,
            matches=("veo",),
            discoverable=False,
            notes=("unrecognised Veo model — described as the current 3.1 tier",),
            caps={"resolutions": ("720p", "1080p", "4k"), "durations": (4, 6, 8), "audio": True,
                  "last_frame": True, "references": True},
        ),

        # -- speech (TTS) -------------------------------------------------
        ModelSpec(
            id="gemini-2.5-flash-preview-tts",
            verified="2026-07-12",
            status=ModelStatus.PREVIEW,
            caps={"kind": "tts"},
        ),
        ModelSpec(
            id="gemini-2.5-pro-preview-tts",
            status=ModelStatus.PREVIEW,
            caps={"kind": "tts"},
        ),
        ModelSpec(
            id="gemini-3.1-flash-tts-preview",
            status=ModelStatus.PREVIEW,
            caps={"kind": "tts"},
        ),
        ModelSpec(
            id="gemini-tts-unknown",
            synthetic=True,
            matches=("gemini-tts",),
            discoverable=False,
            caps={"kind": "tts"},
        ),

        # -- removed ------------------------------------------------------
        ModelSpec(
            id="imagen-3.0-generate-002",
            status=ModelStatus.REMOVED,
            replacement="a Nano Banana model such as gemini-3.1-flash-image",
            reason="deprecated by Google",
            matches=("imagen",),
            discoverable=False,
        ),
    ),
    fallback="gemini-image-unknown",
)

# --------------------------------------------------------------------------
# OpenAI
# --------------------------------------------------------------------------

_GPT_IMAGE_1_LIKE = {
    "arbitrary_sizes": False,
    "input_fidelity": True,
    "transparency": True,
}

OPENAI = Catalog(
    "openai",
    (
        ModelSpec(
            id="gpt-image-2",
            verified="2026-07-12",
            matches=("gpt-image-2",),
            notes=("gpt-image-2: arbitrary sizes — both edges ÷16, max edge 3840px, edge ratio ≤3:1, "
                   "total pixels 655360–8294400; no transparent background",),
            caps={"arbitrary_sizes": True, "input_fidelity": False, "transparency": False},
        ),
        ModelSpec(
            id="gpt-image-1.5",
            notes=("token-billed; base64 output only; fixed sizes 1024x1024/1536x1024/1024x1536",),
            caps=_GPT_IMAGE_1_LIKE,
        ),
        ModelSpec(
            id="gpt-image-1",
            notes=("token-billed; base64 output only; fixed sizes 1024x1024/1536x1024/1024x1536",),
            caps=_GPT_IMAGE_1_LIKE,
        ),
        ModelSpec(
            id="gpt-image-1-mini",
            notes=("token-billed; base64 output only; fixed sizes 1024x1024/1536x1024/1024x1536",),
            # The mini tier does not expose input_fidelity.
            caps={"arbitrary_sizes": False, "input_fidelity": False, "transparency": True},
        ),
        ModelSpec(
            id="gpt-image-unknown",
            synthetic=True,
            matches=("gpt-image",),
            discoverable=False,
            notes=("unrecognised GPT Image model — described as the fixed-size tier",),
            caps=_GPT_IMAGE_1_LIKE,
        ),
        ModelSpec(
            id="dall-e-3",
            status=ModelStatus.REMOVED,
            replacement="gpt-image-2",
            reason="the current Images API rejects its response_format",
            matches=("dall-e",),
            discoverable=False,
        ),
        ModelSpec(
            id="sora",
            status=ModelStatus.REMOVED,
            replacement="a Veo or Seedance model via --provider gemini/volc",
            reason="video generation is not supported on this provider",
            matches=("sora",),
            discoverable=False,
        ),
    ),
    fallback="gpt-image-unknown",
)

# --------------------------------------------------------------------------
# Volcengine Ark
# --------------------------------------------------------------------------

VOLC = Catalog(
    "volc",
    (
        ModelSpec(
            id="doubao-seedream-4-5-251128",
            matches=("doubao-seedream", "seedream"),
            notes=("size below 2560x1440 falls back to the '2K' preset",),
            caps={"modality": "image"},
        ),
        ModelSpec(
            id="doubao-seedance-2-0-260128",
            matches=("doubao-seedance", "seedance"),
            notes=("model IDs are account-specific; enable them in the Volcengine console",),
            caps={"modality": "video"},
        ),
    ),
    # No fallback: an Ark id is usually a custom endpoint (ep-…) that names a
    # deployment rather than a model, and guessing what it serves is exactly the
    # mistake `Provider.backing_model` exists to avoid. The adapter handles those.
)

# --------------------------------------------------------------------------
# ElevenLabs
# --------------------------------------------------------------------------

_ELEVEN_TTS = {"kind": "tts"}

ELEVENLABS = Catalog(
    "elevenlabs",
    (
        ModelSpec(id="eleven_multilingual_v2", verified="2026-07-12", caps=_ELEVEN_TTS),
        ModelSpec(id="eleven_turbo_v2_5", caps=_ELEVEN_TTS),
        ModelSpec(id="eleven_flash_v2_5", caps=_ELEVEN_TTS),
        ModelSpec(id="eleven_v3", caps=_ELEVEN_TTS),
        ModelSpec(id="music_v1", discoverable=False, caps={"kind": "music"}),
        ModelSpec(id="music_v2", discoverable=False, caps={"kind": "music"}),
        ModelSpec(id="eleven_text_to_sound_v2", discoverable=False, caps={"kind": "sound"}),
        ModelSpec(
            id="eleven-unknown",
            synthetic=True,
            matches=("eleven_", "eleven-"),
            discoverable=False,
            caps=_ELEVEN_TTS,
        ),
    ),
    fallback="eleven-unknown",
)

CATALOGS = {"gemini": GEMINI, "openai": OPENAI, "volc": VOLC, "elevenlabs": ELEVENLABS}
