"""Writing the two config files from outside the wizard.

An organisation configuring a machine from its own service writes exactly what
``init`` writes — the same bindings, the same accounts — after an authentication step
that has nothing to do with this project. Only the last part is ours, and it is the
part with rules that are invisible until they are broken:

- the credentials schema is checked **before** a merge and stamped **after** it;
- the file lands 0600 inside a 0700 directory, or the resolver refuses to read it;
- a re-run producing identical content must not leave a second backup — a copy of
  every key, under a name nobody remembers to delete;
- and a *loose* mode has to stay repairable by rewriting, which is the only remedy a
  user is ever told about.

A provisioner that reimplemented the merge would have to rediscover all four. So the
tests here are about the writers being reachable and correct from outside
``media_ai.cli``, not about any particular deployment.
"""

from __future__ import annotations

import stat
import tomllib

import pytest

from media_ai.core.config import Config, UserBinding, load_config, save_config
from media_ai.core.errors import MediaError
from media_ai.credentials import stores


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_AI_CONFIG_FILE", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MEDIA_AI_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
    return tmp_path


def creds(home) -> dict:
    return tomllib.loads((home / "credentials.toml").read_text(encoding="utf-8"))


def provision(home, *, accounts, bindings, replace=False):
    """What a provisioner does once its own auth and fetch are done."""
    stores.save_accounts(accounts, replace=replace)
    save_config(Config(
        bindings={bid: UserBinding(id=bid, credential=f"cred://{bid}") for bid in bindings},
        defaults={"image.text_to_image": bindings[0]},
        exists=True,
    ))


# ------------------------------------------------------------- both files land


def test_a_provisioned_machine_is_usable(home):
    """The whole point: after this, nothing else in the CLI knows it was provisioned."""
    provision(home, accounts={"mock/mock": "sk-provisioned-123456"}, bindings=["mock/mock"])

    config = load_config()
    assert config.bindings["mock/mock"].credential == "cred://mock/mock"
    assert config.defaults == {"image.text_to_image": "mock/mock"}
    assert stores.named_account("mock/mock") == "sk-provisioned-123456"


def test_the_credentials_file_lands_readable_only_by_its_owner(home):
    """0600 is not advisory — a looser mode makes every key in it unreadable."""
    stores.save_accounts({"acme": "sk-123456"})
    path = home / "credentials.toml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_plain_string_is_accepted_as_the_key(home):
    """`{"acme": "sk-…"}` is what a provisioner has; the wizard's dict form also works."""
    stores.save_accounts({"a": "sk-plain-123456", "b": {"api_key": "sk-dict-123456"}})
    assert creds(home)["a"] == {"api_key": "sk-plain-123456"}
    assert creds(home)["b"] == {"api_key": "sk-dict-123456"}


# ----------------------------------------------------------- merge vs replace


def test_merging_keeps_accounts_it_was_not_told_about(home):
    stores.save_accounts({"personal": "sk-mine-123456"})
    stores.save_accounts({"acme": "sk-theirs-123456"})
    assert set(creds(home)) == {"schema", "personal", "acme"}


def test_replacing_removes_what_it_no_longer_grants(home):
    """The reason `replace` exists. An entitlement being withdrawn has to *remove* the
    account it granted; a merge can only ever add or overwrite, so a revoked key would
    sit on disk working until it was rotated somewhere else.
    """
    stores.save_accounts({"acme-a": "sk-a-123456", "acme-b": "sk-b-123456"})
    stores.save_accounts({"acme-a": "sk-a-123456"}, replace=True)
    assert set(creds(home)) == {"schema", "acme-a"}


def test_replacing_is_not_the_default(home):
    """For the wizard and for `bindings add`, replacing would silently discard keys
    nobody asked about."""
    stores.save_accounts({"kept": "sk-kept-123456"})
    stores.save_accounts({"added": "sk-added-123456"})
    assert "kept" in creds(home)


# --------------------------------------------------------------- the backups


def test_the_first_write_leaves_no_backup(home):
    stores.save_accounts({"acme": "sk-123456"})
    assert list(home.glob("credentials.toml.bak*")) == []


def test_an_identical_rerun_leaves_no_second_backup(home):
    """Provisioning runs on every login. An unconditional backup would accumulate one
    copy of every key per run, under names nobody will remember to delete.
    """
    stores.save_accounts({"acme": "sk-123456"})
    for _ in range(3):
        assert stores.save_accounts({"acme": "sk-123456"}) is None
    assert list(home.glob("credentials.toml.bak*")) == []


def test_a_changed_write_is_backed_up(home):
    stores.save_accounts({"acme": "sk-old-123456"})
    saved = stores.save_accounts({"acme": "sk-new-123456"})
    assert saved is not None
    assert "sk-old-123456" in saved.read_text(encoding="utf-8")
    # The backup holds every key in the file, so it must never be created world-readable.
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


def test_an_identical_rerun_still_repairs_a_loose_mode(home):
    """Rewriting is the only remedy a user is ever told about for a loose mode, so the
    write cannot be skipped just because the content matches — and reading the old file
    cannot go through the gate that refuses it, or setup would stop being able to fix
    exactly the file it exists to fix.
    """
    path = home / "credentials.toml"
    stores.save_accounts({"acme": "sk-123456"})
    path.chmod(0o644)
    stores.save_accounts({"acme": "sk-123456"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ------------------------------------------------------------ what it refuses


def test_it_refuses_to_merge_into_a_file_from_a_newer_build(home):
    """Rewriting it in the older shape would take the keys with it."""
    path = home / "credentials.toml"
    body = f'schema = {stores.SCHEMA + 1}\n\n[acme]\napi_key = "sk-old-123456"\n'
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(MediaError) as exc:
        stores.save_accounts({"new": "sk-new-123456"})
    assert exc.value.code == "credentials_from_newer_build"
    assert path.read_text(encoding="utf-8") == body


def test_it_refuses_an_unparseable_file_rather_than_overwriting_it(home):
    """It may be hand-written and worth keeping."""
    path = home / "credentials.toml"
    path.write_text("[broken\nnot toml", encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(MediaError) as exc:
        stores.save_accounts({"new": "sk-new-123456"})
    assert exc.value.code == "credentials_unreadable"


@pytest.mark.parametrize("accounts", [
    {"schema": "sk-123456"},
    {"acme": ""},
    {"acme": {"api_key": "   "}},
    {"acme": {}},
    {"": "sk-123456"},
])
def test_a_malformed_account_is_refused_before_anything_is_written(home, accounts):
    """An empty key is the interesting one: `named_account` reads a blank value as "no
    such account", so the file would claim to hold something the resolver then denies —
    and the error would name the binding rather than the write that caused it.
    """
    with pytest.raises(MediaError):
        stores.save_accounts(accounts)
    assert not (home / "credentials.toml").exists()


# ---------------------------------------------------- reachable from outside


def test_both_writers_are_exported(home):
    """A provisioner importing a private name is a provisioner that breaks on a refactor."""
    from media_ai.core import config as config_mod

    assert "save_config" in config_mod.__all__
    for name in ("save_accounts", "render_accounts", "credentials_header", "credentials_path"):
        assert name in stores.__all__, name


def test_nothing_in_the_write_path_imports_the_cli(home):
    """The provisioner is not a CLI command, and `media_ai.cli` pulls in argparse, the
    prompt driver and the whole wizard. Importing the writers must not drag that in.
    """
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "from media_ai.core.config import save_config\n"
        "from media_ai.credentials.stores import save_accounts\n"
        "print(any(m.startswith('media_ai.cli') for m in sys.modules))\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "False"


# ---------------------------------------------------- and what must never land


def test_a_raw_key_is_refused_before_the_config_file_is_written(home):
    """The reader has always refused one. Refusing on *read* finds it after it has been
    written to a 0644 file and possibly committed — a report of something that already
    happened. This fires while the value is still in memory, which is the only moment
    at which refusing keeps it secret.

    The caller most likely to get this wrong is a provisioner holding a real key it has
    just fetched, deciding which field it goes in.
    """
    with pytest.raises(MediaError) as exc:
        save_config(Config(bindings={
            "acme/fast": UserBinding(id="acme/fast", credential="sk-live-SECRET-abcdef"),
        }, exists=True))
    assert exc.value.code == "credential_is_raw_key"
    assert not (home / "config.toml").exists()
    assert "sk-live-SECRET-abcdef" not in str(exc.value)


def test_the_refusal_does_not_damage_an_existing_config(home):
    """`render_config` runs before the backup, so a refused write leaves no `.bak`
    either — nothing at all happened."""
    save_config(Config(bindings={"mock/mock": UserBinding(id="mock/mock")}, exists=True))
    before = (home / "config.toml").read_text(encoding="utf-8")
    with pytest.raises(MediaError):
        save_config(Config(bindings={"a/b": UserBinding(id="a/b", credential="sk-raw-123456")}, exists=True))
    assert (home / "config.toml").read_text(encoding="utf-8") == before
    assert list(home.glob("config.toml.bak*")) == []


@pytest.mark.parametrize("credential", [
    "env://ACME_KEY", "cred://acme/fast", "keychain://media-ai/acme", "broker://gw.internal",
    "op://vault/item/field", None,
])
def test_every_reference_form_still_writes(home, credential):
    """The check must not be tighter than the resolver, or it refuses working configs."""
    save_config(Config(bindings={"a/b": UserBinding(id="a/b", credential=credential)}, exists=True))
    assert load_config().bindings["a/b"].credential == credential


# --------------------------------------------- and what the backup must never be


def test_the_backup_of_a_loose_file_is_not_left_loose(home):
    """The fix must not leave a copy of what it fixed.

    A group/world-readable `credentials.toml` is unusable — the resolver refuses it —
    and rewriting is the documented remedy. The rewrite backs the old file up first,
    and a backup that inherited the source's mode would leave every key in a 0644 file
    beside the 0600 one the repair just produced: permanently, silently, and *because*
    the user did the right thing.
    """
    path = home / "credentials.toml"
    stores.save_accounts({"gw": "sk-old-123456"})
    path.chmod(0o644)

    saved = stores.save_accounts({"gw": "sk-new-123456"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert saved is not None and "sk-old-123456" in saved.read_text(encoding="utf-8")
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


def test_a_tighter_mode_is_kept_rather_than_widened(home):
    """The ceiling is a ceiling, not an assignment: 0400 stays 0400."""
    path = home / "credentials.toml"
    stores.save_accounts({"gw": "sk-old-123456"})
    path.chmod(0o400)

    saved = stores.save_accounts({"gw": "sk-new-123456"})
    assert stat.S_IMODE(saved.stat().st_mode) == 0o400


def test_the_config_backup_keeps_the_public_mode(home):
    """`config.toml` is the shareable one; nothing here should tighten it by accident."""
    from media_ai.core.config import Config, UserBinding, save_config

    save_config(Config(bindings={"a/b": UserBinding(id="a/b")}, exists=True))
    saved = save_config(Config(bindings={"c/d": UserBinding(id="c/d")}, exists=True))
    assert saved is not None
    assert stat.S_IMODE(saved.stat().st_mode) == 0o644


# ------------------------------------------------- writing part of a document

# `save_config` takes a *whole* config, so a caller holding only the bindings it
# provisions has to load and merge first. Doing it the obvious way instead — build a
# fresh `Config`, hand it over — deletes every table nobody thought about, silently, on
# a command about something else. That is the same defect `bindings add` once had one
# level down, and `save_bindings` is the fix made the default rather than a thing to
# remember.

PROVISIONED = """\
schema = 2

[bindings."personal/one"]
credential = "env://MY_KEY"

[defaults]
"video.text_to_video" = "personal/one"

[update]
check = false
feed = "https://internal.example/feed.json"

[telemetry]
enabled = true

[acme]
role = "team-vision"
"""


@pytest.fixture
def furnished(home):
    (home / "config.toml").write_text(PROVISIONED, encoding="utf-8")
    return home / "config.toml"


def written(path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def org_binding():
    return {"acme/fast": UserBinding(id="acme/fast", credential="cred://acme/fast")}


def test_provisioning_keeps_the_tables_it_was_not_asked_about(furnished):
    """The measured failure: an internal feed URL, the telemetry settings and a fork's
    own records, all gone because the caller named bindings."""
    from media_ai.core.config import save_bindings

    save_bindings(org_binding(), defaults={"image.text_to_image": "acme/fast"}, replace=True)

    after = written(furnished)
    assert after["update"] == {"check": False, "feed": "https://internal.example/feed.json"}
    assert after["telemetry"] == {"enabled": True}
    assert after["acme"] == {"role": "team-vision"}


def test_replace_is_scoped_to_bindings(furnished):
    """"Replace the configuration" and "replace the bindings in it" look alike right up
    until the feed URL is gone."""
    from media_ai.core.config import save_bindings

    save_bindings(org_binding(), replace=True)

    after = written(furnished)
    assert set(after["bindings"]) == {"acme/fast"}, "the withdrawn binding is still there"
    assert "update" in after and "telemetry" in after and "acme" in after


def test_merging_is_the_default(furnished):
    from media_ai.core.config import save_bindings

    save_bindings(org_binding())
    assert set(written(furnished)["bindings"]) == {"personal/one", "acme/fast"}


def test_a_named_binding_is_replaced_whole_not_field_by_field(furnished):
    """A provisioner states the complete definition of what it owns; a field-wise merge
    would make clearing one impossible. `UserBinding.merged_with` is the other case."""
    from media_ai.core.config import save_bindings

    save_bindings({"personal/one": UserBinding(id="personal/one", base_url="https://new/v1")})
    assert written(furnished)["bindings"]["personal/one"] == {"base_url": "https://new/v1"}


def test_defaults_are_left_alone_unless_given(furnished):
    """Dropping every scene default is not implied by naming a set of bindings, and a
    machine with none refuses every call that does not name one."""
    from media_ai.core.config import save_bindings

    save_bindings(org_binding(), replace=True)
    assert written(furnished)["defaults"] == {"video.text_to_video": "personal/one"}


def test_defaults_merge_unless_replacing(furnished):
    from media_ai.core.config import save_bindings

    save_bindings(defaults={"image.text_to_image": "acme/fast"})
    assert written(furnished)["defaults"] == {
        "video.text_to_video": "personal/one", "image.text_to_image": "acme/fast",
    }
    save_bindings(defaults={"image.text_to_image": "acme/fast"}, replace=True)
    assert written(furnished)["defaults"] == {"image.text_to_image": "acme/fast"}


def test_it_still_refuses_a_raw_key(furnished):
    """The write-time guard is not bypassed by the merging writer."""
    from media_ai.core.config import save_bindings

    with pytest.raises(MediaError) as exc:
        save_bindings({"a/b": UserBinding(id="a/b", credential="sk-live-SECRET-abcdef")})
    assert exc.value.code == "credential_is_raw_key"
    assert written(furnished)["bindings"].keys() == {"personal/one"}
