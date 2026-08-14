"""Compatibility of ``config.toml`` across builds, in both directions.

Two properties, and they are not the same one seen twice:

- **A build never deletes what it does not understand.** ``render_config`` rebuilds
  the whole file from the parsed object, so any table or key not modelled here is
  dropped by the next write — in a command about something else entirely. That is how
  a field added in a later release disappears on a machine still running an older
  build, and it is invisible.
- **A build refuses a file from a newer one.** With its own distinct error, because
  "your file is ahead of your CLI" and "your file is behind your CLI" have opposite
  fixes, and the wrong one talks the user into overwriting the good file.

The first is why the second can stay rare: an additive field no longer needs a schema
bump to survive contact with an old build, so the bump is left to mean what it should
— this document's meaning changed.
"""

from __future__ import annotations

import tomllib

import pytest

from media_ai.core.config import SCHEMA, Config, UserBinding, load_config, render_config, save_config
from media_ai.core.errors import MediaError

BINDING = "mock/mock"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A config path this test owns, wired up the way every command finds it."""
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(path))
    return path


def write(cfg, text: str):
    cfg.write_text(text, encoding="utf-8")
    return cfg


def read(cfg) -> dict:
    return tomllib.loads(cfg.read_text(encoding="utf-8"))


def roundtrip(cfg) -> dict:
    """Load the file and write it straight back — what any editing command does."""
    save_config(load_config(cfg))
    return read(cfg)


# ------------------------------------------------------- unknown fields survive


def test_an_unknown_top_level_table_survives_a_write(cfg):
    """The case this exists for: a later release adds a table, an older build writes.

    Nothing in this build reads `[telemetry]`, and that is exactly the point — the build
    that does not understand a table must still hand it back. `[update]` used to be the
    example here and then became a real one, which is the whole lifecycle in miniature:
    a field arrives unknown, survives, and is understood a release later.
    """
    write(cfg, f'schema = {SCHEMA}\n\n[telemetry]\nenabled = false\nsample_rate = 10\n')
    assert roundtrip(cfg)["telemetry"] == {"enabled": False, "sample_rate": 10}


def test_a_provenance_table_a_distribution_wrote_survives(cfg):
    """The shape `docs/EXTENDING.md` tells a provisioner to use, in the shape it uses it.

    A distribution that writes this file from its own service may want to record which
    entries are *its* — so a later push can tell them from what a user typed. This
    project deliberately models no such table: the rules are the distribution's, and a
    schema here would only constrain them. Preservation is what makes that answer
    correct, and lists of names are the part of it worth pinning, because that is the
    shape an ownership record actually takes and the writer's subset is narrow.
    """
    write(cfg, f'schema = {SCHEMA}\n\n[acme]\nsource = "https://config.internal/x"\n'
               'owns = ["acme/fast", "acme/pro"]\n')
    assert roundtrip(cfg)["acme"] == {
        "source": "https://config.internal/x",
        "owns": ["acme/fast", "acme/pro"],
    }


def test_an_unknown_top_level_scalar_survives_a_write(cfg):
    write(cfg, f'schema = {SCHEMA}\nprofile = "team"\n')
    assert roundtrip(cfg)["profile"] == "team"


def test_an_unknown_key_inside_a_binding_survives_a_write(cfg):
    """Per-binding fields are the likelier addition, and the likelier loss.

    `bindings add <id> --credential …` is the command every resolution error hints at,
    so an unmodelled per-binding field is dropped by the one command a confused user is
    most likely to run.
    """
    write(cfg, f'schema = {SCHEMA}\n\n[bindings."{BINDING}"]\ncredential = "env://K"\nissued_by = "setup.internal"\n')
    entry = roundtrip(cfg)["bindings"][BINDING]
    assert entry == {"credential": "env://K", "issued_by": "setup.internal"}


def test_editing_one_binding_keeps_the_unknown_keys_on_the_others(cfg):
    """`merged_with` is the single path that edits an entry, so it has to carry them."""
    write(
        cfg,
        f'schema = {SCHEMA}\n\n'
        f'[bindings."{BINDING}"]\ncredential = "env://A"\nissued_by = "setup.internal"\n\n'
        '[bindings."openai/gpt-image-2"]\ncredential = "env://B"\nnote = "keep me"\n',
    )
    config = load_config(cfg)
    edited = config.bindings[BINDING].merged_with(credential="env://C")
    save_config(Config(bindings={**config.bindings, BINDING: edited}, defaults=config.defaults, extra=config.extra))

    written = read(cfg)["bindings"]
    assert written[BINDING] == {"credential": "env://C", "issued_by": "setup.internal"}
    assert written["openai/gpt-image-2"]["note"] == "keep me"


def test_a_modelled_field_wins_over_a_leftover_in_extra():
    """A stale duplicate must never be able to overwrite a parsed, validated field.

    Only reachable through a future rename — the loader cannot produce both at once —
    which is precisely when it would matter and nobody would be looking.
    """
    binding = UserBinding(id=BINDING, credential="env://REAL", extra={"credential": "env://STALE"})
    rendered = tomllib.loads(render_config(Config(bindings={BINDING: binding})))
    assert rendered["bindings"][BINDING]["credential"] == "env://REAL"


def test_an_unknown_table_cannot_displace_a_modelled_one(cfg):
    """`extra` never carries `bindings`/`defaults`/`schema`, so it cannot shadow them."""
    write(cfg, f'schema = {SCHEMA}\n\n[bindings."{BINDING}"]\ncredential = "env://K"\n\n[defaults]\n')
    config = load_config(cfg)
    assert config.extra == {}
    assert list(config.bindings) == [BINDING]


def test_a_value_the_writer_cannot_express_is_refused_and_named(cfg):
    """Refusing beats dropping — dropping is the failure this whole file is about.

    A float is outside `tomlwrite`'s subset. The message has to name the file, because
    the command that hits this is one about a binding, not about the odd value.
    """
    write(cfg, f'schema = {SCHEMA}\n\n[bindings."{BINDING}"]\ncredential = "env://K"\ntimeout = 1.5\n')
    config = load_config(cfg)
    with pytest.raises(MediaError) as excinfo:
        render_config(config)
    assert excinfo.value.code == "config_unwritable_field"
    assert str(cfg) in excinfo.value.message


def test_a_refused_write_leaves_the_previous_file_alone(cfg):
    """No `.bak`, no truncation: a write that cannot happen must not half-happen."""
    before = f'schema = {SCHEMA}\n\n[bindings."{BINDING}"]\ncredential = "env://K"\ntimeout = 1.5\n'
    write(cfg, before)
    with pytest.raises(MediaError):
        save_config(load_config(cfg))
    assert cfg.read_text(encoding="utf-8") == before
    assert not list(cfg.parent.glob("*.bak*"))


# ------------------------------------------------------------- the schema door


def test_a_file_from_a_newer_build_is_refused_with_its_own_code(cfg):
    """The direction that used to share a message with "outdated", and must not.

    The user is holding a file *ahead* of this CLI — a second machine, an old
    virtualenv, a downgrade. Telling them to re-run setup would overwrite it.
    """
    write(cfg, f"schema = {SCHEMA + 1}\n")
    with pytest.raises(MediaError) as excinfo:
        load_config(cfg)
    assert excinfo.value.code == "config_from_newer_build"
    assert "newer build" in excinfo.value.message


def test_a_schema_this_build_cannot_migrate_is_refused_as_outdated(cfg):
    """The other direction keeps the actionable answer it always had."""
    write(cfg, f'schema = 1\n\n[bindings."{BINDING}"]\ncredential = "env://K"\n')
    with pytest.raises(MediaError) as excinfo:
        load_config(cfg)
    assert excinfo.value.code == "config_schema_outdated"


def test_the_pre_binding_layout_is_still_recognised_by_shape(cfg):
    """Shape detection is the fallback whenever the version field is missing.

    A v1 file has no `schema` key at all, so reading "absent" as "current" would let it
    through as a config with no bindings — configured-looking, and empty.
    """
    write(cfg, '[profiles.default]\nprovider = "volc"\n')
    with pytest.raises(MediaError) as excinfo:
        load_config(cfg)
    assert excinfo.value.code == "config_schema_outdated"


def test_a_minimal_hand_written_file_needs_no_schema_key(cfg):
    write(cfg, f'[bindings."{BINDING}"]\ncredential = "env://K"\n')
    assert list(load_config(cfg).bindings) == [BINDING]


def test_a_write_always_stamps_the_schema(cfg):
    """So the "absent means current" guess is made at most once per file."""
    write(cfg, f'[bindings."{BINDING}"]\ncredential = "env://K"\n')
    assert roundtrip(cfg)["schema"] == SCHEMA


@pytest.mark.parametrize("value", ['"2"', "true", "2.0"])
def test_a_schema_that_is_not_an_integer_says_so(cfg, value):
    """`true` is the sharp one: bool is an int subclass and would compare as schema 1."""
    write(cfg, f"schema = {value}\n")
    with pytest.raises(MediaError) as excinfo:
        load_config(cfg)
    assert "must be an integer" in excinfo.value.message


# --------------------------------------------------------------- through the CLI


def test_bindings_add_does_not_eat_what_it_did_not_write(cfg, capsys):
    """The loss path end to end, through the command that would have caused it.

    Unit-testing `render_config` proves the object round-trips; this proves the thing a
    user actually does — adding an unrelated binding — leaves the rest of the file
    intact. The same shape as the `merged_with` bug that came before it, one level up.
    """
    import sys

    from media_ai.cli import bindings as bindings_mod

    write(
        cfg,
        f'schema = {SCHEMA}\n\n[update]\ncheck = false\n\n'
        f'[bindings."{BINDING}"]\ncredential = "env://A"\nissued_by = "setup.internal"\n',
    )
    argv = ["media-ai bindings", "add", "openai/gpt-image-2", "--credential", "env://OPENAI_API_KEY"]
    old, sys.argv = sys.argv, argv
    try:
        assert bindings_mod.main() == 0, capsys.readouterr().out
    finally:
        sys.argv = old

    written = read(cfg)
    assert written["update"] == {"check": False}
    assert written["bindings"][BINDING]["issued_by"] == "setup.internal"
    assert written["bindings"]["openai/gpt-image-2"]["credential"] == "env://OPENAI_API_KEY"
