"""ElevenLabs provider — text-to-speech and multi-voice dialogue.

Both operations are **synchronous** single requests:

* ``speech.generate`` → ``POST /v1/text-to-speech/{voice_id}`` returns raw audio
  bytes; ``--timestamps`` switches to ``/with-timestamps`` (JSON with base64 audio
  + per-character alignment, written as a sidecar artifact).
* ``speech.dialogue`` → ``POST /v1/text-to-dialogue`` renders a list of
  ``(text, voice_id)`` turns into one track; ``--timestamps`` uses
  ``/with-timestamps`` (adds ``voice_segments`` to the sidecar).

Provider-specific voice knobs (``stability``, ``similarity_boost``, ``style``,
``speed``, ``use_speaker_boost``, continuity hints, …) travel in ``--option`` and
are declared in ``AudioCaps.options``. Auth is the ``xi-api-key`` header
(``ELEVENLABS_API_KEY``). The base URL is configurable via ``ELEVENLABS_BASE_URL``
or a profile ``base_url`` (e.g. to target a regional residency endpoint).

Verified against elevenlabs.io/docs/api-reference.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from ..core.capabilities import AudioCaps, ModelCapabilities, Operation
from ..core.errors import ErrorCategory, MediaError
from ..core.mediaref import guess_mime
from ..core.result import Artifact, GenerationResult
from ..core.types import DialogueRequest, Modality, SpeechRequest
from ..core.usage import record_usage
from ._base import HttpProvider

# Output formats accepted by the API (codec_samplerate[_bitrate]).
_OUTPUT_FORMATS = (
    "alaw_8000", "mp3_22050_32", "mp3_24000_48", "mp3_44100_128", "mp3_44100_192",
    "mp3_44100_32", "mp3_44100_64", "mp3_44100_96", "opus_48000_128", "opus_48000_192",
    "opus_48000_32", "opus_48000_64", "opus_48000_96", "pcm_16000", "pcm_22050",
    "pcm_24000", "pcm_32000", "pcm_44100", "pcm_48000", "pcm_8000", "ulaw_8000",
    "wav_16000", "wav_22050", "wav_24000", "wav_32000", "wav_44100", "wav_48000", "wav_8000",
)
# Voice-settings + request knobs passed through --option (ElevenLabs-specific).
_VOICE_SETTINGS = ("stability", "similarity_boost", "style", "speed", "use_speaker_boost")
_BODY_OPTIONS = ("previous_text", "next_text", "apply_text_normalization", "apply_language_text_normalization")
_QUERY_OPTIONS = ("optimize_streaming_latency", "enable_logging")
_OPTIONS = _VOICE_SETTINGS + _BODY_OPTIONS + _QUERY_OPTIONS
# codec prefix -> mime, for when the file extension doesn't match --output-format.
_CODEC_MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "pcm": "audio/L16",
               "opus": "audio/opus", "ulaw": "audio/basic", "alaw": "audio/basic"}
_TTS_MODELS = ("eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_flash_v2_5", "eleven_v3")
_MAX_DIALOGUE_CHARS = 2000  # docs: keep total inputs[].text at/below this per request


class ElevenLabsProvider(HttpProvider):
    name = "elevenlabs"
    auth_scheme = "xi-api-key"

    def __init__(self, *, credentials=None, config=None) -> None:
        super().__init__(credentials=credentials, config=config)
        # Base URL is configurable (env or a --provider-profile base_url) so callers
        # can target a regional residency endpoint (us/eu/in/sg) without a code change.
        self.base_url = (self.config.get("base_url") or os.getenv("ELEVENLABS_BASE_URL")
                         or "https://api.elevenlabs.io/v1").rstrip("/")
        self.model = os.getenv("ELEVENLABS_MODEL") or "eleven_multilingual_v2"
        self.dialogue_model = os.getenv("ELEVENLABS_DIALOGUE_MODEL") or "eleven_v3"
        self.default_voice = os.getenv("ELEVENLABS_VOICE_ID") or "JBFqnCBsd6RMkjVDRZzb"

    # ---- discovery -------------------------------------------------------
    def models(self) -> list[str]:
        return list(_TTS_MODELS)

    def default_model(self, modality: Modality | None) -> str:
        return self.model

    def capabilities(self, model: str | None = None, modality: Modality | None = None) -> ModelCapabilities:
        model = model or self.model
        return ModelCapabilities(
            provider=self.name, model=model, modalities=frozenset({Modality.AUDIO}),
            audio=AudioCaps(
                operations=frozenset({Operation.SPEECH_GENERATE, Operation.SPEECH_DIALOGUE}),
                default_voice=self.default_voice,
                output_formats=_OUTPUT_FORMATS,
                supports_seed=True,
                supports_language_code=True,
                supports_timestamps=True,
                supports_dialogue=True,
                max_dialogue_voices=10,
                max_characters=_MAX_DIALOGUE_CHARS,
                options=_OPTIONS,
            ),
            notes=("mp3_44100_192 needs Creator tier+; pcm/wav 44.1kHz needs Pro tier+",
                   "language_code is ignored by multilingual_v2 models"),
        )

    # ---- speech (text -> single voice) -----------------------------------
    def generate_speech(self, req: SpeechRequest) -> GenerationResult:
        client, headers = self._prepare()
        model = req.model or self.model
        voice = req.voice or self.default_voice
        body: dict = {"text": req.text, "model_id": model}
        if req.language_code:
            body["language_code"] = req.language_code
        if req.seed is not None:
            body["seed"] = req.seed
        vs = {k: req.options[k] for k in _VOICE_SETTINGS if k in req.options}
        if vs:
            body["voice_settings"] = vs
        for k in _BODY_OPTIONS:
            if k in req.options:
                body[k] = req.options[k]
        qs = self._query(req)
        out = Path(req.output)
        if req.timestamps:
            data = client.request_json("POST", f"/text-to-speech/{voice}/with-timestamps{qs}", body=body, headers=headers)
            artifacts = self._write_timestamped(data, out, req.output_format)
        else:
            audio = client.request_bytes("POST", f"/text-to-speech/{voice}{qs}", body=body, headers=headers)
            artifacts = [self._write_audio(audio, out, req.output_format)]
        record_usage({"tool": "speech.generate", "operation": "speech.generate", "provider": self.name,
                      "model": model, "kind": "audio", "characters": len(req.text)})
        return GenerationResult(modality="audio", operation="speech.generate", provider=self.name, model=model,
                                artifacts=artifacts, usage={"characters": len(req.text)},
                                meta={"voice": voice, "output_format": req.output_format, "timestamps": req.timestamps})

    # ---- dialogue (multi-voice) ------------------------------------------
    def generate_dialogue(self, req: DialogueRequest) -> GenerationResult:
        if not req.turns:
            raise MediaError("dialogue requires at least one turn", category=ErrorCategory.VALIDATION, provider=self.name)
        client, headers = self._prepare()
        model = req.model or self.dialogue_model
        body: dict = {"inputs": [{"text": t.text, "voice_id": t.voice} for t in req.turns], "model_id": model}
        if req.language_code:
            body["language_code"] = req.language_code
        if req.seed is not None:
            body["seed"] = req.seed
        if "stability" in req.options:
            body["settings"] = {"stability": req.options["stability"]}
        if "apply_text_normalization" in req.options:
            body["apply_text_normalization"] = req.options["apply_text_normalization"]
        qs = self._query(req)
        out = Path(req.output)
        if req.timestamps:
            data = client.request_json("POST", f"/text-to-dialogue/with-timestamps{qs}", body=body, headers=headers)
            artifacts = self._write_timestamped(data, out, req.output_format)
        else:
            audio = client.request_bytes("POST", f"/text-to-dialogue{qs}", body=body, headers=headers)
            artifacts = [self._write_audio(audio, out, req.output_format)]
        chars = sum(len(t.text) for t in req.turns)
        record_usage({"tool": "speech.dialogue", "operation": "speech.dialogue", "provider": self.name,
                      "model": model, "kind": "audio", "characters": chars})
        return GenerationResult(modality="audio", operation="speech.dialogue", provider=self.name, model=model,
                                artifacts=artifacts, usage={"characters": chars},
                                meta={"voices": req.voices(), "output_format": req.output_format, "timestamps": req.timestamps})

    # ---- helpers ---------------------------------------------------------
    def _query(self, req) -> str:
        params: dict = {}
        if req.output_format:
            params["output_format"] = req.output_format
        for k in _QUERY_OPTIONS:
            if k in req.options:
                params[k] = req.options[k]
        return f"?{urlencode(params)}" if params else ""

    def _mime(self, out: Path, output_format: str | None) -> str:
        if output_format:
            return _CODEC_MIME.get(output_format.split("_", 1)[0], guess_mime(out, media="audio"))
        return guess_mime(out, media="audio")

    def _write_audio(self, audio: bytes, out: Path, output_format: str | None, *, role=None) -> Artifact:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        return Artifact.from_path(out, "audio", mime=self._mime(out, output_format), role=role)

    def _write_timestamped(self, data: dict, out: Path, output_format: str | None) -> list[Artifact]:
        b64 = data.get("audio_base64")
        if not b64:
            raise MediaError("ElevenLabs response had no audio", category=ErrorCategory.PROVIDER, provider=self.name)
        artifacts = [self._write_audio(base64.b64decode(b64), out, output_format)]
        sidecar = out.with_suffix(out.suffix + ".timestamps.json")
        payload = {k: data[k] for k in ("alignment", "normalized_alignment", "voice_segments") if data.get(k) is not None}
        sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.append(Artifact.from_path(sidecar, "timestamps", mime="application/json", role="alignment"))
        return artifacts

    # ---- errors ----------------------------------------------------------
    def _error(self, status: int, body: str) -> MediaError:
        cat = {400: ErrorCategory.VALIDATION, 401: ErrorCategory.AUTH, 403: ErrorCategory.AUTH,
               404: ErrorCategory.NOT_FOUND, 422: ErrorCategory.VALIDATION,
               429: ErrorCategory.RATE_LIMIT}.get(status, ErrorCategory.PROVIDER)
        return MediaError(f"ElevenLabs HTTP {status}: {body}", category=cat, provider=self.name, details={"status": status})
