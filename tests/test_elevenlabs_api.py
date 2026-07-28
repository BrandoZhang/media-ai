"""Network-free tests for the ElevenLabs adapter (text-to-speech + dialogue + music + sfx)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from media_ai.cli import speech as speech_cli
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.types import (
    DialogueRequest,
    DialogueTurn,
    MusicPlanRequest,
    MusicRequest,
    SoundEffectRequest,
    SpeechRequest,
)
from media_ai.credentials.secret import Secret
from conftest import adapter_for
from media_ai.providers.elevenlabs import _parse_multipart


def test_speech_generate_body_and_path(fake_provider, tmp_path):
    prov, fake = fake_provider("elevenlabs/eleven-multilingual-v2", [b"ID3-fake-mp3-bytes"])
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
    assert res.modality == "audio"


def test_speech_defaults_to_provider_voice(fake_provider, tmp_path):
    prov, fake = fake_provider("elevenlabs/eleven-multilingual-v2", [b"mp3"])
    prov.generate_speech(SpeechRequest(text="hi", output=tmp_path / "o.mp3"))
    assert fake.calls[0]["path"].startswith(f"/text-to-speech/{prov.default_voice}")


def test_speech_timestamps_writes_sidecar(fake_provider, tmp_path):
    align = {"characters": ["H", "i"], "character_start_times_seconds": [0.0, 0.1],
             "character_end_times_seconds": [0.1, 0.2]}
    resp = {"audio_base64": base64.b64encode(b"AUDIO").decode(), "alignment": align, "normalized_alignment": align}
    prov, fake = fake_provider("elevenlabs/eleven-multilingual-v2", [resp])
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
    prov, fake = fake_provider("elevenlabs/eleven-v3", [b"dialogue-mp3"])
    req = DialogueRequest(
        turns=[DialogueTurn("Joe", "Knock knock"), DialogueTurn("Jane", "Who is there?")],
        cast={"Joe": "voiceA", "Jane": "voiceB"},
        output=tmp_path / "d.mp3", model="eleven_v3", output_format="mp3_44100_128",
        options={"stability": 0.6},
    )
    res = prov.generate_dialogue(req)
    call = fake.calls[0]
    assert call["path"].startswith("/text-to-dialogue")
    assert "with-timestamps" not in call["path"]
    body = call["body"]
    # voice_id is looked up from the cast by each turn's speaker
    assert body["inputs"] == [{"text": "Knock knock", "voice_id": "voiceA"},
                              {"text": "Who is there?", "voice_id": "voiceB"}]
    assert body["model_id"] == "eleven_v3" and body["settings"] == {"stability": 0.6}
    assert Path(res.primary().path).read_bytes() == b"dialogue-mp3"


def test_dialogue_and_speech_default_to_different_bindings(configured, tmp_path):
    """Speech and dialogue are separate scenes, so they default separately.

    This used to need a hack: `speech dialogue` deliberately withheld the resolved
    model so the adapter could substitute its own dialogue default, because one
    provider-wide default could not be both `eleven_multilingual_v2` and `eleven_v3`
    — and sending the former to /text-to-dialogue is rejected outright. Scene-keyed
    defaults make the two independent, and the hack is gone.
    """
    configured(
        {"elevenlabs/eleven-multilingual-v2": "env://ELEVENLABS_API_KEY",
         "elevenlabs/eleven-v3": "env://ELEVENLABS_API_KEY"},
        defaults={"speech.text_to_speech": "elevenlabs/eleven-multilingual-v2",
                  "speech.dialogue": "elevenlabs/eleven-v3"},
    )
    parse = speech_cli._build_parser().parse_args

    spoken = parse(["generate", "--text", "hi", "--output", str(tmp_path / "s.mp3")])
    dialogue = parse(["dialogue", "--speaker", "A=voiceA", "--turn", "A", "hello",
                      "--output", str(tmp_path / "d.mp3")])

    from media_ai.cli import common

    assert common.bind(spoken, _req(spoken))[1].id == "elevenlabs/eleven-multilingual-v2"
    assert common.bind(dialogue, _req(dialogue))[1].id == "elevenlabs/eleven-v3"


def _req(args):
    """The request the CLI would build, without making the call."""
    if args.op == "dialogue":
        cast, turns, instruction = speech_cli._parse_dialogue(args)
        return DialogueRequest(turns=turns, cast=cast, instruction=instruction, output=Path(args.output))
    return SpeechRequest(text=args.text, output=Path(args.output))


def test_dialogue_unknown_speaker_rejected(fake_provider, tmp_path):
    prov, _ = fake_provider("elevenlabs/eleven-v3", [b"x"])
    req = DialogueRequest(turns=[DialogueTurn("Ghost", "boo")], cast={"Joe": "voiceA"}, output=tmp_path / "d.mp3")
    with pytest.raises(MediaError) as ei:
        prov.generate_dialogue(req)
    assert ei.value.category == ErrorCategory.VALIDATION


def test_dialogue_timestamps_includes_voice_segments(fake_provider, tmp_path):
    resp = {"audio_base64": base64.b64encode(b"D").decode(),
            "voice_segments": [{"voice_id": "voiceA", "start_time_seconds": 0.0, "end_time_seconds": 0.5,
                                "character_start_index": 0, "character_end_index": 5, "dialogue_input_index": 0}]}
    prov, fake = fake_provider("elevenlabs/eleven-v3", [resp])
    out = tmp_path / "d.mp3"
    prov.generate_dialogue(DialogueRequest(turns=[DialogueTurn("Joe", "Hello")], cast={"Joe": "voiceA"},
                                           output=out, timestamps=True))
    assert fake.calls[0]["path"].startswith("/text-to-dialogue/with-timestamps")
    saved = json.loads((tmp_path / "d.mp3.timestamps.json").read_text())
    assert saved["voice_segments"][0]["voice_id"] == "voiceA"


def test_auth_header_is_xi_api_key():
    prov = adapter_for("elevenlabs/eleven-multilingual-v2")
    base, headers = prov._auth(Secret("secret-key-123456", provider="elevenlabs", source="test"))
    assert headers == {"xi-api-key": "secret-key-123456"}
    assert "Authorization" not in headers
    assert base == prov.base_url


def test_error_mapping():
    prov = adapter_for("elevenlabs/eleven-multilingual-v2")
    assert prov._error(401, "bad key").category == ErrorCategory.AUTH
    assert prov._error(403, "forbidden").category == ErrorCategory.AUTH
    assert prov._error(422, "validation").category == ErrorCategory.VALIDATION
    assert prov._error(429, "rate limited").category == ErrorCategory.RATE_LIMIT
    assert prov._error(500, "boom").category == ErrorCategory.PROVIDER


def test_payment_required_is_an_actionable_non_retryable_entitlement_error():
    prov = adapter_for("elevenlabs/music-v2")
    err = prov._error(
        402,
        '{"detail":{"type":"payment_required","code":"paid_plan_required",'
        '"message":"Music API is not available for free users.","status":"limited_access"}}',
    )
    assert err.category == ErrorCategory.AUTH
    assert err.code == "paid_plan_required"
    assert err.retryable is False
    assert err.details["status"] == 402
    assert err.details["provider_status"] == "limited_access"
    assert "upgrade" in (err.hint or "")


def test_a_residency_endpoint_is_set_per_binding_not_per_process(monkeypatch):
    """Two regions are two bindings, so the endpoint belongs to the binding.

    It used to come from `$ELEVENLABS_BASE_URL`, which made it global: a process
    could not route EU traffic to one region and US traffic to another, and nothing
    in the config said which one a given call used.
    """
    eu = adapter_for("elevenlabs/eleven-multilingual-v2",
                     base_url="https://api.eu.residency.elevenlabs.io/v1")
    assert eu.base_url == "https://api.eu.residency.elevenlabs.io/v1"

    monkeypatch.setenv("ELEVENLABS_BASE_URL", "https://api.us.elevenlabs.io/v1")
    default = adapter_for("elevenlabs/eleven-multilingual-v2")
    assert default.base_url == "https://api.elevenlabs.io/v1", "the environment must not reach in"


def test_mime_from_output_format(fake_provider, tmp_path):
    # a pcm output_format written to a .bin file -> mime derived from the format, not the ext
    prov, _ = fake_provider("elevenlabs/eleven-multilingual-v2", [b"pcmbytes"])
    res = prov.generate_speech(SpeechRequest(text="x", output=tmp_path / "o.bin", voice="v", output_format="pcm_16000"))
    assert res.primary().mime == "audio/L16"


def test_dialogue_requires_turns(fake_provider, tmp_path):
    prov, _ = fake_provider("elevenlabs/eleven-v3", [])
    with pytest.raises(MediaError) as ei:
        prov.generate_dialogue(DialogueRequest(turns=[], output=tmp_path / "d.mp3"))
    assert ei.value.category == ErrorCategory.VALIDATION


# ---- music -----------------------------------------------------------------

def test_music_generate_from_prompt(fake_provider, tmp_path):
    prov, fake = fake_provider("elevenlabs/music-v2", [b"ID3-music"])
    req = MusicRequest(output=tmp_path / "song.mp3", prompt="lofi hip hop beat",
                       duration_ms=8000, output_format="mp3_44100_128",
                       options={"force_instrumental": True})
    res = prov.generate_music(req)
    call = fake.calls[0]
    assert call["path"].startswith("/music") and "detailed" not in call["path"]
    assert "output_format=mp3_44100_128" in call["path"]
    body = call["body"]
    assert body == {"model_id": "music_v2", "prompt": "lofi hip hop beat",
                    "music_length_ms": 8000, "force_instrumental": True}
    assert Path(res.primary().path).read_bytes() == b"ID3-music"


def test_music_generate_from_plan(fake_provider, tmp_path):
    prov, fake = fake_provider("elevenlabs/music-v2", [b"mp3"])
    plan = {"positive_global_styles": ["pop"], "negative_global_styles": [], "sections": []}
    prov.generate_music(MusicRequest(output=tmp_path / "s.mp3", composition_plan=plan, seed=7))
    body = fake.calls[0]["body"]
    assert body["composition_plan"] == plan and body["seed"] == 7
    assert "prompt" not in body


def test_music_requires_exactly_one_source(fake_provider, tmp_path):
    prov, _ = fake_provider("elevenlabs/music-v2", [b"x"])
    with pytest.raises(MediaError) as ei:  # neither prompt nor plan
        prov.generate_music(MusicRequest(output=tmp_path / "s.mp3"))
    assert ei.value.category == ErrorCategory.VALIDATION


def test_music_detailed_parses_multipart_sidecar(fake_provider, tmp_path):
    meta = {"composition_plan": {"positive_global_styles": ["pop"]}, "song_metadata": {"title": "T"}}
    boundary = b"abc123"
    body = (b"--" + boundary + b"\r\nContent-Type: application/json\r\n\r\n"
            + json.dumps(meta).encode() + b"\r\n"
            + b"--" + boundary + b"\r\nContent-Type: audio/mpeg\r\n\r\n"
            + b"ID3-detailed-audio\r\n"
            + b"--" + boundary + b"--\r\n")
    prov, fake = fake_provider("elevenlabs/music-v2", [body])
    out = tmp_path / "song.mp3"
    res = prov.generate_music(MusicRequest(output=out, prompt="epic", detailed=True))
    assert fake.calls[0]["path"].startswith("/music/detailed")
    assert out.read_bytes() == b"ID3-detailed-audio"
    saved = json.loads((tmp_path / "song.mp3.metadata.json").read_text())
    assert saved["song_metadata"]["title"] == "T"
    assert [a.kind for a in res.artifacts] == ["audio", "metadata"]


def test_parse_multipart_helper():
    b = (b"--B\r\nContent-Type: application/json\r\n\r\n{\"a\": 1}\r\n"
         b"--B\r\nContent-Type: audio/mpeg\r\n\r\nRAWAUDIO\r\n--B--\r\n")
    meta, audio = _parse_multipart(b)
    assert meta == {"a": 1} and audio == b"RAWAUDIO"
    # a non-multipart body is treated as raw audio
    assert _parse_multipart(b"justbytes") == (None, b"justbytes")


def test_music_plan_is_json(fake_provider, tmp_path):
    plan = {"positive_global_styles": ["pop"], "negative_global_styles": [], "sections": []}
    prov, fake = fake_provider("elevenlabs/music-v2", [plan])
    out = tmp_path / "plan.json"
    res = prov.generate_music_plan(MusicPlanRequest(prompt="jazzy", output=out, duration_ms=12000))
    call = fake.calls[0]
    assert call["path"] == "/music/plan"
    assert call["body"] == {"prompt": "jazzy", "model_id": "music_v2", "music_length_ms": 12000}
    assert json.loads(out.read_text())["positive_global_styles"] == ["pop"]
    assert res.primary().kind == "plan" and res.primary().mime == "application/json"


# ---- sound effects ---------------------------------------------------------

def test_sound_generate_body_and_path(fake_provider, tmp_path):
    prov, fake = fake_provider("elevenlabs/sound-v2", [b"ID3-sfx"])
    req = SoundEffectRequest(text="a spooky whoosh", output=tmp_path / "sfx.mp3",
                             duration_seconds=3.0, output_format="mp3_44100_128",
                             options={"loop": True, "prompt_influence": 0.5})
    res = prov.generate_sound(req)
    call = fake.calls[0]
    assert call["path"].startswith("/sound-generation")
    body = call["body"]
    assert body == {"text": "a spooky whoosh", "model_id": "eleven_text_to_sound_v2",
                    "duration_seconds": 3.0, "loop": True, "prompt_influence": 0.5}
    assert Path(res.primary().path).read_bytes() == b"ID3-sfx"
    assert res.usage["characters"] == len("a spooky whoosh")
