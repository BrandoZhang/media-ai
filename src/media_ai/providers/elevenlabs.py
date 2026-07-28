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
from pathlib import Path
from urllib.parse import urlencode

from ..core.errors import ErrorCategory, MediaError
from ..core.mediaref import guess_mime
from ..core.scene import Scene
from ..core.result import Artifact, GenerationResult
from ..core.types import DialogueRequest, MusicPlanRequest, MusicRequest, SoundEffectRequest, SpeechRequest
from ..core.usage import record_usage
from ._base import HttpAdapter

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

# Music (compose) — models, output formats (incl. "auto"), and --option knobs.
_MUSIC_MODELS = ("music_v1", "music_v2")
_MUSIC_OUTPUT_FORMATS = (
    "auto", "mp3_48000_128", "mp3_48000_192", "mp3_48000_240", "mp3_48000_320",
    "mp3_22050_32", "mp3_24000_48", "mp3_44100_32", "mp3_44100_64", "mp3_44100_96",
    "mp3_44100_128", "mp3_44100_192", "pcm_8000", "pcm_16000", "pcm_22050", "pcm_24000",
    "pcm_32000", "pcm_44100", "pcm_48000", "ulaw_8000", "alaw_8000",
    "opus_48000_32", "opus_48000_64", "opus_48000_96", "opus_48000_128", "opus_48000_192",
)
_MUSIC_OPTIONS = ("force_instrumental", "respect_sections_durations", "store_for_inpainting",
                  "sign_with_c2pa", "with_timestamps")
_MUSIC_MIN_MS, _MUSIC_MAX_MS = 3000, 600000
# Sound effects — output formats (no "auto") and --option knobs.
_SOUND_MODEL = "eleven_text_to_sound_v2"
_SOUND_OUTPUT_FORMATS = (
    "mp3_22050_32", "mp3_24000_48", "mp3_44100_32", "mp3_44100_64", "mp3_44100_96",
    "mp3_44100_128", "mp3_44100_192", "pcm_8000", "pcm_16000", "pcm_22050", "pcm_24000",
    "pcm_32000", "pcm_44100", "pcm_48000", "ulaw_8000", "alaw_8000",
    "opus_48000_32", "opus_48000_64", "opus_48000_96", "opus_48000_128", "opus_48000_192",
)
_SOUND_OPTIONS = ("loop", "prompt_influence")
_SOUND_MIN_S, _SOUND_MAX_S = 0.5, 30.0
# Used when neither the binding nor its manifest names one.
_FALLBACK_VOICE = "JBFqnCBsd6RMkjVDRZzb"


class ElevenLabsAdapter(HttpAdapter):

    def supported_scenes(self) -> frozenset[Scene]:
        return frozenset({
            Scene.SPEECH_TEXT_TO_SPEECH, Scene.SPEECH_DIALOGUE,
            Scene.MUSIC_TEXT_TO_MUSIC, Scene.MUSIC_PLAN_TO_MUSIC, Scene.MUSIC_PLAN,
            Scene.SOUND_TEXT_TO_SOUND,
        })

    @property
    def default_voice(self) -> str:
        """The voice used when a call names none.

        From the binding, then its manifest — a house voice is a property of how this
        integration is set up, not of the machine it happens to run on.
        """
        return self.option("voice") or self.constraints.audio.default_voice or _FALLBACK_VOICE


    # ---- discovery -------------------------------------------------------
    # ---- speech (text -> single voice) -----------------------------------
    def generate_speech(self, req: SpeechRequest) -> GenerationResult:
        client, headers = self._prepare()
        model = req.model or self.model_id
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
        if not req.turns or not req.cast:
            raise MediaError("dialogue requires turns and a cast", category=ErrorCategory.VALIDATION, provider=self.name)
        missing = sorted({t.speaker for t in req.turns} - set(req.cast))
        if missing:
            raise MediaError(f"speaker(s) not in cast: {', '.join(missing)}",
                             category=ErrorCategory.VALIDATION, provider=self.name)
        client, headers = self._prepare()
        model = req.model or self.model_id
        body: dict = {"inputs": [{"text": t.text, "voice_id": req.cast[t.speaker]} for t in req.turns], "model_id": model}
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

    # ---- music (compose) -------------------------------------------------
    def generate_music(self, req: MusicRequest) -> GenerationResult:
        if bool(req.prompt) == bool(req.composition_plan):
            raise MediaError("music requires exactly one of a prompt or a composition plan",
                             category=ErrorCategory.VALIDATION, provider=self.name)
        client, headers = self._prepare()
        model = req.model or self.model_id
        body: dict = {"model_id": model}
        if req.composition_plan is not None:
            body["composition_plan"] = req.composition_plan
            if req.seed is not None:
                body["seed"] = req.seed
            if "respect_sections_durations" in req.options:
                body["respect_sections_durations"] = req.options["respect_sections_durations"]
        else:
            body["prompt"] = req.prompt
            if req.duration_ms is not None:
                body["music_length_ms"] = req.duration_ms
            if "force_instrumental" in req.options:
                body["force_instrumental"] = req.options["force_instrumental"]
        for k in ("store_for_inpainting", "sign_with_c2pa"):
            if k in req.options:
                body[k] = req.options[k]
        qs = self._format_qs(req.output_format)
        out = Path(req.output)
        if req.detailed:
            if "with_timestamps" in req.options:
                body["with_timestamps"] = req.options["with_timestamps"]
            raw = client.request_bytes("POST", f"/music/detailed{qs}", body=body, headers=headers)
            meta_json, audio = _parse_multipart(raw)
            if audio is None:
                raise MediaError("ElevenLabs /music/detailed returned no audio part",
                                 category=ErrorCategory.PROVIDER, provider=self.name)
            artifacts = [self._write_audio(audio, out, req.output_format)]
            sidecar = out.with_suffix(out.suffix + ".metadata.json")
            sidecar.write_text(json.dumps(meta_json or {}, ensure_ascii=False, indent=2), encoding="utf-8")
            artifacts.append(Artifact.from_path(sidecar, "metadata", mime="application/json", role="metadata"))
        else:
            audio = client.request_bytes("POST", f"/music{qs}", body=body, headers=headers)
            artifacts = [self._write_audio(audio, out, req.output_format)]
        record_usage({"tool": "music.generate", "operation": "music.generate", "provider": self.name,
                      "model": model, "kind": "audio"})
        return GenerationResult(modality="audio", operation="music.generate", provider=self.name, model=model,
                                artifacts=artifacts, usage={},
                                meta={"prompt": req.prompt, "from_plan": req.composition_plan is not None,
                                      "output_format": req.output_format, "detailed": req.detailed})

    def generate_music_plan(self, req: MusicPlanRequest) -> GenerationResult:
        client, headers = self._prepare()
        model = req.model or self.model_id
        body: dict = {"prompt": req.prompt, "model_id": model}
        if req.duration_ms is not None:
            body["music_length_ms"] = req.duration_ms
        if req.source_plan is not None:
            body["source_composition_plan"] = req.source_plan
        data = client.request_json("POST", "/music/plan", body=body, headers=headers)
        out = Path(req.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        record_usage({"tool": "music.plan", "operation": "music.plan", "provider": self.name,
                      "model": model, "kind": "plan"})
        return GenerationResult(modality="audio", operation="music.plan", provider=self.name, model=model,
                                artifacts=[Artifact.from_path(out, "plan", mime="application/json")],
                                usage={}, meta={"prompt": req.prompt, "free": True})

    # ---- sound effects ---------------------------------------------------
    def generate_sound(self, req: SoundEffectRequest) -> GenerationResult:
        client, headers = self._prepare()
        model = req.model or self.model_id
        body: dict = {"text": req.text, "model_id": model}
        if req.duration_seconds is not None:
            body["duration_seconds"] = req.duration_seconds
        for k in ("loop", "prompt_influence"):
            if k in req.options:
                body[k] = req.options[k]
        qs = self._format_qs(req.output_format)
        out = Path(req.output)
        audio = client.request_bytes("POST", f"/sound-generation{qs}", body=body, headers=headers)
        artifacts = [self._write_audio(audio, out, req.output_format)]
        record_usage({"tool": "sound.generate", "operation": "sound.generate", "provider": self.name,
                      "model": model, "kind": "audio", "characters": len(req.text)})
        return GenerationResult(modality="audio", operation="sound.generate", provider=self.name, model=model,
                                artifacts=artifacts, usage={"characters": len(req.text)},
                                meta={"text": req.text, "output_format": req.output_format})

    # ---- helpers ---------------------------------------------------------
    def _format_qs(self, output_format: str | None) -> str:
        return f"?{urlencode({'output_format': output_format})}" if output_format else ""

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


def _parse_multipart(body: bytes) -> tuple[dict | None, bytes | None]:
    """Split a ``multipart/mixed`` body (used by ``/v1/music/detailed``) into its JSON
    metadata part and its binary audio part. The opening delimiter (``--<boundary>``)
    is the body's first line, so no response header is needed to find the boundary."""
    if not body:
        return None, None
    first = body.split(b"\r\n", 1)[0].split(b"\n", 1)[0]
    if not first.startswith(b"--"):
        return None, body  # not multipart — treat the whole thing as audio
    delim = first  # b"--<boundary>"
    meta: dict | None = None
    audio: bytes | None = None
    for chunk in body.split(delim):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":  # preamble or closing "--"
            continue
        head, sep, content = chunk.partition(b"\r\n\r\n")
        if not sep:
            head, sep, content = chunk.partition(b"\n\n")
        ctype = ""
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-type:"):
                ctype = line.split(b":", 1)[1].strip().decode("ascii", "replace").lower()
        content = content.rstrip(b"\r\n")
        if "application/json" in ctype:
            try:
                meta = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                meta = None
        elif content:
            audio = content
    return meta, audio
