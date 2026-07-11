"""Offline mock audio tests — no Pillow/ffmpeg needed (stdlib ``wave`` only), so this
runs on a bare box unlike the media-stack-gated CLI tests."""

from __future__ import annotations

import json
import wave

from media_ai.core.capabilities import UnsupportedPolicy, validate_request
from media_ai.core.errors import MediaError
from media_ai.core.types import DialogueRequest, DialogueTurn, Modality, SpeechRequest
from media_ai.providers.mock import MockProvider


def _is_valid_wav(path) -> bool:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() > 0 and w.getnchannels() == 1


def test_mock_speech_generate_writes_wav(tmp_path):
    out = tmp_path / "s.wav"
    res = MockProvider().generate_speech(SpeechRequest(text="Hello there, world.", output=out))
    assert out.is_file() and _is_valid_wav(out)
    assert res.modality == "audio" and res.operation == "speech.generate"
    assert res.primary().kind == "audio" and res.primary().bytes > 0
    assert res.usage["characters"] == len("Hello there, world.")


def test_mock_speech_timestamps_sidecar(tmp_path):
    out = tmp_path / "s.wav"
    res = MockProvider().generate_speech(SpeechRequest(text="Hi", output=out, timestamps=True))
    sidecar = tmp_path / "s.wav.timestamps.json"
    assert sidecar.is_file()
    align = json.loads(sidecar.read_text())["alignment"]
    assert align["characters"] == ["H", "i"]
    assert [a.kind for a in res.artifacts] == ["audio", "timestamps"]


def test_mock_dialogue_writes_wav_and_voice_segments(tmp_path):
    out = tmp_path / "d.wav"
    turns = [DialogueTurn("Knock knock", "mock-voice-a"), DialogueTurn("Who is there?", "mock-voice-b")]
    res = MockProvider().generate_dialogue(DialogueRequest(turns=turns, output=out, timestamps=True))
    assert _is_valid_wav(out)
    assert res.operation == "speech.dialogue" and res.meta["voices"] == ["mock-voice-a", "mock-voice-b"]
    segs = json.loads((tmp_path / "d.wav.timestamps.json").read_text())["voice_segments"]
    assert [s["voice_id"] for s in segs] == ["mock-voice-a", "mock-voice-b"]
    assert segs[0]["dialogue_input_index"] == 0


def test_mock_audio_capabilities_declare_speech(tmp_path):
    caps = MockProvider().capabilities(modality=Modality.AUDIO)
    assert Modality.AUDIO in caps.modalities and caps.audio is not None
    assert caps.audio.supports_dialogue and caps.audio.supports_timestamps
    # a bogus option is rejected pre-flight
    req = SpeechRequest(text="x", output=tmp_path / "o.wav", options={"__nope__": 1})
    try:
        validate_request(req, caps, UnsupportedPolicy.ERROR)
        raised = False
    except MediaError:
        raised = True
    assert raised
