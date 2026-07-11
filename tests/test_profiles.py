"""Provider profiles: route calls to different endpoints/projects/tenants, each
with its own credential source and (optionally) base URL."""

from __future__ import annotations

import pytest
from media_ai.core import registry
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.types import Modality
from media_ai.credentials.redaction import redact
from media_ai.providers.volc import VolcProvider

CONFIG = """
[profiles.prod_video]
provider   = "volc"
model      = "ep-20260214051115-zrbtw"
base_url   = "https://ark.example-region.volces.com/api/v3"
credential = "env://ARK_PROD_VIDEO_KEY"

[profiles.prod_image]
provider   = "volc"
model      = "ep-image-999"
credential = "env://ARK_PROD_IMAGE_KEY"

[profiles.no_cred]
provider = "volc"
model    = "ep-inherits-default"
"""


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text(CONFIG)
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(p))
    return p


def test_profile_binds_provider_model_baseurl_and_key(config_file, monkeypatch):
    monkeypatch.setenv("ARK_PROD_VIDEO_KEY", "sk-video-account-B-123")
    prov, model = registry.build(profile="prod_video", modality=Modality.VIDEO)
    assert isinstance(prov, VolcProvider)
    assert model == "ep-20260214051115-zrbtw"
    assert prov.base_url == "https://ark.example-region.volces.com/api/v3"
    cred = prov.credential()
    assert cred.reveal() == "sk-video-account-B-123"
    assert cred.source == "profile:prod_video"


def test_two_profiles_route_to_different_keys(config_file, monkeypatch):
    # the reported scenario: image + video endpoints on different accounts
    monkeypatch.setenv("ARK_PROD_VIDEO_KEY", "key-B-video")
    monkeypatch.setenv("ARK_PROD_IMAGE_KEY", "key-A-image")
    vprov, _ = registry.build(profile="prod_video", modality=Modality.VIDEO)
    iprov, _ = registry.build(profile="prod_image", modality=Modality.IMAGE)
    assert vprov.credential().reveal() == "key-B-video"
    assert iprov.credential().reveal() == "key-A-image"


def test_profile_selected_via_env(config_file, monkeypatch):
    monkeypatch.setenv("ARK_PROD_VIDEO_KEY", "k")
    monkeypatch.setenv("MEDIA_PROFILE", "prod_video")
    prov, model = registry.build(modality=Modality.VIDEO)  # no explicit profile arg
    assert model == "ep-20260214051115-zrbtw" and prov.base_url.endswith("example-region.volces.com/api/v3")


def test_explicit_model_and_provider_override_profile(config_file, monkeypatch):
    monkeypatch.setenv("ARK_PROD_VIDEO_KEY", "k")
    prov, model = registry.build(model="doubao-seedance-2-0-260128", profile="prod_video", modality=Modality.VIDEO)
    assert model == "doubao-seedance-2-0-260128"  # explicit --model wins over profile.model


def test_profile_without_credential_falls_back_to_chain(config_file, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "sk-default-ark")
    prov, model = registry.build(profile="no_cred", modality=Modality.VIDEO)
    assert model == "ep-inherits-default"
    assert prov.credential().reveal() == "sk-default-ark"  # normal chain (ARK_API_KEY)


def test_profile_credential_value_is_redacted(config_file, monkeypatch):
    monkeypatch.setenv("ARK_PROD_VIDEO_KEY", "sk-super-secret-video-XYZ")
    prov, _ = registry.build(profile="prod_video", modality=Modality.VIDEO)
    prov.credential()  # registers the value with the redactor
    assert redact("authorization: Bearer sk-super-secret-video-XYZ") == "authorization: Bearer ***"


def test_missing_profile_raises_cli(config_file):
    with pytest.raises(MediaError) as ei:
        registry.build(profile="does_not_exist", modality=Modality.VIDEO)
    assert ei.value.category == ErrorCategory.CLI


def test_no_config_file_raises_when_profile_requested(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "absent.toml"))
    with pytest.raises(MediaError) as ei:
        registry.build(profile="prod_video", modality=Modality.VIDEO)
    assert ei.value.category == ErrorCategory.CLI


def test_raw_key_in_profile_is_rejected(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text('[profiles.bad]\nprovider = "volc"\ncredential = "sk-raw-key-in-file"\n')
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(p))
    with pytest.raises(MediaError) as ei:
        registry.build(profile="bad", modality=Modality.VIDEO)
    assert ei.value.category == ErrorCategory.AUTH and "reference" in ei.value.message


def test_no_profile_is_backward_compatible(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "sk-plain")
    prov, model = registry.build(provider="volc", model="doubao-seedance-2-0-260128", modality=Modality.VIDEO)
    assert prov.credential().reveal() == "sk-plain" and model == "doubao-seedance-2-0-260128"
