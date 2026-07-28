"""Tests for skill -> binding discovery.

These assert the *shape* of the derivation (a skill's group decides its scenes, and
scenes decide which bindings are worth offering) rather than pinning an exact list, so
adding a binding does not break them.
"""

from __future__ import annotations

import pytest

from media_ai.cli._discovery import (
    TIERS,
    available_skills,
    bindings_for_skills,
    core_skills,
    resolve_selection,
    scenes_for_skill,
    selectable_skills,
    skill_info,
)
from conftest import CATALOG
from media_ai.core.scene import Scene

# Skills that drive no credentialed generation: local ffmpeg, offline, or docs.
LOCAL_ONLY = [
        "media-ai-capabilities",
    "media-ai-usage",
    "media-ai-shared",
    "media-ai-job",
]


def test_available_skills_finds_packaged_skills():
    skills = available_skills()
    assert "media-ai-image" in skills
    assert "media-ai-shared" in skills
    assert all(s.startswith("media-ai-") for s in skills)


def test_available_skills_is_sorted_and_unique():
    skills = available_skills()
    assert list(skills) == sorted(skills)
    assert len(skills) == len(set(skills))


@pytest.mark.parametrize(
    "skill,expected",
    [
        ("media-ai-image", {Scene.IMAGE_TEXT_TO_IMAGE, Scene.IMAGE_IMAGE_TO_IMAGE}),
        ("media-ai-video", {Scene.VIDEO_TEXT_TO_VIDEO, Scene.VIDEO_IMAGE_TO_VIDEO,
                            Scene.VIDEO_KEYFRAME_TO_VIDEO, Scene.VIDEO_REFERENCE_TO_VIDEO,
                            Scene.VIDEO_EXTEND, Scene.VIDEO_CONCAT}),
        ("media-ai-speech", {Scene.SPEECH_TEXT_TO_SPEECH, Scene.SPEECH_DIALOGUE}),
        ("media-ai-music", {Scene.MUSIC_TEXT_TO_MUSIC, Scene.MUSIC_PLAN_TO_MUSIC, Scene.MUSIC_PLAN}),
        ("media-ai-sound", {Scene.SOUND_TEXT_TO_SOUND}),
    ],
)
def test_scenes_derived_from_skill_name(skill, expected):
    assert scenes_for_skill(skill) == expected


@pytest.mark.parametrize("skill", [s for s in LOCAL_ONLY if s != "media-ai-usage"])
def test_skills_that_drive_no_generation_have_no_scenes(skill):
    assert scenes_for_skill(skill) == frozenset()


def test_unknown_skill_is_empty_not_an_error():
    assert scenes_for_skill("media-ai-nonexistent") == frozenset()


def test_every_packaged_skill_is_classified():
    """No packaged skill should be silently unaccounted for."""
    for skill in available_skills():
        assert scenes_for_skill(skill) or skill in LOCAL_ONLY, f"{skill} maps to no scene and is not known-local"


# ------------------------------------------------------------- self-description


class TestSkillMetadata:
    """Every skill has to describe itself, because the installer shows a *menu*.

    A list of ``media-ai-*`` directory names is not a choice anyone can make: nothing
    on it says what ``media-ai-sound`` does or which of them are assumed by the rest.
    """

    def test_every_skill_declares_a_summary(self):
        for skill in available_skills():
            summary = skill_info(skill).summary
            assert summary, f"{skill} would be offered with no description"
            assert len(summary) > 40, f"{skill}: {summary!r} is too terse to choose from"

    def test_every_skill_declares_a_known_tier(self):
        for skill in available_skills():
            assert skill_info(skill).tier in TIERS

    def test_summary_is_not_the_agent_facing_description(self):
        """The `description` is keyword bait for skill matching; a human needs prose."""
        for skill in available_skills():
            assert "Use when asked" not in skill_info(skill).summary

    def test_declared_dependencies_exist(self):
        known = set(available_skills())
        for skill in available_skills():
            assert set(skill_info(skill).needs) <= known, f"{skill} needs a skill that does not ship"

    def test_unknown_skill_degrades_instead_of_raising(self):
        info = skill_info("media-ai-does-not-exist")
        assert info.tier == "optional" and info.needs == () and info.summary == ""


class TestInstallTiers:
    """What the wizard asks about, and what it just does."""

    def test_the_shared_contract_and_offline_helpers_are_never_asked_about(self):
        assert set(core_skills()) == {"media-ai-shared", "media-ai-capabilities", "media-ai-usage"}

    def test_core_skills_are_not_offered_as_a_choice(self):
        assert not set(core_skills()) & set(selectable_skills())

    def test_the_job_skill_is_a_dependency_not_a_choice(self):
        """Async is half of what video generation *is*, not a separate preference."""
        assert skill_info("media-ai-job").tier == "dependency"
        assert "media-ai-job" not in selectable_skills()

    def test_video_pulls_in_the_job_skill(self):
        skills, reasons = resolve_selection(["media-ai-video"])
        assert "media-ai-job" in skills
        assert reasons["media-ai-job"] == "needed by media-ai-video"

    def test_picking_nothing_still_installs_the_core(self):
        skills, reasons = resolve_selection([])
        assert set(skills) == set(core_skills())
        assert all(why == "always installed" for why in reasons.values())

    def test_every_addition_is_explained(self):
        """Extra directories may be written; doing it silently may not."""
        picked = ["media-ai-image", "media-ai-video"]
        skills, reasons = resolve_selection(picked)
        assert set(reasons) == set(skills) - set(picked)

    def test_an_explicit_pick_is_never_reported_as_automatic(self):
        _skills, reasons = resolve_selection(["media-ai-image"])
        assert "media-ai-image" not in reasons

    def test_resolution_is_stable_and_sorted(self):
        a, _ = resolve_selection(["media-ai-video", "media-ai-image"])
        b, _ = resolve_selection(["media-ai-image", "media-ai-video"])
        assert a == b == sorted(a)

    def test_only_ever_resolves_to_skills_that_ship(self):
        skills, _ = resolve_selection([*selectable_skills(), "media-ai-not-a-skill"])
        assert set(skills) - {"media-ai-not-a-skill"} <= set(available_skills())

    def test_selecting_everything_offered_covers_every_packaged_skill(self):
        """Nothing should be unreachable: a skill that ships but can never be chosen
        is one nobody will ever have."""
        skills, _ = resolve_selection(selectable_skills())
        assert set(skills) == set(available_skills())


# ------------------------------------------------------------------ matrix


def test_bindings_for_skills_offers_only_what_needs_a_key():
    """A local backend has nothing to configure, so it is never in the credential ask.

    This is the derivation that replaced a declared `kind` on each skill: whether a
    binding belongs in this menu falls out of `auth.kind`, and cannot drift from it.
    """
    offered = bindings_for_skills(available_skills())
    assert offered
    assert "local/ffmpeg" not in offered
    assert "mock/mock" not in offered


def test_bindings_for_skills_is_a_union_not_a_product():
    """The property the wizard's size depends on."""
    image = set(bindings_for_skills(["media-ai-image"]))
    video = set(bindings_for_skills(["media-ai-video"]))
    speech = set(bindings_for_skills(["media-ai-speech"]))
    combined = set(bindings_for_skills(["media-ai-image", "media-ai-video", "media-ai-speech"]))
    assert combined == image | video | speech


def test_every_offered_binding_declares_a_scene_the_skill_drives():
    for skill in ("media-ai-image", "media-ai-video", "media-ai-speech"):
        wanted = scenes_for_skill(skill)
        for bid in bindings_for_skills([skill]):
            assert CATALOG.get(bid).scenes & wanted, f"{bid} offered for {skill} but serves none of its scenes"


@pytest.mark.parametrize("skill", LOCAL_ONLY)
def test_local_only_skills_need_no_bindings(skill):
    assert bindings_for_skills([skill]) == {}


def test_local_only_skills_do_not_widen_the_ask():
    image_only = bindings_for_skills(["media-ai-image"])
    with_locals = bindings_for_skills(["media-ai-image", *LOCAL_ONLY])
    assert with_locals == image_only


def test_bindings_map_back_to_the_skills_they_serve():
    served = bindings_for_skills(["media-ai-image", "media-ai-video"])
    for bid, skills in served.items():
        assert skills, f"{bid} listed with no skills"
        assert set(skills) <= {"media-ai-image", "media-ai-video"}
        assert skills == sorted(skills)


def test_empty_selection_asks_for_nothing():
    assert bindings_for_skills([]) == {}


def test_result_ordering_is_stable():
    a = bindings_for_skills(["media-ai-image", "media-ai-video"])
    b = bindings_for_skills(["media-ai-video", "media-ai-image"])
    assert a == b
    assert list(a) == sorted(a)
