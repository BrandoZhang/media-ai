"""Scene derivation: the inputs a caller passed decide which scene a request is.

No flag selects a scene, so these tests are the specification of the mapping — and
in particular of the two places it is not obvious: a video handed in as reference
material versus one handed in to continue from, and the precedence between them.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from media_ai.core.scene import Scene, derive_scene, scenes_for_group
from media_ai.core.types import (
    DialogueRequest,
    ImageRequest,
    MediaRef,
    MusicPlanRequest,
    MusicRequest,
    SoundEffectRequest,
    SpeechRequest,
    VideoRequest,
)

OUT = Path("/tmp/out.bin")
REF = MediaRef("a.png")
CLIP = MediaRef("prior.mp4")


def _video(**kw) -> VideoRequest:
    return VideoRequest(prompt="p", output=OUT, **kw)


def test_image_scene_follows_references():
    assert derive_scene(ImageRequest(prompt="p", output=OUT)) is Scene.IMAGE_TEXT_TO_IMAGE
    assert derive_scene(ImageRequest(prompt="p", output=OUT, references=[REF])) is Scene.IMAGE_IMAGE_TO_IMAGE


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({}, Scene.VIDEO_TEXT_TO_VIDEO),
        ({"first_frame": REF}, Scene.VIDEO_IMAGE_TO_VIDEO),
        ({"last_frame": REF}, Scene.VIDEO_IMAGE_TO_VIDEO),
        ({"first_frame": REF, "last_frame": MediaRef("b.png")}, Scene.VIDEO_KEYFRAME_TO_VIDEO),
        ({"reference_images": [REF]}, Scene.VIDEO_REFERENCE_TO_VIDEO),
        ({"reference_videos": [CLIP]}, Scene.VIDEO_REFERENCE_TO_VIDEO),
        ({"reference_audios": [MediaRef("a.wav")]}, Scene.VIDEO_REFERENCE_TO_VIDEO),
        ({"continue_from": CLIP}, Scene.VIDEO_EXTEND),
    ],
)
def test_video_scene_follows_inputs(kwargs, expected):
    assert derive_scene(_video(**kwargs)) is expected


def test_a_video_is_reference_material_or_a_starting_point_and_the_field_says_which():
    """Same file type, different role — which is exactly why they are separate fields.

    `--reference-video` used to mean both, disambiguated only by which provider you
    happened to pick: material on Ark, continue-from on Veo. Deriving the scene from
    the flag is what removes the ambiguity.
    """
    assert derive_scene(_video(reference_videos=[CLIP])) is Scene.VIDEO_REFERENCE_TO_VIDEO
    assert derive_scene(_video(continue_from=CLIP)) is Scene.VIDEO_EXTEND


def test_continue_from_outranks_every_other_video_input():
    """Continuing a clip is what the request is for; anything else describes the
    continuation rather than asking for a different scene."""
    req = _video(continue_from=CLIP, first_frame=REF, last_frame=REF, reference_images=[REF])
    assert derive_scene(req) is Scene.VIDEO_EXTEND


def test_audio_scenes():
    assert derive_scene(SpeechRequest(text="hi", output=OUT)) is Scene.SPEECH_TEXT_TO_SPEECH
    assert derive_scene(DialogueRequest(turns=[], output=OUT)) is Scene.SPEECH_DIALOGUE
    assert derive_scene(SoundEffectRequest(text="whoosh", output=OUT)) is Scene.SOUND_TEXT_TO_SOUND


def test_music_scene_distinguishes_prompt_from_plan():
    assert derive_scene(MusicRequest(output=OUT, prompt="jazz")) is Scene.MUSIC_TEXT_TO_MUSIC
    assert derive_scene(MusicRequest(output=OUT, composition_plan={"sections": []})) is Scene.MUSIC_PLAN_TO_MUSIC
    assert derive_scene(MusicPlanRequest(prompt="jazz", output=OUT)) is Scene.MUSIC_PLAN


def test_scene_groups_match_cli_command_groups():
    """A skill covers a command group, so the groups are what skills are cut along.

    Read out of the dispatcher rather than restated here: a hardcoded list only asserts
    that someone updated two places, while this asserts the thing that has to be true —
    every scene is reachable from a command, and every generation command drives scenes.
    Adding ``animation.*`` without registering ``media-ai animation`` fails here, which
    is the mistake worth catching, because a scene no command can express is one a
    binding can declare and nothing can call.
    """
    from media_ai.__main__ import _GROUPS

    groups = {s.group for s in Scene}
    assert groups <= set(_GROUPS), f"scene group(s) with no CLI command: {sorted(groups - set(_GROUPS))}"
    for group in groups:
        assert group in _GROUPS
        importlib.import_module(f"media_ai.cli.{_GROUPS[group]}")
    assert Scene.VIDEO_CONCAT in scenes_for_group("video")
    assert scenes_for_group("nothing") == frozenset()


def test_every_group_carries_the_modality_of_what_it_produces():
    """``animation`` is the case that makes this worth asserting: a video goes in and an
    *image* comes out, and ``modality`` is the field a consumer branches on. Grouping it
    under ``video`` — the tempting reading, since the input is a clip — would have
    reported an animated GIF as a video."""
    from media_ai.core.types import Modality

    assert Scene.ANIMATION_FROM_VIDEO.modality is Modality.IMAGE
    assert Scene.ANIMATION_FROM_FRAMES.modality is Modality.IMAGE
    assert Scene.VIDEO_CONCAT.modality is Modality.VIDEO


def test_unknown_request_type_is_an_error_not_a_guess():
    with pytest.raises(TypeError):
        derive_scene(object())


# --------------------------------------------------------------- placeholders


def test_only_the_offline_binding_declares_itself_a_placeholder():
    """`placeholder` removes a binding from every recommendation, so a real backend
    carrying it by mistake would silently stop being offered anywhere."""
    from conftest import CATALOG

    assert {b.id for b in CATALOG.all() if b.placeholder} == {"mock/mock"}


def test_a_placeholder_is_never_recommended_but_is_still_reported():
    """The two halves have to differ: an agent needs to know mock exists, and must not
    be told to send work to it."""
    from conftest import CATALOG
    from media_ai.core.resolve import _recommendable

    class _Fake:
        def __init__(self, spec):
            self.spec, self.id = spec, spec.id

    bindings = [_Fake(CATALOG.get("mock/mock")), _Fake(CATALOG.get("openai/gpt-image-2"))]
    assert _recommendable(bindings) == ["openai/gpt-image-2"]
    assert _recommendable([_Fake(CATALOG.get("mock/mock"))]) == []
