"""Scenes — the fine-grained kind of generation a request represents.

This is the *only* taxonomy of "what kind of generation is this". There used to be a
second one — an ``Operation`` naming the command (``video.generate``) — and it could
not carry the distinction that matters: one ``video.generate`` covered text→video,
image→video, first+last frame, multimodal references and extension at once, while
those five differ *per model* (Veo 3.1 Lite takes a first frame but no reference
images and cannot extend). Collapsing them scattered the difference into ``supports_*``
booleans that could be read but not enumerated, so nothing could answer "which of my
bindings can do this?" before spending a call.

A :class:`Scene` is that difference made into a value. Bindings declare the scenes
they serve (see :mod:`media_ai.core.binding`), the CLI derives the scene from the
inputs a caller actually passed, and the two are compared before any network call.

**Scene is the semantic role of the inputs, not their file type.** A video handed
in as *reference material* (Seedance draws on its content or style) and a video
handed in as a *starting point* (Veo continues from its final frame, by URI) are
different scenes even though both are "a video went in" — hence
``reference_to_video`` versus ``extend``, and the two distinct request fields
behind them.

The corollary bounds what belongs here: a capability that does **not** change the
input roles is not a scene. Seedream 5.0 pro's interactive editing — ``<bbox>``
coordinates written into the prompt — takes exactly the inputs of
``image_to_image`` and differs only in how the prompt reads, so it is a
capability flag plus a documented technique, not a scene. Nothing can derive it
from the request without parsing prose, which is the test.
"""

from __future__ import annotations

from enum import Enum

from .types import (
    AnimationRequest,
    DialogueRequest,
    ImageRequest,
    Modality,
    MusicPlanRequest,
    MusicRequest,
    SoundEffectRequest,
    SpeechRequest,
    VideoRequest,
)

__all__ = ["Scene", "derive_scene", "scenes_for_group"]


class Scene(str, Enum):
    """One generation scene, named ``<group>.<what>``.

    The group matches the CLI command group (``media-ai video …`` → ``video.*``),
    so a skill covering a command group covers exactly the scenes under it.
    """

    IMAGE_TEXT_TO_IMAGE = "image.text_to_image"
    IMAGE_IMAGE_TO_IMAGE = "image.image_to_image"

    VIDEO_TEXT_TO_VIDEO = "video.text_to_video"
    VIDEO_IMAGE_TO_VIDEO = "video.image_to_video"
    VIDEO_KEYFRAME_TO_VIDEO = "video.keyframe_to_video"
    VIDEO_REFERENCE_TO_VIDEO = "video.reference_to_video"
    VIDEO_EXTEND = "video.extend"
    VIDEO_CONCAT = "video.concat"

    SPEECH_TEXT_TO_SPEECH = "speech.text_to_speech"
    SPEECH_DIALOGUE = "speech.dialogue"

    MUSIC_TEXT_TO_MUSIC = "music.text_to_music"
    MUSIC_PLAN_TO_MUSIC = "music.plan_to_music"
    MUSIC_PLAN = "music.plan"

    SOUND_TEXT_TO_SOUND = "sound.text_to_sound"

    ANIMATION_FROM_VIDEO = "animation.from_video"
    ANIMATION_FROM_FRAMES = "animation.from_frames"

    @property
    def group(self) -> str:
        """The CLI command group this scene belongs to (``video``, ``speech``, …)."""
        return self.value.split(".", 1)[0]

    @property
    def modality(self) -> Modality:
        return _GROUP_MODALITY[self.group]


#: Three command groups share one modality — speech, music and sound effects are all
#: audio — which is why the group, not the modality, is the unit skills are cut along.
_GROUP_MODALITY: dict[str, Modality] = {
    "image": Modality.IMAGE,
    "video": Modality.VIDEO,
    "speech": Modality.AUDIO,
    "music": Modality.AUDIO,
    "sound": Modality.AUDIO,
    # An animated GIF/WebP/APNG is served as an image — `image/gif`, an `<img>` tag —
    # even though a video went in. The group carries the *output* modality, which is why
    # this is its own group rather than a scene under `video`: there it would have been
    # reported as a video, and the modality field is what a consumer branches on.
    "animation": Modality.IMAGE,
}


def scenes_for_group(group: str) -> frozenset[Scene]:
    """Every scene under one command group. Empty for a group that drives none."""
    return frozenset(s for s in Scene if s.group == group)


def derive_scene(req) -> Scene:
    """The scene a request represents, from the inputs it carries.

    Derived rather than asked for: a caller says what it has (a first frame, a set
    of references, a composition plan), and which scene that is follows. No CLI flag
    selects a scene, so a caller cannot claim one the request does not match.
    """
    if isinstance(req, ImageRequest):
        return Scene.IMAGE_IMAGE_TO_IMAGE if req.references else Scene.IMAGE_TEXT_TO_IMAGE

    if isinstance(req, VideoRequest):
        return _video_scene(req)

    if isinstance(req, DialogueRequest):
        return Scene.SPEECH_DIALOGUE
    if isinstance(req, SpeechRequest):
        return Scene.SPEECH_TEXT_TO_SPEECH

    if isinstance(req, MusicPlanRequest):
        return Scene.MUSIC_PLAN
    if isinstance(req, MusicRequest):
        return Scene.MUSIC_PLAN_TO_MUSIC if req.composition_plan else Scene.MUSIC_TEXT_TO_MUSIC

    if isinstance(req, SoundEffectRequest):
        return Scene.SOUND_TEXT_TO_SOUND

    if isinstance(req, AnimationRequest):
        # One source video, or a set of stills. Those are genuinely different input
        # roles — a frame sequence is where footage that had to be matted frame by frame
        # comes back in — so they are two scenes. **Transparency is not**: it takes the
        # same inputs and changes only the output, which by this module's own rule makes
        # it a request field, not a scene.
        return Scene.ANIMATION_FROM_FRAMES if req.frames else Scene.ANIMATION_FROM_VIDEO

    raise TypeError(f"no scene derivation for {type(req).__name__}")


def _video_scene(req: VideoRequest) -> Scene:
    """Video's five scenes, most specific input first.

    ``continue_from`` outranks everything: continuing a clip is what the request is
    *for*, and a caller that also passed references is describing the continuation,
    not asking for a different scene.
    """
    if req.continue_from is not None:
        return Scene.VIDEO_EXTEND
    if req.reference_images or req.reference_videos or req.reference_audios:
        return Scene.VIDEO_REFERENCE_TO_VIDEO
    if req.first_frame is not None and req.last_frame is not None:
        return Scene.VIDEO_KEYFRAME_TO_VIDEO
    if req.first_frame is not None or req.last_frame is not None:
        # A lone last frame is still animating a still; the adapter decides whether
        # its backend accepts one without a first frame.
        return Scene.VIDEO_IMAGE_TO_VIDEO
    return Scene.VIDEO_TEXT_TO_VIDEO
