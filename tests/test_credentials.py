"""Security tests: secret handling, resolver chain, redaction, and file-perm refusal."""

from __future__ import annotations

import json
import pickle

import pytest
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.credentials import redaction
from media_ai.credentials.resolver import default_chain
from media_ai.credentials.secret import BrokeredHandle, Secret
from media_ai.credentials.stores import env_resolver


def test_secret_never_reveals_in_repr_str_or_pickle():
    s = Secret("sk-TOPSECRET-abcdef123456", provider="openai", source="env:OPENAI_API_KEY")
    assert "TOPSECRET" not in repr(s)
    assert str(s) == "***"
    assert "TOPSECRET" not in pickle.loads(pickle.dumps(s))
    # not JSON-serializable -> can't accidentally end up in result JSON
    with pytest.raises(TypeError):
        json.dumps(s)
    assert s.reveal() == "sk-TOPSECRET-abcdef123456"  # explicit reveal still works


def test_registered_secret_is_redacted_everywhere():
    Secret("MY-LIVE-KEY-9999", provider="volc", source="env")
    assert redaction.redact("authorization: Bearer MY-LIVE-KEY-9999") == "authorization: Bearer ***"
    obj = redaction.redact_obj({"msg": "key=MY-LIVE-KEY-9999", "authorization": "Bearer x"})
    assert "MY-LIVE-KEY-9999" not in json.dumps(obj)
    assert obj["authorization"] == "***"  # sensitive key dropped


def test_redact_masks_key_shapes_even_if_unregistered():
    assert redaction.redact("token sk-abcdef1234567890 here") == "token *** here"
    assert redaction.redact("AIzaSyABCDEFGHIJKLMNOPQRSTUV0123456789") == "***"


def test_chain_env_resolution_and_source(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-value-123456")
    cred = default_chain().resolve("openai")
    assert isinstance(cred, Secret)
    assert cred.source == "env:OPENAI_API_KEY"
    assert cred.reveal() == "sk-env-value-123456"


def test_gemini_two_env_vars(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-fallback-key-1")
    assert env_resolver("gemini").reveal() == "google-fallback-key-1"


def test_missing_credential_raises_auth():
    with pytest.raises(MediaError) as ei:
        default_chain().resolve("openai")
    assert ei.value.category == ErrorCategory.AUTH
    assert ei.value.exit_code == 4


def test_broker_takes_priority_and_holds_no_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used-abcdef")
    monkeypatch.setenv("MEDIA_CRED_BROKER", "https://broker.internal")
    monkeypatch.setenv("MEDIA_CRED_BROKER_TOKEN", "session-token-xyz")
    cred = default_chain().resolve("openai")
    assert isinstance(cred, BrokeredHandle)
    assert cred.endpoint == "https://broker.internal"
    with pytest.raises(MediaError):
        cred.reveal()  # a brokered handle has no local secret


def test_world_readable_config_file_is_refused(tmp_path, monkeypatch):
    cfg = tmp_path / "credentials.toml"
    cfg.write_text('[openai]\napi_key = "sk-in-file-123456"\n')
    cfg.chmod(0o644)  # group/world readable
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(cfg))
    with pytest.raises(MediaError) as ei:
        default_chain().resolve("openai")
    assert ei.value.category == ErrorCategory.AUTH
    assert "chmod 600" in ei.value.message


def test_config_file_600_is_read(tmp_path, monkeypatch):
    cfg = tmp_path / "credentials.toml"
    cfg.write_text('[volc]\napi_key = "ark-file-key-123456"\n')
    cfg.chmod(0o600)
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(cfg))
    cred = default_chain().resolve("volc")
    assert cred.reveal() == "ark-file-key-123456" and cred.source == "config-file"


def test_secret_manager_reference_env_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_SECRET_REF_OPENAI", "env://MY_SECRET_SOURCE")
    monkeypatch.setenv("MY_SECRET_SOURCE", "resolved-from-ref-1234")
    cred = default_chain().resolve("openai")
    assert cred.reveal() == "resolved-from-ref-1234" and cred.source == "secret-manager"
