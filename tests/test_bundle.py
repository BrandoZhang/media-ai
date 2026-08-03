"""``media-ai config export|import`` — provisioning a machine from a file.

These two commands are the only ones that move *secrets between machines*, so the
guards worth having are about what travels and what refuses to: a key never leaves
without being asked for, only the accounts the exported bindings actually name go with
them, and a bundle this build cannot honour is refused before anything is written
rather than after.

The second half is about versions. A bundle exists to be read somewhere else, which in
a fleet means somewhere older or newer — the one document in the project that migrates
instead of refusing. :class:`Migrations` is driven directly, because a chain with a
hole in it fails silently as "unsupported" on a machine nobody is watching.

Driven through ``main()`` with a patched argv, like ``test_bindings_cli``, so the JSON
contract and the exit codes are exercised too.
"""

from __future__ import annotations

import io
import json
import stat
import tomllib
from pathlib import Path

import pytest
from media_ai.cli import bindings as bindings_mod
from media_ai.cli import config as config_mod
from media_ai.core.errors import MediaError
from media_ai.core.migrate import Migrations

RAW_KEY = "sk-live-ark-key-0123456789"


def run(mod, *argv, expect=0, capsys=None):
    """Invoke one command exactly as the console entry point does."""
    import sys

    argv = [f"media-ai {mod.__name__.rsplit('.', 1)[-1]}", *argv]
    old, sys.argv = sys.argv, argv
    try:
        code = mod.main()
    except SystemExit as exc:  # argparse parse failure
        code = exc.code
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code == expect, f"{argv} -> {code}: {out}"
    return json.loads(out.strip().splitlines()[-1])


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """``machine("prod")`` — point the CLI at one box's config and credentials.

    Two of them in one test is the whole point: export from the first, import into the
    second, and the assertions are about what crossed.
    """
    def make(name: str) -> dict:
        home = tmp_path / name
        home.mkdir(exist_ok=True)
        config, credentials = home / "config.toml", home / "credentials.toml"
        monkeypatch.setenv("MEDIA_CONFIG_FILE", str(config))
        monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(credentials))
        return {"home": home, "config": config, "credentials": credentials}

    return make


def write_credentials(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)


def read(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def source(machine, capsys):
    """A configured machine: one cred:// binding, one env:// binding, and an orphan key."""
    box = machine("source")
    run(bindings_mod, "add", "volc-ark/seedance-2.0", "--credential", "cred://ark-main",
        "--endpoint-id", "ep-20260803-demo", capsys=capsys)
    run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "env://OPENAI_API_KEY", capsys=capsys)
    run(config_mod, "set-default", "video.text_to_video", "volc-ark/seedance-2.0", capsys=capsys)
    run(config_mod, "set-default", "image.text_to_image", "openai/gpt-image-2", capsys=capsys)
    write_credentials(
        box["credentials"],
        f'[ark-main]\napi_key = "{RAW_KEY}"\n\n[retired-account]\napi_key = "sk-nobody-uses-this-anymore"\n',
    )
    return box


# ------------------------------------------------------------------------ export


def test_a_key_never_leaves_unless_it_was_asked_for(source, tmp_path, capsys):
    """The default export is the shareable half, and says so in its own header.

    Exporting configuration is a routine act — copying a setup to a second laptop, or
    into a repo — and it must not be the act that spreads a key. ``--include-credentials``
    is the consent, and without it the accounts file is not even read.
    """
    out = tmp_path / "plain.toml"
    res = run(config_mod, "export", "--output", str(out), capsys=capsys)

    assert res["carries_credentials"] is False
    assert res["credentials"] == []
    text = out.read_text(encoding="utf-8")
    assert RAW_KEY not in text and "credentials" not in read(out)
    assert mode_of(out) == 0o644, "the shareable half is a shareable file"
    # The references travel: what a binding uses is part of the configuration.
    assert read(out)["config"]["bindings"]["volc-ark/seedance-2.0"]["credential"] == "cred://ark-main"


def test_it_names_the_accounts_the_target_will_have_to_supply(source, tmp_path, capsys):
    """A bundle that leaves a `cred://` account behind is a bundle that half-works.

    The binding imports cleanly and then fails at call time with `credential_unresolved`,
    one machine away from the export that decided not to carry it. Saying so here is the
    difference between an operator adding the key and an operator debugging it.
    """
    res = run(config_mod, "export", "--output", str(tmp_path / "plain.toml"), capsys=capsys)
    assert res["missing_credentials"] == ["ark-main"]


def test_include_credentials_carries_the_accounts_its_bindings_name_and_no_others(source, tmp_path, capsys):
    """Least privilege: a key that travels for no reason is a key in one more place."""
    out = tmp_path / "full.toml"
    res = run(config_mod, "export", "--output", str(out), "--include-credentials", capsys=capsys)

    assert res["credentials"] == ["ark-main"]
    assert res["omitted_credentials"] == ["retired-account"], "an account nothing exported uses stays home"
    assert res["missing_credentials"] == []
    accounts = read(out)["credentials"]
    assert accounts["ark-main"]["api_key"] == RAW_KEY
    assert "retired-account" not in accounts
    assert mode_of(out) == 0o600, "a file holding a key is not readable by the rest of the box"


def test_a_shared_account_reached_through_another_is_carried_too(machine, tmp_path, capsys):
    """`cred://` chains resolve on the *source*; the target only has what it was sent.

    An account whose api_key is itself `cred://something-else` is useless without the
    account at the end of the chain, and the failure lands at call time on the machine
    least able to explain it.
    """
    box = machine("chained")
    run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "cred://front", capsys=capsys)
    write_credentials(
        box["credentials"],
        f'[front]\napi_key = "cred://behind"\n\n[behind]\napi_key = "{RAW_KEY}"\n',
    )
    res = run(config_mod, "export", "--output", str(tmp_path / "b.toml"),
              "--include-credentials", capsys=capsys)
    assert res["credentials"] == ["behind", "front"]


def test_a_bundle_of_vault_references_is_still_a_private_file(machine, tmp_path, capsys):
    """The file mode switches on the [credentials] section existing, not on what is in it.

    An all-``op://`` bundle written 0644 would change mode the day somebody pasted a
    real key into the same account file — silently, and long after the operator decided
    where to keep it.
    """
    box = machine("vaulted")
    run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "cred://vaulted", capsys=capsys)
    write_credentials(box["credentials"], '[vaulted]\napi_key = "op://vault/openai/key"\n')

    out = tmp_path / "vault.toml"
    res = run(config_mod, "export", "--output", str(out), "--include-credentials", capsys=capsys)
    assert res["carries_credentials"] is True and mode_of(out) == 0o600
    assert "SECRETS" in out.read_text(encoding="utf-8")
    # And it is copied, not resolved: no vault is contacted, on this machine or the next.
    assert read(out)["credentials"]["vaulted"]["api_key"] == "op://vault/openai/key"


def test_exporting_one_binding_takes_only_its_key(source, tmp_path, capsys):
    out = tmp_path / "one.toml"
    res = run(config_mod, "export", "--output", str(out), "--include-credentials",
              "--binding", "openai/gpt-image-2", capsys=capsys)

    assert res["bindings"] == ["openai/gpt-image-2"]
    assert res["credentials"] == [], "the only binding exported reads its key from the environment"
    assert res["omitted_credentials"] == ["ark-main", "retired-account"]
    written = read(out)["config"]
    assert "volc-ark/seedance-2.0" not in written["bindings"]
    assert written["defaults"] == {"image.text_to_image": "openai/gpt-image-2"}, "a default for a dropped binding goes"


def test_a_credential_free_default_survives_a_binding_filter(machine, tmp_path, capsys):
    """`video.concat` runs on `local/ffmpeg`, which is not in [bindings] at all.

    Filtering by binding cannot be said to have excluded something that was never
    configured — and dropping that default is how a provisioned box ends up refusing
    `video concat` while naming the free binding sitting right there.
    """
    machine("free")
    run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "env://OPENAI_API_KEY", capsys=capsys)
    run(config_mod, "set-default", "video.concat", "local/ffmpeg", capsys=capsys)
    res = run(config_mod, "export", "--output", str(tmp_path / "f.toml"),
              "--binding", "openai/gpt-image-2", capsys=capsys)
    assert res["defaults"]["video.concat"] == "local/ffmpeg"


def test_exporting_a_binding_this_machine_does_not_have_refuses_with_the_list(source, tmp_path, capsys):
    res = run(config_mod, "export", "--output", str(tmp_path / "x.toml"),
              "--binding", "gemini/veo-3.1", expect=2, capsys=capsys)
    assert res["error"]["code"] == "binding_not_configured"
    assert "openai/gpt-image-2" in res["error"]["details"]["configured"]
    assert not (tmp_path / "x.toml").exists()


def test_an_existing_bundle_is_not_overwritten_by_accident(source, tmp_path, capsys):
    out = tmp_path / "keep.toml"
    out.write_text("# hand-written\n", encoding="utf-8")
    res = run(config_mod, "export", "--output", str(out), expect=2, capsys=capsys)
    assert res["error"]["code"] == "output_exists"
    assert out.read_text(encoding="utf-8") == "# hand-written\n"
    run(config_mod, "export", "--output", str(out), "--force", capsys=capsys)
    assert "schema" in out.read_text(encoding="utf-8")


def test_exporting_an_unconfigured_machine_says_so_instead_of_writing_an_empty_file(machine, tmp_path, capsys):
    machine("blank")
    res = run(config_mod, "export", "--output", str(tmp_path / "empty.toml"), expect=2, capsys=capsys)
    assert res["error"]["code"] == "nothing_to_export"
    assert res["error"]["hint"] == "media-ai init"
    assert not (tmp_path / "empty.toml").exists()


def test_the_result_object_carries_names_and_never_values(source, tmp_path, capsys):
    """stdout is read by an agent, printed in CI logs, and pasted into issues."""
    res = run(config_mod, "export", "--output", str(tmp_path / "full.toml"),
              "--include-credentials", capsys=capsys)
    assert RAW_KEY not in json.dumps(res)


# ------------------------------------------------------------------------ import


def exported(tmp_path, capsys, *extra) -> Path:
    out = tmp_path / "bundle.toml"
    run(config_mod, "export", "--output", str(out), *extra, capsys=capsys)
    return out


def test_a_bundle_provisions_a_second_machine_with_no_wizard(source, machine, tmp_path, capsys):
    """The whole point, end to end: what was configured there is callable here."""
    bundle = exported(tmp_path, capsys, "--include-credentials")
    target = machine("prod")

    res = run(config_mod, "import", "--input", str(bundle), capsys=capsys)
    assert res["bindings"]["added"] == ["openai/gpt-image-2", "volc-ark/seedance-2.0"]
    assert res["credentials"]["added"] == ["ark-main"]
    assert res["defaults"]["video.text_to_video"] == "volc-ark/seedance-2.0"

    listed = {b["binding"]: b for b in run(bindings_mod, "list", capsys=capsys)["bindings"]}
    assert listed["volc-ark/seedance-2.0"]["configured"] is True
    assert listed["volc-ark/seedance-2.0"]["model_id"] == "ep-20260803-demo", "the endpoint id crossed with it"
    assert mode_of(target["credentials"]) == 0o600
    # The key is reachable through the same reference it had on the source machine.
    from media_ai.credentials.stores import named_account

    assert named_account("ark-main") == RAW_KEY


def test_import_reads_stdin_so_a_bundle_need_not_touch_the_target_disk(source, machine, tmp_path,
                                                                      capsys, monkeypatch):
    bundle = exported(tmp_path, capsys, "--include-credentials")
    machine("piped")
    monkeypatch.setattr("sys.stdin", io.StringIO(bundle.read_text(encoding="utf-8")))

    res = run(config_mod, "import", "--input", "-", capsys=capsys)
    assert res["source"] == "<stdin>"
    assert res["bindings"]["added"] == ["openai/gpt-image-2", "volc-ark/seedance-2.0"]


def test_a_dry_run_writes_nothing_at_all(source, machine, tmp_path, capsys):
    bundle = exported(tmp_path, capsys, "--include-credentials")
    target = machine("dry")

    res = run(config_mod, "import", "--input", str(bundle), "--dry-run", capsys=capsys)
    assert res["dry_run"] is True and res["bindings"]["added"]
    assert res["wrote"] == [str(target["config"]), str(target["credentials"])]
    assert not target["config"].exists() and not target["credentials"].exists()


def test_importing_merges_into_what_is_already_configured(source, machine, tmp_path, capsys):
    """Provisioning a box that is already doing something must not un-configure it."""
    bundle = exported(tmp_path, capsys)
    machine("mixed")
    run(bindings_mod, "add", "gemini/veo-3.1", "--credential", "env://GEMINI_API_KEY", capsys=capsys)

    res = run(config_mod, "import", "--input", str(bundle), capsys=capsys)
    assert res["mode"] == "merge"
    assert res["bindings"]["removed"] == []
    assert "gemini/veo-3.1" in run(config_mod, "show", capsys=capsys)["bindings"]


def test_replace_drops_what_the_bundle_does_not_carry_and_reports_it(source, machine, tmp_path, capsys):
    bundle = exported(tmp_path, capsys)
    machine("replaced")
    run(bindings_mod, "add", "gemini/veo-3.1", "--credential", "env://GEMINI_API_KEY", capsys=capsys)

    res = run(config_mod, "import", "--input", str(bundle), "--replace", capsys=capsys)
    assert res["mode"] == "replace"
    assert res["bindings"]["removed"] == ["gemini/veo-3.1"]
    assert "gemini/veo-3.1" not in run(config_mod, "show", capsys=capsys)["bindings"]


def test_replace_never_deletes_keys_the_bundle_did_not_bring(source, machine, tmp_path, capsys):
    """"Replace what is here with what I brought" cannot mean "delete what I didn't"."""
    bundle = exported(tmp_path, capsys)  # no [credentials] section
    target = machine("keys-kept")
    write_credentials(target["credentials"], '[local-only]\napi_key = "sk-set-up-by-hand-here"\n')

    run(config_mod, "import", "--input", str(bundle), "--replace", capsys=capsys)
    assert read(target["credentials"])["local-only"]["api_key"] == "sk-set-up-by-hand-here"


def test_skip_credentials_takes_the_config_and_leaves_the_keys(source, machine, tmp_path, capsys):
    bundle = exported(tmp_path, capsys, "--include-credentials")
    target = machine("no-keys")

    res = run(config_mod, "import", "--input", str(bundle), "--skip-credentials", capsys=capsys)
    assert res["credentials"] == {"added": [], "updated": [], "unchanged": [], "removed": []}
    assert target["config"].is_file() and not target["credentials"].exists()


def test_re_importing_the_same_bundle_changes_nothing(source, machine, tmp_path, capsys):
    """Provisioning re-runs. A second identical import must not accumulate backups —
    each one a copy of every key in the file, under a name nobody remembers to delete."""
    bundle = exported(tmp_path, capsys, "--include-credentials")
    target = machine("idempotent")

    run(config_mod, "import", "--input", str(bundle), capsys=capsys)
    before = (target["config"].read_bytes(), target["credentials"].read_bytes())
    res = run(config_mod, "import", "--input", str(bundle), capsys=capsys)

    assert res["backed_up"] == []
    assert res["bindings"]["unchanged"] == ["openai/gpt-image-2", "volc-ark/seedance-2.0"]
    assert res["credentials"]["unchanged"] == ["ark-main"]
    assert (target["config"].read_bytes(), target["credentials"].read_bytes()) == before
    assert not list(target["home"].glob("*.bak*"))


def test_an_import_that_really_changes_something_keeps_the_previous_file(source, machine, tmp_path, capsys):
    bundle = exported(tmp_path, capsys)
    target = machine("backed-up")
    run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "env://SOMETHING_ELSE", capsys=capsys)

    res = run(config_mod, "import", "--input", str(bundle), capsys=capsys)
    assert res["bindings"]["updated"] == ["openai/gpt-image-2"]
    assert res["backed_up"] and "SOMETHING_ELSE" in Path(res["backed_up"][0]).read_text(encoding="utf-8")
    assert read(target["config"])["bindings"]["openai/gpt-image-2"]["credential"] == "env://OPENAI_API_KEY"


def test_a_missing_bundle_is_a_not_found_with_a_runnable_hint(machine, tmp_path, capsys):
    machine("empty")
    res = run(config_mod, "import", "--input", str(tmp_path / "nope.toml"), expect=9, capsys=capsys)
    assert res["error"]["code"] == "bundle_not_found"
    assert res["error"]["hint"].startswith("media-ai config export")


def test_a_raw_key_in_the_config_half_is_refused_exactly_as_it_is_in_the_file(machine, tmp_path, capsys):
    """One parser decides what a valid config is. A bundle cannot be the way around it."""
    target = machine("smuggled")
    bundle = tmp_path / "smuggled.toml"
    bundle.write_text(
        f'schema = 1\n\n[config]\nschema = 2\n[config.bindings."mock/mock"]\ncredential = "{RAW_KEY}"\n',
        encoding="utf-8",
    )
    res = run(config_mod, "import", "--input", str(bundle), expect=2, capsys=capsys)
    assert res["error"]["code"] == "credential_is_raw_key"
    assert not target["config"].exists(), "nothing may be written on the path that rejects a key"
    assert RAW_KEY not in json.dumps(res)


def test_a_bundle_from_a_newer_build_is_refused_before_it_touches_anything(machine, tmp_path, capsys):
    target = machine("older")
    bundle = tmp_path / "future.toml"
    bundle.write_text("schema = 99\n\n[config]\nschema = 2\n", encoding="utf-8")

    res = run(config_mod, "import", "--input", str(bundle), expect=2, capsys=capsys)
    assert res["error"]["code"] == "bundle_schema_newer"
    assert res["error"]["details"] == {"found": 99, "supported": 1}
    assert "upgrade media-ai" in res["error"]["hint"]
    assert not target["config"].exists()


def test_something_that_is_not_a_bundle_says_which_part_is_missing(machine, tmp_path, capsys):
    machine("confused")
    plain = tmp_path / "config.toml"
    plain.write_text('schema = 2\n\n[bindings."mock/mock"]\n', encoding="utf-8")
    res = run(config_mod, "import", "--input", str(plain), expect=2, capsys=capsys)
    # A config file *is* a valid-looking TOML document with a schema in it — the number
    # is what tells the two apart, and 2 is not a bundle version.
    assert res["error"]["code"] == "bundle_schema_newer"


def test_a_binding_this_build_does_not_declare_is_refused_not_written(machine, tmp_path, capsys):
    """A config naming an undeclared binding breaks *every* later command.

    ``available_bindings`` raises on it, so ``bindings list`` — the command an operator
    would run to find out what happened — fails too. Importing a newer fleet member's
    bundle onto an older CLI is the ordinary way to get there.
    """
    target = machine("older-cli")
    bundle = tmp_path / "newer-fleet.toml"
    bundle.write_text(
        'schema = 1\n\n[config]\nschema = 2\n'
        '[config.bindings."acme/quantum-9"]\ncredential = "env://ACME_KEY"\n'
        '[config.bindings."mock/mock"]\n'
        '[config.defaults]\n"image.text_to_image" = "acme/quantum-9"\n',
        encoding="utf-8",
    )
    res = run(config_mod, "import", "--input", str(bundle), expect=2, capsys=capsys)
    assert res["error"]["code"] == "binding_undeclared"
    assert res["error"]["details"]["bindings"] == ["acme/quantum-9"]
    assert "mock/mock" in res["error"]["details"]["declared"]
    assert not target["config"].exists()

    ok = run(config_mod, "import", "--input", str(bundle), "--skip-unknown", capsys=capsys)
    assert ok["skipped"] == {"bindings": ["acme/quantum-9"], "defaults": {"image.text_to_image": "acme/quantum-9"}}
    assert ok["bindings"]["added"] == ["mock/mock"]
    # And what was written is usable — which is the whole reason for refusing above.
    assert run(bindings_mod, "list", capsys=capsys)["ok"]


def test_a_default_naming_a_binding_the_target_cannot_reach_is_refused(machine, tmp_path, capsys):
    """A default is what every unflagged call silently gets, so it may not dangle."""
    target = machine("partial")
    bundle = tmp_path / "partial.toml"
    bundle.write_text(
        'schema = 1\n\n[config]\nschema = 2\n[config.bindings."mock/mock"]\n'
        '[config.defaults]\n"video.text_to_video" = "gemini/veo-3.1"\n',
        encoding="utf-8",
    )
    res = run(config_mod, "import", "--input", str(bundle), expect=2, capsys=capsys)
    assert res["error"]["code"] == "default_binding_missing"
    assert not target["config"].exists()

    ok = run(config_mod, "import", "--input", str(bundle), "--skip-unknown", capsys=capsys)
    assert ok["skipped"]["defaults"] == {"video.text_to_video": "gemini/veo-3.1"}
    assert "video.text_to_video" not in ok["defaults"]


# ----------------------------------------------------------------- the version seam


def test_a_document_at_the_current_version_is_left_alone():
    chain = Migrations("thing", target=1)
    assert chain.upgrade({"schema": 1, "keep": "me"}, source="t") == {"schema": 1, "keep": "me"}


def test_steps_run_in_order_and_the_chain_owns_the_version():
    """A step that had to write its own number could skip its successor by fumbling it."""
    chain = Migrations("thing", target=3)

    @chain.step(1)
    def _one(data: dict) -> dict:
        return {**data, "trail": [*data.get("trail", []), "1->2"]}

    @chain.step(2)
    def _two(data: dict) -> dict:
        return {**data, "trail": [*data.get("trail", []), "2->3"]}

    start = {"schema": 1}
    out = chain.upgrade(start, source="t")
    assert out == {"schema": 3, "trail": ["1->2", "2->3"]}
    assert start == {"schema": 1}, "the input document is never mutated"


def test_a_hole_in_the_chain_refuses_instead_of_passing_the_document_through():
    """Half-understood is worse than refused: the fields a later version reused would
    be read with their old meaning, silently."""
    chain = Migrations("thing", target=3)
    chain.step(2)(lambda data: data)

    with pytest.raises(MediaError) as exc:
        chain.upgrade({"schema": 1}, source="t")
    assert exc.value.code == "thing_schema_unsupported"
    assert exc.value.details == {"found": 1, "supported": 3}


@pytest.mark.parametrize("value", [None, "1", True, 0, -3])
def test_a_document_with_no_usable_version_is_not_one_of_ours(value):
    """`schema = true` is the interesting one: bool is an int subclass in Python, so
    without an explicit check it would read as version 1 and be quietly accepted."""
    chain = Migrations("thing", target=1)
    with pytest.raises(MediaError) as exc:
        chain.upgrade({"schema": value} if value is not None else {}, source="t")
    assert exc.value.code == "thing_schema_missing"


def test_a_step_out_of_range_or_declared_twice_is_a_bug_at_import_time():
    chain = Migrations("thing", target=2)
    chain.step(1)(lambda data: data)
    with pytest.raises(ValueError):
        chain.step(1)(lambda data: data)
    with pytest.raises(ValueError):
        chain.step(2)(lambda data: data)


def test_the_config_payload_has_its_own_chain_pinned_to_the_config_schema():
    """The envelope and the payload version independently: a bundle written a year ago
    is still schema 1 while `config.toml` has moved on, and it is the payload that then
    needs upgrading."""
    from media_ai.core.bundle import CONFIG_MIGRATIONS
    from media_ai.core.config import SCHEMA

    assert CONFIG_MIGRATIONS.target == SCHEMA
