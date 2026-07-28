"""Offline mock audio tests — no Pillow/ffmpeg needed (stdlib ``wave`` only), so this
runs on a bare box unlike the media-stack-gated CLI tests."""

from __future__ import annotations

import json
import wave

from media_ai.core.errors import MediaError
from media_ai.core.types import (
    DialogueRequest,
    DialogueTurn,
    MusicPlanRequest,
    MusicRequest,
    SoundEffectRequest,
    SpeechRequest,
)
from conftest import adapter_for


def _is_valid_wav(path) -> bool:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() > 0 and w.getnchannels() == 1


def test_mock_speech_generate_writes_wav(tmp_path):
    out = tmp_path / "s.wav"
    res = adapter_for("mock/mock").generate_speech(SpeechRequest(text="Hello there, world.", output=out))
    assert out.is_file() and _is_valid_wav(out)
    assert res.modality == "audio" and res.operation == "speech.generate"
    assert res.primary().kind == "audio" and res.primary().bytes > 0
    assert res.usage["characters"] == len("Hello there, world.")


def test_mock_speech_timestamps_sidecar(tmp_path):
    out = tmp_path / "s.wav"
    res = adapter_for("mock/mock").generate_speech(SpeechRequest(text="Hi", output=out, timestamps=True))
    sidecar = tmp_path / "s.wav.timestamps.json"
    assert sidecar.is_file()
    align = json.loads(sidecar.read_text())["alignment"]
    assert align["characters"] == ["H", "i"]
    assert [a.kind for a in res.artifacts] == ["audio", "timestamps"]


def test_mock_dialogue_writes_wav_and_voice_segments(tmp_path):
    out = tmp_path / "d.wav"
    turns = [DialogueTurn("Joe", "Knock knock"), DialogueTurn("Jane", "Who is there?")]
    cast = {"Joe": "mock-voice-a", "Jane": "mock-voice-b"}
    res = adapter_for("mock/mock").generate_dialogue(
        DialogueRequest(turns=turns, cast=cast, instruction="a cheerful skit", output=out, timestamps=True))
    assert _is_valid_wav(out)
    assert res.operation == "speech.dialogue" and res.meta["voices"] == ["mock-voice-a", "mock-voice-b"]
    assert res.meta["instruction"] == "a cheerful skit"
    segs = json.loads((tmp_path / "d.wav.timestamps.json").read_text())["voice_segments"]
    assert [s["voice_id"] for s in segs] == ["mock-voice-a", "mock-voice-b"]
    assert segs[0]["dialogue_input_index"] == 0


def test_mock_music_from_prompt(tmp_path):
    out = tmp_path / "song.wav"
    res = adapter_for("mock/mock").generate_music(MusicRequest(output=out, prompt="lofi beat", duration_ms=6000))
    assert _is_valid_wav(out) and res.operation == "music.generate"
    assert res.meta["from_plan"] is False


def test_mock_music_detailed_writes_metadata(tmp_path):
    out = tmp_path / "song.wav"
    res = adapter_for("mock/mock").generate_music(MusicRequest(output=out, prompt="epic", detailed=True))
    assert [a.kind for a in res.artifacts] == ["audio", "metadata"]
    meta = json.loads((tmp_path / "song.wav.metadata.json").read_text())
    assert "positive_global_styles" in meta and meta["sections"]


def test_mock_music_requires_one_source(tmp_path):
    try:
        adapter_for("mock/mock").generate_music(MusicRequest(output=tmp_path / "s.wav"))  # neither
        raised = False
    except MediaError:
        raised = True
    assert raised


def test_mock_music_plan_and_reuse(tmp_path):
    plan_out = tmp_path / "plan.json"
    adapter_for("mock/mock").generate_music_plan(MusicPlanRequest(prompt="jazzy", output=plan_out, duration_ms=9000))
    plan = json.loads(plan_out.read_text())
    assert plan["sections"][0]["duration_ms"] == 9000
    # feed the plan back into generate_music
    song = tmp_path / "song.wav"
    adapter_for("mock/mock").generate_music(MusicRequest(output=song, composition_plan=plan))
    assert _is_valid_wav(song)


def test_mock_sound_effect(tmp_path):
    out = tmp_path / "sfx.wav"
    res = adapter_for("mock/mock").generate_sound(SoundEffectRequest(text="whoosh", output=out, duration_seconds=1.5))
    assert _is_valid_wav(out) and res.operation == "sound.generate"
    assert res.usage["characters"] == len("whoosh")


def test_the_mock_binding_declares_what_it_implements():
    """Discovery reads the manifest, so the manifest is what has to be true.

    An adapter quietly supporting more than it declared is the same drift as one
    declaring more than it supports — the validator refuses the request either way.
    """
    from conftest import CATALOG
    from media_ai.core.scene import Scene

    spec = CATALOG.get("mock/mock")
    assert {Scene.SPEECH_TEXT_TO_SPEECH, Scene.SPEECH_DIALOGUE} <= spec.scenes
    assert spec.constraints.supports_flag("dialogue")
    assert spec.constraints.audio.formats
