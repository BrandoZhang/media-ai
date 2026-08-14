"""The schema chains: every number below the current one has an answer, for both files.

Both registries are empty, and these tests are most of the reason they exist. The day
somebody bumps either ``SCHEMA``, :func:`test_every_older_schema_has_an_answer` fails
until they have said which kind of change it was — a conversion, or a break. Deciding
that in advance, next to the change, is the whole point; the alternative is the
decision being made by whoever first hits the error on a user's machine.

The two documents share a mechanism and not their stakes. A config can be re-derived
by re-running setup, so refusing to convert one costs an afternoon; ``credentials.toml``
holds keys pasted in from somewhere else and possibly issued once, which is why its
refusal never suggests starting over.

The rest pin the two halves of the door: reading applies what it can and *never*
writes, and ``config migrate`` is where a rewrite is allowed to happen because someone
asked for it by name.
"""

from __future__ import annotations

import json
import stat
import tomllib

import pytest

from media_ai.cli import config as config_cli
from media_ai.core import migrations
from media_ai.core.config import SCHEMA, load_config, migrate_file
from media_ai.core.errors import MediaError
from media_ai.core.migrations import CONFIG, CREDENTIALS, DOCUMENTS, UNMIGRATABLE, Migration, plan, registered
from media_ai.credentials import stores


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(path))
    return path


@pytest.fixture
def creds(tmp_path, monkeypatch):
    path = tmp_path / "credentials.toml"
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(path))
    return path


@pytest.fixture
def registry(monkeypatch):
    """A registry this test owns, so a fake step cannot leak into the next test."""
    monkeypatch.setattr(migrations, "_REGISTRY", {doc: {} for doc in DOCUMENTS})
    return migrations._REGISTRY


def step(registry, document, frm, *, lossless, apply=lambda d: d, summary="a step"):
    registry[document][frm] = Migration(
        document=document, frm=frm, to=frm + 1, lossless=lossless, apply=apply, summary=summary
    )


def write_creds(path, body: str):
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)
    return path


def run_cli(*argv, expect=0, capsys=None):
    import sys

    old, sys.argv = sys.argv, ["media-ai config", *argv]
    try:
        code = config_cli.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code == expect, f"{argv} -> {code}: {out}"
    return json.loads(out.strip().splitlines()[-1])


def file_report(result, document):
    return next(f for f in result["files"] if f["document"] == document)


# ------------------------------------------------------------- the registry itself


@pytest.mark.parametrize("document, current", [(CONFIG, SCHEMA), (CREDENTIALS, stores.SCHEMA)])
def test_every_older_schema_has_an_answer(document, current):
    """No silent gaps: each schema below the current one converts, or says why not.

    This is the test that makes a ``SCHEMA`` bump a deliberate act, for either file.
    Both answers are fine — what is not fine is a number with neither, which reaches a
    user as "this build has no conversion for it" written by nobody in particular.
    """
    known = registered(document)
    for schema in range(1, current):
        assert schema in known or schema in UNMIGRATABLE[document], (
            f"{document} schema {schema} has neither a migration nor an entry in "
            "migrations.UNMIGRATABLE; bumping SCHEMA means deciding which"
        )


def test_both_documents_are_covered():
    """A third file with a schema of its own must not be able to skip the rule above."""
    assert set(UNMIGRATABLE) == set(DOCUMENTS)
    assert {registered(doc) is not None for doc in DOCUMENTS} == {True}


def test_a_migration_moves_exactly_one_schema(registry):
    """A 2→5 beside a 2→3 is two answers to one question, decided by lookup order."""
    with pytest.raises(ValueError, match="one schema at a time"):
        migrations.migration(document=CONFIG, frm=2, to=5, lossless=True, summary="too far")(lambda d: d)


def test_a_schema_cannot_have_two_migrations(registry):
    step(registry, CONFIG, 2, lossless=True)
    with pytest.raises(ValueError, match="already has a migration"):
        migrations.migration(document=CONFIG, frm=2, to=3, lossless=True, summary="second")(lambda d: d)


def test_an_unknown_document_is_refused(registry):
    """Bare strings would make a typo a registry nobody ever reads."""
    with pytest.raises(ValueError, match="unknown document"):
        migrations.migration(document="notes.toml", frm=1, to=2, lossless=True, summary="x")(lambda d: d)


def test_the_registries_do_not_share_steps(registry):
    """One mechanism, two documents — a config step must not convert a credentials file."""
    step(registry, CONFIG, 1, lossless=True)
    assert plan(CONFIG, 1, 2) is not None
    assert plan(CREDENTIALS, 1, 2) is None


def test_a_broken_chain_is_no_plan_rather_than_half_a_plan(registry):
    """Half a conversion is a document in a shape no build has ever read."""
    step(registry, CONFIG, 2, lossless=True)
    assert plan(CONFIG, 2, 3) is not None
    assert plan(CONFIG, 2, 4) is None


def test_a_plan_composes_the_steps_in_order(registry):
    step(registry, CONFIG, 2, lossless=True, summary="first")
    step(registry, CONFIG, 3, lossless=True, summary="second")
    assert [s.summary for s in plan(CONFIG, 2, 4)] == ["first", "second"]


# --------------------------------------------------------- reading a config file


def test_reading_applies_a_lossless_step_without_touching_the_file(cfg, registry, monkeypatch):
    """The load path converts in memory. A `--help` that rewrote a config file would be
    a surprise nobody asked for, and the other machine sharing the file still reads it.
    """
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    step(registry, CONFIG, 2, lossless=True,
         apply=lambda d: {**d, "defaults": {"image.text_to_image": "mock/mock"}})
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")

    assert load_config().defaults == {"image.text_to_image": "mock/mock"}
    assert cfg.read_text(encoding="utf-8") == before


def test_reading_refuses_a_lossy_step_and_names_the_command(cfg, registry, monkeypatch):
    """A step that loses something needs a decision, so the read stops and points."""
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    step(registry, CONFIG, 2, lossless=False)
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")

    with pytest.raises(MediaError) as exc:
        load_config()
    assert exc.value.code == "config_schema_outdated"
    assert "config migrate" in exc.value.message


def test_reading_a_schema_with_no_migration_says_to_start_over(cfg, monkeypatch):
    """No plan means no `config migrate` in the hint: that command could only fail."""
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")

    with pytest.raises(MediaError) as exc:
        load_config()
    assert exc.value.code == "config_schema_outdated"
    assert "config migrate" not in exc.value.message
    assert "init" in exc.value.message


def test_the_unmigratable_reason_reaches_the_user(cfg, monkeypatch):
    """`UNMIGRATABLE` is documentation only if it is printed where the refusal happens."""
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    monkeypatch.setitem(UNMIGRATABLE[CONFIG], 2, "the widget table has no successor")
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")

    with pytest.raises(MediaError) as exc:
        load_config()
    assert "the widget table has no successor" in exc.value.message


# ---------------------------------------------------- reading a credentials file


def test_reading_credentials_applies_a_lossless_step(creds, registry, monkeypatch):
    monkeypatch.setattr("media_ai.credentials.stores.SCHEMA", 2)
    step(registry, CREDENTIALS, 1, lossless=True,
         apply=lambda d: {**d, "acme": {"api_key": d["acme"]["secret"]}})
    write_creds(creds, 'schema = 1\n\n[acme]\nsecret = "sk-old-123456"\n')
    before = creds.read_text(encoding="utf-8")

    assert stores.named_account("acme") == "sk-old-123456"
    assert creds.read_text(encoding="utf-8") == before


def test_a_credentials_refusal_never_says_to_start_over(creds, monkeypatch):
    """The asymmetry that matters. "Delete the file and re-run setup" is an answer a
    config can take and this file cannot: the keys came from somewhere else and may
    have been issued once.
    """
    monkeypatch.setattr("media_ai.credentials.stores.SCHEMA", 2)
    write_creds(creds, 'schema = 1\n\n[acme]\napi_key = "sk-old-123456"\n')

    from media_ai.brand import cmd

    with pytest.raises(MediaError) as exc:
        stores.named_account("acme")
    assert exc.value.code == "credentials_schema_outdated"
    # The config path's remedy, which must not appear here.
    assert cmd("init") not in exc.value.message
    assert "do not delete the file" in exc.value.message
    assert "not be reissuable" in exc.value.message


def test_writing_converts_before_it_merges(creds, registry, monkeypatch):
    """Merging into a shape this build reads wrongly would rewrite the file in the old
    layout and take the keys with it."""
    monkeypatch.setattr("media_ai.credentials.stores.SCHEMA", 2)
    step(registry, CREDENTIALS, 1, lossless=True,
         apply=lambda d: {**d, "acme": {"api_key": d["acme"]["secret"]}})
    write_creds(creds, 'schema = 1\n\n[acme]\nsecret = "sk-old-123456"\n')

    stores.save_accounts({"new": "sk-new-123456"})
    after = tomllib.loads(creds.read_text(encoding="utf-8"))
    assert after["schema"] == 2
    assert after["acme"] == {"api_key": "sk-old-123456"}
    assert after["new"] == {"api_key": "sk-new-123456"}


# ------------------------------------------------------------- config migrate


def test_migrate_converts_both_files(cfg, creds, registry, monkeypatch, capsys):
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    monkeypatch.setattr("media_ai.credentials.stores.SCHEMA", 2)
    step(registry, CONFIG, 2, lossless=False, summary="pick one credential source per binding",
         apply=lambda d: {**d, "defaults": {"image.text_to_image": "mock/mock"}})
    step(registry, CREDENTIALS, 1, lossless=False, summary="rename secret to api_key",
         apply=lambda d: {**d, "acme": {"api_key": d["acme"]["secret"]}})
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")
    write_creds(creds, 'schema = 1\n\n[acme]\nsecret = "sk-old-123456"\n')

    out = run_cli("migrate", capsys=capsys)
    assert out["migrated"] is True
    assert file_report(out, "config.toml")["steps"] == ["pick one credential source per binding"]
    assert file_report(out, "credentials.toml")["steps"] == ["rename secret to api_key"]
    assert tomllib.loads(cfg.read_text(encoding="utf-8"))["schema"] == 3
    assert tomllib.loads(creds.read_text(encoding="utf-8")) == {
        "schema": 2, "acme": {"api_key": "sk-old-123456"},
    }


def test_the_converted_credentials_file_keeps_its_mode(creds, registry, monkeypatch, capsys):
    """A conversion that widened the mode would hand every key in it away."""
    monkeypatch.setattr("media_ai.credentials.stores.SCHEMA", 2)
    step(registry, CREDENTIALS, 1, lossless=False, apply=lambda d: d)
    write_creds(creds, 'schema = 1\n\n[acme]\napi_key = "sk-old-123456"\n')

    run_cli("migrate", capsys=capsys)
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600
    backup = next(creds.parent.glob("credentials.toml.bak*"))
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_nothing_is_written_when_either_file_cannot_be_converted(cfg, creds, registry, monkeypatch, capsys):
    """The rule `init` follows for these same two files: a dry pass first, so a
    document that cannot be converted fails with neither touched.
    """
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    monkeypatch.setattr("media_ai.credentials.stores.SCHEMA", 2)
    step(registry, CONFIG, 2, lossless=False, apply=lambda d: d)
    # nothing registered for credentials 1 -> 2
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")
    write_creds(creds, 'schema = 1\n\n[acme]\napi_key = "sk-old-123456"\n')
    before_cfg = cfg.read_text(encoding="utf-8")
    before_creds = creds.read_text(encoding="utf-8")

    out = run_cli("migrate", expect=4, capsys=capsys)
    assert out["error"]["code"] == "credentials_schema_outdated"
    assert cfg.read_text(encoding="utf-8") == before_cfg
    assert creds.read_text(encoding="utf-8") == before_creds


def test_dry_run_reports_the_steps_and_writes_nothing(cfg, registry, monkeypatch, capsys):
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    step(registry, CONFIG, 2, lossless=False, summary="drop the provider table")
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")

    out = run_cli("migrate", "--dry-run", capsys=capsys)
    assert out["migrated"] is False
    assert file_report(out, "config.toml")["steps"] == ["drop the provider table"]
    assert cfg.read_text(encoding="utf-8") == before


def test_migrating_current_files_succeeds_and_does_nothing(cfg, creds, capsys):
    """"Nothing to do" is the success case of a command whose job is to make a thing true."""
    cfg.write_text(f'schema = {SCHEMA}\n[bindings."mock/mock"]\n', encoding="utf-8")
    write_creds(creds, f'schema = {stores.SCHEMA}\n\n[acme]\napi_key = "sk-123456"\n')
    before = cfg.read_text(encoding="utf-8")

    out = run_cli("migrate", capsys=capsys)
    assert out["migrated"] is False
    assert [f["steps"] for f in out["files"]] == [[], []]
    assert cfg.read_text(encoding="utf-8") == before


def test_one_file_absent_is_reported_not_refused(cfg, creds, capsys):
    """Every binding on `env://` needs no credentials file; a fresh checkout has no
    config. Failing the command over either would break it where nothing is wrong."""
    cfg.write_text(f'schema = {SCHEMA}\n[bindings."mock/mock"]\n', encoding="utf-8")

    out = run_cli("migrate", capsys=capsys)
    assert file_report(out, "config.toml")["present"] is True
    assert file_report(out, "credentials.toml")["present"] is False
    assert not creds.exists()


def test_both_files_absent_is_an_error_not_two_new_files(cfg, creds, capsys):
    """`migrate` converts; it does not create."""
    out = run_cli("migrate", expect=2, capsys=capsys)
    assert out["error"]["code"] == "config_absent"
    assert not cfg.exists() and not creds.exists()


def test_a_migration_that_produces_an_unreadable_file_leaves_the_original(cfg, registry, monkeypatch):
    """Validated before it is written: a bad step must not leave a config nothing reads."""
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    step(registry, CONFIG, 2, lossless=False, apply=lambda d: {**d, "defaults": {"nonsense.scene": "mock/mock"}})
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")

    with pytest.raises(MediaError):
        migrate_file()
    assert cfg.read_text(encoding="utf-8") == before


# ------------------------------------------------- what must never reach stdout

# `config migrate` is the first non-interactive command that writes the secret file, so
# what it *says* about that file is worth pinning rather than inferring. The report is
# built from a path, a presence flag, two schema numbers and the step summaries — none
# of which is derived from the file's contents — but "none of which" is a property of
# today's code, and the next person to write a migration writes a `summary`.

SECRET = "sk-LIVE-SECRET-abcdef123456"
ACCOUNT = "internal-gateway"


def creds_with_a_key(path):
    return write_creds(path, f'schema = 1\n\n[{ACCOUNT}]\napi_key = "{SECRET}"\n')


def stdout_of(*argv, expect=0, capsys=None) -> str:
    import sys

    old, sys.argv = sys.argv, ["media-ai config", *argv]
    try:
        code = config_cli.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code == expect, f"{argv} -> {code}: {out}"
    return out


def test_a_no_op_migration_names_no_account_and_no_key(cfg, creds, capsys):
    cfg.write_text(f'schema = {SCHEMA}\n[bindings."mock/mock"]\n', encoding="utf-8")
    creds_with_a_key(creds)
    out = stdout_of("migrate", capsys=capsys)
    assert SECRET not in out and ACCOUNT not in out


def test_a_converting_migration_names_no_account_and_no_key(creds, registry, monkeypatch, capsys):
    """The path where the file's contents actually flow through the code."""
    monkeypatch.setattr("media_ai.credentials.stores.SCHEMA", 2)
    step(registry, CREDENTIALS, 1, lossless=False, summary="rename the account key")
    creds_with_a_key(creds)
    out = stdout_of("migrate", capsys=capsys)
    assert SECRET not in out and ACCOUNT not in out
    # …and it really did convert, or the assertion above is vacuous.
    assert tomllib.loads(creds.read_text(encoding="utf-8"))["schema"] == 2


def test_a_parse_failure_reports_a_position_not_the_line(creds, capsys):
    """A message quoting the offending text would put a key in the error contract."""
    write_creds(creds, f'schema = 1\n[acme]\napi_key = {SECRET}\n')  # unquoted: invalid TOML
    out = stdout_of("migrate", expect=4, capsys=capsys)
    assert SECRET not in out
    assert "line 3" in out


def test_a_summary_that_leaks_is_masked_on_the_way_out(registry, creds, monkeypatch, capsys):
    """The forward-looking half. Nothing stops a future migration from interpolating
    data into its `summary`, and that string is printed. It goes through the same
    redactor every other sink does, so a key-shaped token is masked even though it was
    never registered as a live secret — this asserts the backstop covers this path.
    """
    monkeypatch.setattr("media_ai.credentials.stores.SCHEMA", 2)
    step(registry, CREDENTIALS, 1, lossless=False, summary=f"moved {SECRET} to the new field")
    creds_with_a_key(creds)
    out = stdout_of("migrate", capsys=capsys)
    assert SECRET not in out
    assert "***" in out
