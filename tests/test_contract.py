"""One suite every shipped binding must satisfy.

Parametrized over the manifests rather than over adapters, because the manifest is
what discovery prints and what validation enforces. A binding that declares something
incoherent — a scene with no way to express it, an option nothing accepts — fails here
rather than at a billed call.

The complement lives in ``test_manifests.py``: that file checks the declaration is
*well-formed*, this one checks it is *usable*.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import CATALOG, adapter_for

from media_ai.core.errors import MediaError
from media_ai.core.scene import Scene
from media_ai.core.types import (
    AnimationRequest,
    DialogueRequest,
    DialogueTurn,
    ImageRequest,
    MusicPlanRequest,
    MusicRequest,
    SoundEffectRequest,
    SpeechRequest,
    VideoRequest,
)
from media_ai.core.validate import UnsupportedPolicy, validate_request

BINDING_IDS = [b.id for b in CATALOG.all()]


def _minimal(scene: Scene, tmp_path: Path, voice: str = "v1"):
    """The plainest request that expresses a scene — no optional knobs at all.

    Anything a binding declares must accept one of these; if it does not, the
    constraints contradict the scene list.
    """
    out = tmp_path / "out.bin"
    if scene is Scene.IMAGE_TEXT_TO_IMAGE:
        return ImageRequest(prompt="a fox", output=out)
    if scene is Scene.IMAGE_IMAGE_TO_IMAGE:
        return ImageRequest(prompt="a fox", output=out, references=[_ref(tmp_path, "ref.png")])
    if scene in {Scene.VIDEO_TEXT_TO_VIDEO, Scene.VIDEO_CONCAT}:
        return VideoRequest(prompt="a wave", output=out)
    if scene is Scene.VIDEO_IMAGE_TO_VIDEO:
        return VideoRequest(prompt="a wave", output=out, first_frame=_ref(tmp_path, "a.png"))
    if scene is Scene.VIDEO_KEYFRAME_TO_VIDEO:
        return VideoRequest(prompt="a wave", output=out,
                            first_frame=_ref(tmp_path, "a.png"), last_frame=_ref(tmp_path, "b.png"))
    if scene is Scene.VIDEO_REFERENCE_TO_VIDEO:
        return VideoRequest(prompt="a wave", output=out, reference_images=[_ref(tmp_path, "r.png")])
    if scene is Scene.VIDEO_EXTEND:
        return VideoRequest(prompt="a wave", output=out, continue_from=_ref(tmp_path, "prior.mp4"))
    if scene is Scene.SPEECH_TEXT_TO_SPEECH:
        return SpeechRequest(text="hello there", output=out)
    if scene is Scene.SPEECH_DIALOGUE:
        # A binding with a fixed voice list only accepts one of its own, so the
        # minimal request has to ask the declaration what to use.
        return DialogueRequest(turns=[DialogueTurn("A", "hi")], cast={"A": voice}, output=out)
    if scene is Scene.MUSIC_TEXT_TO_MUSIC:
        return MusicRequest(output=out, prompt="lofi")
    if scene is Scene.MUSIC_PLAN_TO_MUSIC:
        return MusicRequest(output=out, composition_plan={"sections": []})
    if scene is Scene.MUSIC_PLAN:
        return MusicPlanRequest(prompt="lofi", output=out)
    if scene is Scene.SOUND_TEXT_TO_SOUND:
        return SoundEffectRequest(text="a whoosh", output=out)
    if scene is Scene.ANIMATION_FROM_VIDEO:
        return AnimationRequest(output=out, source=_ref(tmp_path, "clip.mp4"))
    if scene is Scene.ANIMATION_FROM_FRAMES:
        return AnimationRequest(output=out, frames=[_ref(tmp_path, "f01.png")])
    raise AssertionError(f"no minimal request for {scene}")


def _ref(tmp_path: Path, name: str):
    from media_ai.core.types import MediaRef

    path = tmp_path / name
    path.write_bytes(b"\0" * 16)
    return MediaRef(str(path))


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_every_declared_scene_accepts_a_minimal_request(binding_id, tmp_path):
    b = CATALOG.get(binding_id)
    voices = b.constraints.audio.voices
    for scene in sorted(b.scenes, key=lambda s: s.value):
        req = _minimal(scene, tmp_path, voice=voices[0] if voices else "v1")
        validate_request(req, b.constraints, binding=binding_id, scene=scene)


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_an_undeclared_option_is_rejected(binding_id, tmp_path):
    """Provider knobs are opt-in per binding, so an unknown one is a typo — and a typo
    passed through would send a parameter the API may silently ignore."""
    b = CATALOG.get(binding_id)
    scene = sorted(b.scenes, key=lambda s: s.value)[0]
    req = _minimal(scene, tmp_path)
    req.options = {"__definitely_not_a_real_option__": 1}
    with pytest.raises(MediaError) as ei:
        validate_request(req, b.constraints, binding=binding_id, scene=scene)
    assert ei.value.exit_code == 3


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_the_policy_can_downgrade_any_refusal(binding_id, tmp_path):
    b = CATALOG.get(binding_id)
    scene = sorted(b.scenes, key=lambda s: s.value)[0]
    req = _minimal(scene, tmp_path)
    req.options = {"__definitely_not_a_real_option__": 1}
    assert validate_request(req, b.constraints, UnsupportedPolicy.WARN)
    assert validate_request(req, b.constraints, UnsupportedPolicy.IGNORE) == []


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_the_adapter_constructs_from_its_binding(binding_id):
    """Construction takes the binding and nothing else — no environment, no defaults."""
    adapter = adapter_for(binding_id)
    assert adapter.name == CATALOG.get(binding_id).provider
    assert adapter.model_id == CATALOG.get(binding_id).model_id
    assert CATALOG.get(binding_id).scenes <= adapter.supported_scenes()


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_declared_video_bindings_are_asynchronous(binding_id):
    """Every backend that *generates* video does it as a job: submit, poll, finalize."""
    b = CATALOG.get(binding_id)
    generates_video = b.scenes & (
        {s for s in Scene if s.group == "video"} - {Scene.VIDEO_CONCAT}
    )
    if generates_video:
        assert b.constraints.video.is_async, f"{binding_id} generates video but declares itself synchronous"


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_a_usage_line_always_names_its_binding(binding_id, tmp_path, monkeypatch):
    """The ledger's whole job is answering "what did this cost, and through what?".

    A line naming only a provider cannot be attributed: one provider serves several
    bindings at different prices. Filling the id in `Adapter.record` rather than at
    each call site is what makes that unmissable, and this asserts no adapter can
    route around it.
    """
    import json

    from media_ai.core.scene import Scene as _Scene

    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("MEDIA_AI_USAGE_LOG", str(log))
    adapter = adapter_for(binding_id)
    adapter.record(_Scene.IMAGE_TEXT_TO_IMAGE, kind="image", total_tokens=1)
    adapter.record(None, kind="video")  # the job-finalize path, which cannot know one

    named, unnamed = [json.loads(x) for x in log.read_text().splitlines()]
    assert named["binding"] == binding_id and named["scene"] == "image.text_to_image"
    assert named["provider"] == CATALOG.get(binding_id).provider
    assert named["model"] == CATALOG.get(binding_id).model_id
    # Absent, not guessed: a wrong scene in a cost report is worse than a missing one.
    assert unnamed["binding"] == binding_id and "scene" not in unnamed


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_the_wrong_form_of_geometry_is_refused_for_every_binding(binding_id, tmp_path):
    """Pixel size against a ratio-only binding (or the reverse) must never reach the wire.

    This escaped once, and only on the video path: the video branch of `_check_geometry`
    returned before the mode comparison, so `--size 1280x720` on a ratio-only video
    binding validated clean, submitted, and came back as a **billed job at the
    provider's default geometry** — different output than was asked for, reported as
    success. Parametrized over every binding so the next one cannot regress on a path
    the others cover.
    """
    from media_ai.core.geometry import GeometrySpec

    b = CATALOG.get(binding_id)
    mode = b.constraints.geometry.mode
    if mode == "both":
        pytest.skip(f"{binding_id} accepts either form")

    # A binding declaring no configurable geometry must refuse both forms, not just one.
    wrong = (GeometrySpec(width=1280, height=720) if mode in {"aspect_ratio", "none"}
             else GeometrySpec(aspect_ratio="16:9", resolution="720p"))
    for scene in sorted(b.scenes, key=lambda s: s.value):
        if scene is Scene.VIDEO_CONCAT:
            continue
        req = _minimal(scene, tmp_path, voice=(b.constraints.audio.voices or ["v1"])[0])
        if not hasattr(req, "geometry"):
            continue
        req.geometry = wrong
        with pytest.raises(MediaError) as ei:
            validate_request(req, b.constraints, binding=binding_id, scene=scene)
        assert ei.value.exit_code == 3, f"{binding_id}/{scene.value} accepted the wrong geometry form"
