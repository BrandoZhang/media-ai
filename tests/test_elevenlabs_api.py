"""Network-free tests for the ElevenLabs adapter (text-to-speech + dialogue)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.types import DialogueRequest, DialogueTurn, SpeechRequest
from media_ai.credentials.secret import Secret
from media_ai.providers.elevenlabs import ElevenLabsProvider


def test_speech_generate_body_and_path(fake_provider, tmp_path):
    prov, fake = fake_provider(ElevenLabsProvider, [b"ID3-fake-mp3-bytes"])
    req = SpeechRequest(text="Hello there.", output=tmp_path / "o.mp3", model="eleven_multilingual_v2",
                        voice="voiceXYZ", output_format="mp3_44100_128", seed=7,
                        options={"stability": 0.4, "similarity_boost": 0.8, "previous_text": "Prev."})
    res = prov.generate_speech(req)
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"].startswith("/text-to-speech/voiceXYZ")
    assert "output_format=mp3_44100_128" in call["path"]
    assert "with-timestamps" not in call["path"]
    body = call["body"]
    assert body["text"] == "Hello there." and body["model_id"] == "eleven_multilingual_v2"
    assert body["seed"] == 7
    assert body["voice_settings"] == {"stability": 0.4, "similarity_boost": 0.8}
    assert body["previous_text"] == "Prev."
    assert Path(res.primary().path).read_bytes() == b"ID3-fake-mp3-bytes"
    assert res.usage["characters"] == len("Hello there.")
    assert res.modality == "audio" and res.operation == "speech.generate"


def test_speech_defaults_to_provider_voice(fake_provider, tmp_path):
    prov, fake = fake_provider(ElevenLabsProvider, [b"mp3"])
    prov.generate_speech(SpeechRequest(text="hi", output=tmp_path / "o.mp3"))
    assert fake.calls[0]["path"].startswith(f"/text-to-speech/{prov.default_voice}")


def test_speech_timestamps_writes_sidecar(fake_provider, tmp_path):
    align = {"characters": ["H", "i"], "character_start_times_seconds": [0.0, 0.1],
             "character_end_times_seconds": [0.1, 0.2]}
    resp = {"audio_base64": base64.b64encode(b"AUDIO").decode(), "alignment": align, "normalized_alignment": align}
    prov, fake = fake_provider(ElevenLabsProvider, [resp])
    out = tmp_path / "o.mp3"
    res = prov.generate_speech(SpeechRequest(text="Hi", output=out, voice="v", timestamps=True))
    assert fake.calls[0]["path"].startswith("/text-to-speech/v/with-timestamps")
    assert out.read_bytes() == b"AUDIO"
    sidecar = tmp_path / "o.mp3.timestamps.json"
    assert sidecar.is_file()
    saved = json.loads(sidecar.read_text())
    assert saved["alignment"]["characters"] == ["H", "i"]
    # audio + timestamps sidecar
    assert [a.kind for a in res.artifacts] == ["audio", "timestamps"]


def test_dialogue_body_and_path(fake_provider, tmp_path):
    prov, fake = fake_provider(ElevenLabsProvider, [b"dialogue-mp3"])
    req = DialogueRequest(
        turns=[DialogueTurn("Knock knock", "voiceA"), DialogueTurn("Who is there?", "voiceB")],
        output=tmp_path / "d.mp3", model="eleven_v3", output_format="mp3_44100_128",
        options={"stability": 0.6},
    )
    res = prov.generate_dialogue(req)
    call = fake.calls[0]
    assert call["path"].startswith("/text-to-dialogue")
    assert "with-timestamps" not in call["path"]
    body = call["body"]
    assert body["inputs"] == [{"text": "Knock knock", "voice_id": "voiceA"},
                              {"text": "Who is there?", "voice_id": "voiceB"}]
    assert body["model_id"] == "eleven_v3" and body["settings"] == {"stability": 0.6}
    assert Path(res.primary().path).read_bytes() == b"dialogue-mp3"
    assert res.operation == "speech.dialogue"


def test_dialogue_timestamps_includes_voice_segments(fake_provider, tmp_path):
    resp = {"audio_base64": base64.b64encode(b"D").decode(),
            "voice_segments": [{"voice_id": "voiceA", "start_time_seconds": 0.0, "end_time_seconds": 0.5,
                                "character_start_index": 0, "character_end_index": 5, "dialogue_input_index": 0}]}
    prov, fake = fake_provider(ElevenLabsProvider, [resp])
    out = tmp_path / "d.mp3"
    prov.generate_dialogue(DialogueRequest(turns=[DialogueTurn("Hello", "voiceA")], output=out, timestamps=True))
    assert fake.calls[0]["path"].startswith("/text-to-dialogue/with-timestamps")
    saved = json.loads((tmp_path / "d.mp3.timestamps.json").read_text())
    assert saved["voice_segments"][0]["voice_id"] == "voiceA"


def test_auth_header_is_xi_api_key():
    prov = ElevenLabsProvider()
    base, headers = prov._auth(Secret("secret-key-123456", provider="elevenlabs", source="test"))
    assert headers == {"xi-api-key": "secret-key-123456"}
    assert "Authorization" not in headers
    assert base == prov.base_url


def test_error_mapping():
    prov = ElevenLabsProvider()
    assert prov._error(401, "bad key").category == ErrorCategory.AUTH
    assert prov._error(403, "forbidden").category == ErrorCategory.AUTH
    assert prov._error(422, "validation").category == ErrorCategory.VALIDATION
    assert prov._error(429, "rate limited").category == ErrorCategory.RATE_LIMIT
    assert prov._error(500, "boom").category == ErrorCategory.PROVIDER


def test_base_url_configurable_via_config_and_env(monkeypatch):
    prov = ElevenLabsProvider(config={"base_url": "https://api.eu.residency.elevenlabs.io/v1"})
    assert prov.base_url == "https://api.eu.residency.elevenlabs.io/v1"
    monkeypatch.setenv("ELEVENLABS_BASE_URL", "https://api.us.elevenlabs.io/v1")
    assert ElevenLabsProvider().base_url == "https://api.us.elevenlabs.io/v1"


def test_mime_from_output_format(fake_provider, tmp_path):
    # a pcm output_format written to a .bin file -> mime derived from the format, not the ext
    prov, _ = fake_provider(ElevenLabsProvider, [b"pcmbytes"])
    res = prov.generate_speech(SpeechRequest(text="x", output=tmp_path / "o.bin", voice="v", output_format="pcm_16000"))
    assert res.primary().mime == "audio/L16"


def test_dialogue_requires_turns(fake_provider, tmp_path):
    prov, _ = fake_provider(ElevenLabsProvider, [])
    with pytest.raises(MediaError) as ei:
        prov.generate_dialogue(DialogueRequest(turns=[], output=tmp_path / "d.mp3"))
    assert ei.value.category == ErrorCategory.VALIDATION
