"""``[providers.<name>]`` in config.toml — per-modality/per-operation default models.

A profile pins a single ``model``, which cannot say "images use one model and video
another" on the same provider. Those defaults genuinely differ: Gemini's image, Veo,
and TTS families are disjoint, and ElevenLabs splits further by *operation*. These
tests pin the resolution order that makes that expressible.
"""

from __future__ import annotations

import pytest

from media_ai.core import registry
from media_ai.core.types import Modality
from media_ai.credentials.profile import provider_defaults


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Point $MEDIA_CONFIG_FILE at a writable file and return a writer for it."""
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(path))

    def write(text: str):
        path.write_text(text, encoding="utf-8")
        return path

    return write


# ----------------------------------------------------------------- reading


def test_missing_file_yields_no_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "absent.toml"))
    assert provider_defaults("gemini") == {}


def test_reads_the_named_provider_table(config_file):
    config_file('[providers.gemini]\nimage_model = "gemini-3-pro-image"\n')
    assert provider_defaults("gemini") == {"image_model": "gemini-3-pro-image"}


def test_provider_name_is_case_insensitive(config_file):
    config_file('[providers.gemini]\nimage_model = "x"\n')
    assert provider_defaults("GEMINI") == {"image_model": "x"}


def test_other_providers_are_not_leaked(config_file):
    config_file('[providers.gemini]\nimage_model = "g"\n\n[providers.openai]\nimage_model = "o"\n')
    assert provider_defaults("openai") == {"image_model": "o"}


def test_absent_table_is_empty(config_file):
    config_file('[providers.gemini]\nimage_model = "g"\n')
    assert provider_defaults("volc") == {}


def test_profiles_only_config_still_works(config_file):
    """The pre-existing file shape must keep parsing."""
    config_file('[profiles.p]\nprovider = "openai"\n')
    assert provider_defaults("openai") == {}


def test_malformed_config_raises_a_clear_error(config_file):
    from media_ai.core.errors import MediaError

    config_file("[providers.gemini\nbroken")
    with pytest.raises(MediaError, match="could not parse"):
        provider_defaults("gemini")


# ------------------------------------------------------------- end to end


def test_config_sets_the_per_modality_defaults(config_file, monkeypatch):
    monkeypatch.delenv("GEMINI_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_VIDEO_MODEL", raising=False)
    config_file(
        '[providers.gemini]\n'
        'image_model = "gemini-3-pro-image"\n'
        'video_model = "veo-3.1-fast-generate-preview"\n'
    )
    prov = registry.get_provider("gemini")
    assert prov.default_model(Modality.IMAGE) == "gemini-3-pro-image"
    assert prov.default_model(Modality.VIDEO) == "veo-3.1-fast-generate-preview"


def test_image_and_video_defaults_are_independent(config_file, monkeypatch):
    """The thing a single-model profile cannot express."""
    monkeypatch.delenv("GEMINI_VIDEO_MODEL", raising=False)
    config_file('[providers.gemini]\nimage_model = "gemini-3-pro-image"\n')
    prov = registry.get_provider("gemini")
    assert prov.default_model(Modality.IMAGE) == "gemini-3-pro-image"
    # Untouched by the image override.
    assert prov.default_model(Modality.VIDEO) == "veo-3.1-generate-preview"


def test_config_beats_environment(config_file, monkeypatch):
    monkeypatch.setenv("GEMINI_IMAGE_MODEL", "from-env")
    config_file('[providers.gemini]\nimage_model = "from-config"\n')
    assert registry.get_provider("gemini").default_model(Modality.IMAGE) == "from-config"


def test_environment_still_applies_without_config(config_file, monkeypatch):
    monkeypatch.setenv("GEMINI_IMAGE_MODEL", "from-env")
    config_file("")
    assert registry.get_provider("gemini").default_model(Modality.IMAGE) == "from-env"


def test_builtin_default_when_neither_is_set(config_file, monkeypatch):
    monkeypatch.delenv("GEMINI_IMAGE_MODEL", raising=False)
    config_file("")
    assert registry.get_provider("gemini").default_model(Modality.IMAGE) == "gemini-3.1-flash-image"


def test_explicit_config_argument_beats_the_file(config_file):
    config_file('[providers.gemini]\nimage_model = "from-file"\n')
    prov = registry.get_provider("gemini", config={"image_model": "explicit"})
    assert prov.default_model(Modality.IMAGE) == "explicit"


def test_profile_base_url_still_wins_over_provider_table(config_file):
    config_file(
        '[providers.volc]\nbase_url = "https://from-table"\n\n'
        '[profiles.p]\nprovider = "volc"\nbase_url = "https://from-profile"\n'
    )
    assert registry.get_provider(profile="p").base_url == "https://from-profile"


def test_provider_table_supplies_base_url_without_a_profile(config_file):
    config_file('[providers.volc]\nbase_url = "https://from-table"\n')
    assert registry.get_provider("volc").base_url == "https://from-table"


@pytest.mark.parametrize(
    "provider,key,attr",
    [
        ("openai", "image_model", "image_model"),
        ("volc", "image_model", "image_model"),
        ("volc", "video_model", "video_model"),
        ("elevenlabs", "model", "model"),
        ("elevenlabs", "dialogue_model", "dialogue_model"),
        ("elevenlabs", "music_model", "music_model"),
        ("elevenlabs", "sound_model", "sound_model"),
    ],
)
def test_every_adapter_knob_is_reachable_from_config(config_file, provider, key, attr):
    config_file(f'[providers.{provider}]\n{key} = "configured-value"\n')
    assert getattr(registry.get_provider(provider), attr) == "configured-value"


def test_unknown_keys_are_ignored_not_fatal(config_file):
    """A newer config must stay readable by an older CLI."""
    config_file('[providers.openai]\nimage_model = "gpt-image-1.5"\nfuture_knob = "whatever"\n')
    assert registry.get_provider("openai").image_model == "gpt-image-1.5"


# ------------------------------------------------- elevenlabs sound discovery


def test_sound_model_is_discoverable():
    """sound.generate runs on its own model; capabilities must name it."""
    caps = registry.get_provider("elevenlabs").capabilities()
    assert caps.audio.sound_models == ("eleven_text_to_sound_v2",)


def test_sound_models_reach_the_json_contract():
    """to_dict keeps tuples (as it does for music_models); JSON is where it becomes a list."""
    import json

    caps = json.loads(json.dumps(registry.get_provider("elevenlabs").capabilities().to_dict()))
    assert caps["audio"]["sound_models"] == ["eleven_text_to_sound_v2"]


def test_music_models_were_already_reported():
    caps = registry.get_provider("elevenlabs").capabilities()
    assert caps.audio.music_models == ("music_v1", "music_v2")
