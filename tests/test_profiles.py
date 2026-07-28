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
model      = "ep-example-endpoint"
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
    assert model == "ep-example-endpoint"
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
    assert model == "ep-example-endpoint" and prov.base_url.endswith("example-region.volces.com/api/v3")


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


def test_bare_colon_secret_manager_reference_is_accepted(tmp_path, monkeypatch):
    # A secret-manager reference in bare `scheme:` form (e.g. an AWS ARN) is a valid
    # reference that resolve_reference() understands — it must not be rejected as a raw key.
    from media_ai.credentials.profile import load_profile

    p = tmp_path / "config.toml"
    p.write_text(
        '[profiles.arn]\nprovider = "openai"\n'
        'credential = "arn:aws:secretsmanager:us-east-1:123456789012:secret:openai-key"\n'
    )
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(p))
    prof = load_profile("arn")  # does not raise
    assert prof.credential.startswith("arn:aws:secretsmanager:")


def test_no_profile_is_backward_compatible(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "sk-plain")
    prov, model = registry.build(provider="volc", model="doubao-seedance-2-0-260128", modality=Modality.VIDEO)
    assert prov.credential().reveal() == "sk-plain" and model == "doubao-seedance-2-0-260128"


# --------------------------------------------------------------------------
# named credentials (cred://) + fallback lists
# --------------------------------------------------------------------------

NAMED_CONFIG = """
[profiles.image_a]
provider   = "volc"
model      = "ep-image-A"
credential = "cred://volc_account_a"

[profiles.video_ha]
provider   = "volc"
model      = "ep-video-B"
credential = ["cred://volc_account_b", "cred://volc_shared", "env://ARK_API_KEY"]
"""


@pytest.fixture
def named_setup(tmp_path, monkeypatch):
    """A config.toml with cred:// profiles + a chmod-600 credentials.toml behind them."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(NAMED_CONFIG)
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(cfg))
    creds = tmp_path / "credentials.toml"
    creds.write_text(
        '[volc_account_a]\napi_key = "key-account-a-111111"\n'  # flat account blocks
        '[volc_account_b]\napi_key = "key-account-b-222222"\n'
    )
    creds.chmod(0o600)
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(creds))
    return tmp_path, creds


def test_profile_resolves_named_account(named_setup):
    prov, model = registry.build(profile="image_a", modality=Modality.IMAGE)
    assert isinstance(prov, VolcProvider) and model == "ep-image-A"
    cred = prov.credential()
    assert cred.reveal() == "key-account-a-111111"
    assert cred.source == "profile:image_a"


def test_fallback_list_uses_first_that_resolves(named_setup):
    # volc_account_b is present -> it wins even though later fallbacks also exist.
    prov, _ = registry.build(profile="video_ha", modality=Modality.VIDEO)
    assert prov.credential().reveal() == "key-account-b-222222"


def test_fallback_list_skips_absent_and_uses_next(named_setup, monkeypatch):
    # Drop the preferred named credential; the list should fall through to the next
    # option that resolves (here the shared one, added below).
    tmp_path, creds = named_setup
    creds.write_text(
        '[volc_account_a]\napi_key = "key-account-a-111111"\n'
        '[volc_shared]\napi_key = "key-shared-333333"\n'  # account_b absent now
    )
    creds.chmod(0o600)
    prov, _ = registry.build(profile="video_ha", modality=Modality.VIDEO)
    assert prov.credential().reveal() == "key-shared-333333"  # skipped account_b -> shared


def test_fallback_list_falls_through_to_env(named_setup, monkeypatch):
    tmp_path, creds = named_setup
    creds.write_text('[volc_account_a]\napi_key = "unused-111111"\n')  # b + shared absent
    creds.chmod(0o600)
    monkeypatch.setenv("ARK_API_KEY", "env-last-resort-444444")
    prov, _ = registry.build(profile="video_ha", modality=Modality.VIDEO)
    assert prov.credential().reveal() == "env-last-resort-444444"  # last list entry env://ARK_API_KEY


def test_fallback_list_all_missing_raises_auth(named_setup, monkeypatch):
    tmp_path, creds = named_setup
    creds.write_text('[volc_account_a]\napi_key = "unused-111111"\n')  # nothing the list wants
    creds.chmod(0o600)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    prov, _ = registry.build(profile="video_ha", modality=Modality.VIDEO)
    with pytest.raises(MediaError) as ei:
        prov.credential()
    assert ei.value.category == ErrorCategory.AUTH
    assert "none of the fallback credentials" in ei.value.message


def test_single_named_credential_is_strict_when_absent(named_setup):
    tmp_path, creds = named_setup
    creds.write_text('[other]\napi_key = "x-111111"\n')  # image_a wants volc_account_a
    creds.chmod(0o600)
    prov, _ = registry.build(profile="image_a", modality=Modality.IMAGE)
    with pytest.raises(MediaError) as ei:
        prov.credential()  # a single reference is strict: no silent switch to another key
    assert ei.value.category == ErrorCategory.AUTH


def test_raw_key_anywhere_in_fallback_list_is_rejected(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text('[profiles.bad]\nprovider = "volc"\ncredential = ["cred://ok", "sk-raw-key-here"]\n')
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(p))
    with pytest.raises(MediaError) as ei:
        registry.build(profile="bad", modality=Modality.VIDEO)
    assert ei.value.category == ErrorCategory.AUTH and "reference" in ei.value.message
