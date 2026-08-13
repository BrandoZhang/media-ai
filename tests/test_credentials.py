"""Security tests: secret handling, reference resolution, redaction, file-perm refusal.

The resolution model these cover is deliberately flat: a binding names exactly one
source and there is no precedence to reason about. So the questions worth asking are
"does each scheme resolve?", "does a failure say which reference failed?", and "does
the plaintext stay out of every sink?" — not "which layer won?".
"""

from __future__ import annotations

import json
import pickle

import pytest

from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.credentials import redaction, stores
from media_ai.credentials.reference import BindingCredentials, is_reference, resolve_reference
from media_ai.credentials.secret import BrokeredHandle, Secret
from media_ai.credentials.stores import named_account, register_secret_backend


def _creds_file(tmp_path, monkeypatch, body: str, mode: int = 0o600):
    path = tmp_path / "credentials.toml"
    path.write_text(body, encoding="utf-8")
    path.chmod(mode)
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(path))
    return path


# --------------------------------------------------------------- the handle


def test_secret_never_reveals_in_repr_str_or_pickle():
    s = Secret("sk-TOPSECRET-abcdef123456", provider="openai", source="env")
    assert "TOPSECRET" not in repr(s)
    assert str(s) == "***"
    assert "TOPSECRET" not in pickle.loads(pickle.dumps(s))
    with pytest.raises(TypeError):
        json.dumps({"key": s})


def test_registered_secret_is_redacted_everywhere():
    Secret("sk-live-registered-value-98765", provider="openai", source="env")
    assert "sk-live-registered-value-98765" not in redaction.redact("using sk-live-registered-value-98765 now")


# ----------------------------------------------------------- the schemes


def test_env_reference_resolves(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-env-value-123456")
    cred = resolve_reference("env://ARK_API_KEY", provider="volc-ark")
    assert isinstance(cred, Secret)
    assert cred.reveal() == "ark-env-value-123456"
    assert cred.source == "env"


def test_env_reference_accepts_the_bare_colon_form(monkeypatch):
    monkeypatch.setenv("BARE_COLON_VAR", "bare-colon-value-123")
    assert resolve_reference("env://BARE_COLON_VAR").reveal() == "bare-colon-value-123"
    assert resolve_reference("env:BARE_COLON_VAR").reveal() == "bare-colon-value-123"


def test_cred_reference_resolves_an_account(tmp_path, monkeypatch):
    _creds_file(tmp_path, monkeypatch, '["volc-ark/seedance-2.0"]\napi_key = "ark-account-999999"\n')
    assert resolve_reference("cred://volc-ark/seedance-2.0").reveal() == "ark-account-999999"


def test_an_account_key_may_itself_be_a_reference(tmp_path, monkeypatch):
    """Which is what lets a machine keep every key in a vault and still name accounts."""
    _creds_file(tmp_path, monkeypatch, '[shared]\napi_key = "env://SHARED_SOURCE"\n')
    monkeypatch.setenv("SHARED_SOURCE", "resolved-nested-123456")
    assert resolve_reference("cred://shared").reveal() == "resolved-nested-123456"


def test_broker_reference_holds_no_key(monkeypatch):
    monkeypatch.setenv("MEDIA_CRED_BROKER", "https://broker.internal")
    monkeypatch.setenv("MEDIA_CRED_BROKER_TOKEN", "session-token-xyz")
    cred = resolve_reference("broker://", provider="openai")
    assert isinstance(cred, BrokeredHandle)
    assert cred.endpoint == "https://broker.internal"
    with pytest.raises(MediaError):
        cred.reveal()  # the process never has one to reveal


def test_a_pluggable_backend_serves_its_scheme(monkeypatch):
    register_secret_backend("vaulttest", lambda ref: "from-the-vault-1234")
    assert resolve_reference("vaulttest://team/key").reveal() == "from-the-vault-1234"


# ------------------------------------------------- failing loudly, and by name


@pytest.mark.parametrize(
    "ref, expected_code, expected_text",
    [
        ("env://DEFINITELY_UNSET_VAR", "credential_unresolved", "DEFINITELY_UNSET_VAR"),
        ("cred://no-such-account", "credential_unresolved", "no-such-account"),
        ("wishful://thinking", "credential_scheme_unknown", "wishful"),
    ],
)
def test_a_reference_that_does_not_resolve_says_which_one(ref, expected_code, expected_text, tmp_path, monkeypatch):
    _creds_file(tmp_path, monkeypatch, '[present]\napi_key = "here-123456"\n')
    with pytest.raises(MediaError) as ei:
        resolve_reference(ref, provider="openai")
    assert ei.value.category is ErrorCategory.AUTH
    assert ei.value.exit_code == 4
    assert ei.value.code == expected_code
    assert expected_text in ei.value.message


def test_nothing_falls_through_to_another_source(tmp_path, monkeypatch):
    """The point of naming one source: a key elsewhere is never quietly substituted.

    Under the old chain this call succeeded — the env var was one of five places
    consulted in order. Now the binding said `cred://`, so an absent account is the
    answer, not a cue to look somewhere else.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used-abcdef")
    _creds_file(tmp_path, monkeypatch, "[unrelated]\napi_key = \"x-123456\"\n")
    with pytest.raises(MediaError) as ei:
        resolve_reference("cred://openai", provider="openai")
    assert "sk-should-not-be-used-abcdef" not in str(ei.value)


def test_a_binding_with_no_credential_says_so():
    with pytest.raises(MediaError) as ei:
        BindingCredentials(None, provider="openai").resolve()
    assert ei.value.code == "credential_missing"
    assert ei.value.category is ErrorCategory.AUTH


# ------------------------------------------------------------ the file itself


def test_group_or_world_readable_credentials_file_is_refused(tmp_path, monkeypatch):
    _creds_file(tmp_path, monkeypatch, '[openai]\napi_key = "sk-in-file-123456"\n', mode=0o644)
    with pytest.raises(MediaError) as ei:
        resolve_reference("cred://openai")
    assert ei.value.category is ErrorCategory.AUTH
    assert "chmod 600" in ei.value.message


def test_a_cred_reference_cycle_is_refused(tmp_path, monkeypatch):
    _creds_file(tmp_path, monkeypatch, '[loop]\napi_key = "cred://loop"\n')
    with pytest.raises(MediaError) as ei:
        resolve_reference("cred://loop")
    assert "circular" in ei.value.message


def test_an_absent_credentials_file_is_not_an_error(tmp_path, monkeypatch):
    """Only asking it for something it does not have is."""
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(tmp_path / "nope.toml"))
    assert named_account("anything") is None


# ----------------------------------------------------------- which layout it is

# `credentials.toml` is the one file here a user cannot reconstruct — a config can be
# re-derived by re-running setup, but the keys in it were pasted from somewhere else
# and may have been issued once. So whatever this layout becomes, an existing file has
# to be convertible rather than replaced, and converting starts with knowing which
# layout you are holding.


def test_a_file_written_before_the_key_existed_still_works(tmp_path, monkeypatch):
    """Absent means 1. Nothing is migrated, nothing is rewritten on read."""
    _creds_file(tmp_path, monkeypatch, '[openai]\napi_key = "sk-in-file-123456"\n')
    assert named_account("openai") == "sk-in-file-123456"


def test_reading_never_writes_the_key_back(tmp_path, monkeypatch):
    """A read that stamped the file would rewrite a secret file as a side effect."""
    path = _creds_file(tmp_path, monkeypatch, '[openai]\napi_key = "sk-in-file-123456"\n')
    before = path.read_bytes(), path.stat().st_mtime_ns
    named_account("openai")
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_a_declared_current_schema_reads_normally(tmp_path, monkeypatch):
    _creds_file(tmp_path, monkeypatch, f'schema = {stores.SCHEMA}\n\n[openai]\napi_key = "sk-in-file-123456"\n')
    assert named_account("openai") == "sk-in-file-123456"


def test_a_file_from_a_newer_build_is_refused_and_says_to_upgrade(tmp_path, monkeypatch):
    """Not "re-run setup" — that is the opposite instruction, and it would overwrite
    a file that is ahead of this CLI, taking the keys with it."""
    _creds_file(tmp_path, monkeypatch, f'schema = {stores.SCHEMA + 1}\n\n[openai]\napi_key = "sk-x-123456"\n')
    with pytest.raises(MediaError) as ei:
        resolve_reference("cred://openai")
    assert ei.value.code == "credentials_from_newer_build"
    assert ei.value.category is ErrorCategory.AUTH
    assert "Upgrade" in ei.value.message


@pytest.mark.parametrize("value", ['"1"', "true", "1.0"])
def test_a_schema_that_is_not_an_integer_says_the_key_is_reserved(tmp_path, monkeypatch, value):
    """`true` is the sharp one: bool is an int subclass and would read as schema 1."""
    _creds_file(tmp_path, monkeypatch, f'schema = {value}\n\n[openai]\napi_key = "sk-x-123456"\n')
    with pytest.raises(MediaError) as ei:
        resolve_reference("cred://openai")
    assert ei.value.code == "credentials_schema_invalid"


def test_an_account_cannot_be_called_schema(tmp_path, monkeypatch):
    """Reserved at the top level, and the error says so rather than reading it as 0."""
    _creds_file(tmp_path, monkeypatch, '[schema]\napi_key = "sk-x-123456"\n')
    with pytest.raises(MediaError) as ei:
        resolve_reference("cred://schema")
    assert "reserved" in ei.value.message


def test_the_secret_in_a_rejected_file_is_not_in_the_message(tmp_path, monkeypatch):
    """The refusal quotes the schema value; it must not carry the file's contents."""
    _creds_file(tmp_path, monkeypatch, f'schema = {stores.SCHEMA + 1}\n\n[openai]\napi_key = "sk-x-123456"\n')
    with pytest.raises(MediaError) as ei:
        resolve_reference("cred://openai")
    assert "sk-x-123456" not in str(ei.value.to_dict())


# ---------------------------------------------------- and what the writer does with it

# `init` merges this run's accounts into whatever is on disk, so it is the other half
# of the layout question: reading has to know which shape it is holding, and writing
# has to not rewrite a shape it does not understand.


def test_the_writer_stamps_the_schema_onto_a_file_that_had_none(tmp_path, monkeypatch):
    from media_ai.cli.init import _merged_credentials

    path = _creds_file(tmp_path, monkeypatch, '[openai]\napi_key = "sk-old-123456"\n')
    merged = _merged_credentials(path, {"volc-ark/seedance-2.0": {"api_key": "sk-new-123456"}})
    assert merged["schema"] == stores.SCHEMA
    assert merged["openai"] == {"api_key": "sk-old-123456"}


def test_the_writer_stamps_this_build_over_what_the_file_claimed(tmp_path, monkeypatch):
    """The file coming out was written by this build, whatever went in said."""
    from media_ai.cli.init import _merged_credentials

    path = _creds_file(tmp_path, monkeypatch, f'schema = {stores.SCHEMA}\n\n[openai]\napi_key = "sk-old-123456"\n')
    assert _merged_credentials(path, {})["schema"] == stores.SCHEMA


def test_the_writer_refuses_to_merge_into_a_newer_file(tmp_path, monkeypatch):
    """Merging into a file it cannot read correctly would rewrite it in the older
    shape and take the keys with it — and these are what cannot be reconstructed."""
    from media_ai.cli.init import _merged_credentials

    body = f'schema = {stores.SCHEMA + 1}\n\n[openai]\napi_key = "sk-old-123456"\n'
    path = _creds_file(tmp_path, monkeypatch, body)
    with pytest.raises(MediaError) as ei:
        _merged_credentials(path, {"new": {"api_key": "sk-new-123456"}})
    assert ei.value.code == "credentials_from_newer_build"
    assert path.read_text(encoding="utf-8") == body


# -------------------------------------------------------- telling the two apart


@pytest.mark.parametrize(
    "value, reference",
    [
        ("env://ARK_API_KEY", True),
        ("cred://volc-ark/seedance-2.0", True),
        ("keychain://media-ai/openai", True),
        ("op://vault/item/field", True),
        ("arn:aws:secretsmanager:us-east-1:1:secret:x", True),
        ("sk-a-real-looking-key-000000", False),
        ("", False),
    ],
)
def test_a_raw_key_is_never_mistaken_for_a_reference(value, reference):
    """What the shareable config file is checked against before anything is written."""
    assert is_reference(value) is reference
