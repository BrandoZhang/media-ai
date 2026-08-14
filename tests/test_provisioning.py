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
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
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
