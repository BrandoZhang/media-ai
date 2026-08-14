"""The config schema chain: every number below the current one has an answer.

The registry is empty today, and these tests are most of the reason it exists. The
day somebody bumps ``SCHEMA``, :func:`test_every_older_schema_has_an_answer` fails
until they have said which kind of change it was — a conversion, or a break. Deciding
that in advance, next to the change, is the whole point; the alternative is the
decision being made by whoever first hits the error on a user's machine.

The rest pin the two halves of the door: reading applies what it can and *never*
writes, and ``config migrate`` is where a rewrite is allowed to happen because someone
asked for it by name.
"""

from __future__ import annotations

import json
import tomllib

import pytest

from media_ai.cli import config as config_cli
from media_ai.core import migrations
from media_ai.core.config import SCHEMA, load_config, migrate_file
from media_ai.core.errors import MediaError
from media_ai.core.migrations import UNMIGRATABLE, Migration, plan, registered


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(path))
    return path


@pytest.fixture
def registry(monkeypatch):
    """A registry this test owns, so a fake step cannot leak into the next test."""
    monkeypatch.setattr(migrations, "_REGISTRY", {})
    return migrations._REGISTRY


def step(registry, frm, *, lossless, apply=lambda d: d, summary="a step"):
    registry[frm] = Migration(frm=frm, to=frm + 1, lossless=lossless, apply=apply, summary=summary)


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


# ------------------------------------------------------------- the registry itself


def test_every_older_schema_has_an_answer():
    """No silent gaps: each schema below the current one converts, or says why not.

    This is the test that makes a ``SCHEMA`` bump a deliberate act. Both answers are
    fine — what is not fine is a number with neither, which reaches a user as "this
    build has no migration for it" written by nobody in particular.
    """
    known = registered()
    for schema in range(1, SCHEMA):
        assert schema in known or schema in UNMIGRATABLE, (
            f"schema {schema} has neither a migration nor an entry in migrations.UNMIGRATABLE; "
            "bumping SCHEMA means deciding which"
        )


def test_a_migration_moves_exactly_one_schema(registry):
    """A 2→5 beside a 2→3 is two answers to one question, decided by lookup order."""
    with pytest.raises(ValueError, match="one schema at a time"):
        migrations.migration(frm=2, to=5, lossless=True, summary="too far")(lambda d: d)


def test_a_schema_cannot_have_two_migrations(registry):
    step(registry, 2, lossless=True)
    with pytest.raises(ValueError, match="already has a migration"):
        migrations.migration(frm=2, to=3, lossless=True, summary="second")(lambda d: d)


def test_a_broken_chain_is_no_plan_rather_than_half_a_plan(registry):
    """Half a conversion is a document in a shape no build has ever read."""
    step(registry, 2, lossless=True)
    # nothing registered for 3
    assert plan(2, 3) is not None
    assert plan(2, 4) is None


def test_a_plan_composes_the_steps_in_order(registry):
    step(registry, 2, lossless=True, summary="first")
    step(registry, 3, lossless=True, summary="second")
    assert [s.summary for s in plan(2, 4)] == ["first", "second"]


# --------------------------------------------------------------- reading a file


def test_reading_applies_a_lossless_step_without_touching_the_file(cfg, registry, monkeypatch):
    """The load path converts in memory. A `--help` that rewrote a config file would be
    a surprise nobody asked for, and the other machine sharing the file still reads it.
    """
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    step(registry, 2, lossless=True, apply=lambda d: {**d, "defaults": {"image.text_to_image": "mock/mock"}})
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")

    assert load_config().defaults == {"image.text_to_image": "mock/mock"}
    assert cfg.read_text(encoding="utf-8") == before


def test_reading_refuses_a_lossy_step_and_names_the_command(cfg, registry, monkeypatch):
    """A step that loses something needs a decision, so the read stops and points."""
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    step(registry, 2, lossless=False)
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
    monkeypatch.setitem(UNMIGRATABLE, 2, "the widget table has no successor")
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")

    with pytest.raises(MediaError) as exc:
        load_config()
    assert "the widget table has no successor" in exc.value.message


# ------------------------------------------------------------- config migrate


def test_migrate_writes_the_converted_file_and_keeps_a_backup(cfg, registry, monkeypatch, capsys):
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    step(registry, 2, lossless=False, summary="pick one credential source per binding",
         apply=lambda d: {**d, "defaults": {"image.text_to_image": "mock/mock"}})
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")

    out = run_cli("migrate", capsys=capsys)
    assert out["migrated"] is True
    assert out["from_schema"] == 2 and out["to_schema"] == 3
    assert out["steps"] == ["pick one credential source per binding"]
    assert out["backup"] and tomllib.loads(cfg.read_text(encoding="utf-8")) == {
        "schema": 3,
        "bindings": {"mock/mock": {}},
        "defaults": {"image.text_to_image": "mock/mock"},
    }


def test_dry_run_reports_the_steps_and_writes_nothing(cfg, registry, monkeypatch, capsys):
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    step(registry, 2, lossless=False, summary="drop the provider table")
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")

    out = run_cli("migrate", "--dry-run", capsys=capsys)
    assert out["migrated"] is False
    assert out["steps"] == ["drop the provider table"]
    assert cfg.read_text(encoding="utf-8") == before


def test_migrating_a_current_file_succeeds_and_does_nothing(cfg, capsys):
    """"Nothing to do" is the success case of a command whose job is to make a thing true."""
    cfg.write_text(f'schema = {SCHEMA}\n[bindings."mock/mock"]\n', encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")

    out = run_cli("migrate", capsys=capsys)
    assert out["migrated"] is False and out["steps"] == []
    assert cfg.read_text(encoding="utf-8") == before


def test_migrating_an_absent_file_is_an_error_not_a_new_file(cfg, capsys):
    """`migrate` converts; it does not create. Writing an empty config here would hand
    back a configured-looking file that names nothing.
    """
    out = run_cli("migrate", expect=2, capsys=capsys)
    assert out["error"]["code"] == "config_absent"
    assert not cfg.exists()


def test_a_migration_that_produces_an_unreadable_file_leaves_the_original(cfg, registry, monkeypatch, capsys):
    """Validated before it is written: a bad step must not leave a config nothing reads."""
    monkeypatch.setattr("media_ai.core.config.SCHEMA", 3)
    step(registry, 2, lossless=False, apply=lambda d: {**d, "defaults": {"nonsense.scene": "mock/mock"}})
    cfg.write_text('schema = 2\n[bindings."mock/mock"]\n', encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")

    with pytest.raises(MediaError):
        migrate_file()
    assert cfg.read_text(encoding="utf-8") == before
