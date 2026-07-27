"""Tests for skill -> provider discovery.

These assert the *shape* of the derivation (union not product, local-only skills
contribute nothing) rather than pinning an exact provider list, so adding a
provider or model does not break them.
"""

from __future__ import annotations

import pytest

from media_ai.cli._discovery import (
    available_skills,
    operations_for_skill,
    provider_matrix,
    providers_for_skills,
)
from media_ai.core import registry
from media_ai.core.types import Operation

# Skills that drive no provider operation: local ffmpeg, offline, or docs.
LOCAL_ONLY = [
    "media-ai-concat",
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
    assert skills == sorted(skills)
    assert len(skills) == len(set(skills))


@pytest.mark.parametrize(
    "skill,expected",
    [
        ("media-ai-image", {Operation.IMAGE_GENERATE, Operation.IMAGE_EDIT}),
        ("media-ai-video", {Operation.VIDEO_GENERATE}),
        ("media-ai-speech", {Operation.SPEECH_GENERATE, Operation.SPEECH_DIALOGUE}),
        ("media-ai-music", {Operation.MUSIC_GENERATE, Operation.MUSIC_PLAN}),
        ("media-ai-sound", {Operation.SOUND_GENERATE}),
    ],
)
def test_operations_derived_from_skill_name(skill, expected):
    assert operations_for_skill(skill) == expected


@pytest.mark.parametrize("skill", LOCAL_ONLY)
def test_local_only_skills_drive_no_operations(skill):
    assert operations_for_skill(skill) == frozenset()


def test_unknown_skill_is_empty_not_an_error():
    assert operations_for_skill("media-ai-nonexistent") == frozenset()


def test_every_packaged_skill_is_classified():
    """No packaged skill should be silently unaccounted for."""
    for skill in available_skills():
        ops = operations_for_skill(skill)
        assert ops or skill in LOCAL_ONLY, f"{skill} maps to no operation and is not known-local"


# ------------------------------------------------------------------ matrix


def test_provider_matrix_excludes_credential_free_providers():
    """mock is a real provider but must never be something we ask for a key for."""
    matrix = provider_matrix()
    for providers in matrix.values():
        assert "mock" not in providers


def test_provider_matrix_only_lists_registered_providers():
    known = set(registry.provider_names())
    for providers in provider_matrix().values():
        assert set(providers) <= known


def test_provider_matrix_covers_the_core_operations():
    matrix = provider_matrix()
    for op in (Operation.IMAGE_GENERATE, Operation.VIDEO_GENERATE, Operation.SPEECH_GENERATE):
        assert matrix.get(op), f"{op.value} has no credentialed provider"


def test_provider_matrix_models_are_non_empty():
    for op, providers in provider_matrix().items():
        for provider, models in providers.items():
            assert models, f"{op.value}/{provider} listed with no models"


def test_provider_matrix_survives_a_broken_provider(monkeypatch):
    """One adapter blowing up must not stop discovery of the others."""
    real = registry.get_provider

    def flaky(name, *a, **kw):
        if name == "openai":
            raise RuntimeError("adapter is broken")
        return real(name, *a, **kw)

    monkeypatch.setattr(registry, "get_provider", flaky)
    matrix = provider_matrix()
    assert matrix, "discovery returned nothing after one provider failed"
    assert all("openai" not in provs for provs in matrix.values())


# ------------------------------------------------------------------- union


def test_providers_for_skills_is_a_union_not_a_product():
    """The property the wizard's size depends on."""
    image = set(providers_for_skills(["media-ai-image"]))
    video = set(providers_for_skills(["media-ai-video"]))
    speech = set(providers_for_skills(["media-ai-speech"]))
    combined = set(providers_for_skills(["media-ai-image", "media-ai-video", "media-ai-speech"]))
    assert combined == image | video | speech


def test_selecting_every_skill_stays_within_the_provider_ceiling():
    """However many skills are picked, the ask is bounded by the provider count."""
    everything = providers_for_skills(available_skills())
    credentialed = {
        name
        for name in registry.provider_names()
        if getattr(registry.get_provider(name), "requires_credentials", True)
    }
    assert set(everything) <= credentialed
    assert len(everything) <= len(credentialed)


@pytest.mark.parametrize("skill", LOCAL_ONLY)
def test_local_only_skills_need_no_providers(skill):
    assert providers_for_skills([skill]) == {}


def test_local_only_skills_do_not_widen_the_ask():
    image_only = providers_for_skills(["media-ai-image"])
    with_locals = providers_for_skills(["media-ai-image", *LOCAL_ONLY])
    assert with_locals == image_only


def test_providers_map_back_to_the_skills_they_serve():
    served = providers_for_skills(["media-ai-image", "media-ai-video"])
    for provider, skills in served.items():
        assert skills, f"{provider} listed with no skills"
        assert set(skills) <= {"media-ai-image", "media-ai-video"}
        assert skills == sorted(skills)


def test_empty_selection_asks_for_nothing():
    assert providers_for_skills([]) == {}


def test_result_ordering_is_stable():
    a = providers_for_skills(["media-ai-image", "media-ai-video"])
    b = providers_for_skills(["media-ai-video", "media-ai-image"])
    assert a == b
    assert list(a) == sorted(a)
