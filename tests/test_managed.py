"""``[managed]`` — which entries in ``config.toml`` an organisation wrote, not the user.

Nothing writes this table yet. It is modelled ahead of the push that will, because the
alternative is modelling it *after* a fleet has org-written entries in files that do
not say so — at which point no build can tell them from what a user typed, and the only
safe behaviour left is to never touch anything.

The property under test is therefore preservation, not use. A command that has nothing
to do with provenance rewrites the whole file (``render_config`` cannot round-trip), so
the failure that matters is `bindings add` quietly dropping the ownership record and
leaving org-written entries behind with nothing claiming them.
"""

from __future__ import annotations

import json
import sys
import tomllib

import pytest

from media_ai.core.config import (
    SCHEMA,
    Config,
    Managed,
    UserBinding,
    load_config,
    render_config,
    save_config,
)
from media_ai.core.errors import MediaError

MANAGED = f"""\
schema = {SCHEMA}

[bindings."mock/mock"]

[defaults]
"image.text_to_image" = "mock/mock"

[managed]
source   = "https://internal.example/media-ai/setup.json"
revision = "2026-08-14T02:00:00Z"
bindings = ["mock/mock"]
defaults = ["image.text_to_image"]
"""


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(path))
    return path


def read(cfg) -> dict:
    return tomllib.loads(cfg.read_text(encoding="utf-8"))


def run(mod, *argv, expect=0, capsys=None) -> dict:
    argv = [f"media-ai {mod.__name__.rsplit('.', 1)[-1]}", *argv]
    old, sys.argv = sys.argv, argv
    try:
        code = mod.main()
    except SystemExit as exc:
        code = exc.code
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code == expect, f"{argv} -> {code}: {out}"
    return json.loads(out.strip().splitlines()[-1])


# --------------------------------------------------------------------- parsing


def test_the_table_is_read(cfg):
    cfg.write_text(MANAGED, encoding="utf-8")
    managed = load_config().managed
    assert managed.source == "https://internal.example/media-ai/setup.json"
    assert managed.revision == "2026-08-14T02:00:00Z"
    assert managed.owns_binding("mock/mock")
    assert managed.owns_default("image.text_to_image")
    assert not managed.owns_binding("local/ffmpeg")


def test_an_ordinary_config_has_no_owner(cfg):
    """Absent is the state almost every installation is in, and it must stay cheap."""
    cfg.write_text(f'schema = {SCHEMA}\n[bindings."mock/mock"]\n', encoding="utf-8")
    assert load_config().managed is None


def test_an_ownership_claim_needs_an_owner(cfg):
    """A push could neither recognise nor safely disown entries claimed by nobody."""
    cfg.write_text(f"schema = {SCHEMA}\n[managed]\nbindings = []\n", encoding="utf-8")
    with pytest.raises(MediaError) as exc:
        load_config()
    assert exc.value.code == "managed_source_missing"


@pytest.mark.parametrize("body", [
    "[managed]\nsource = 1\n",
    '[managed]\nsource = "x"\nbindings = "mock/mock"\n',
    '[managed]\nsource = "x"\ndefaults = [1, 2]\n',
    '[managed]\nsource = "x"\nrevision = ""\n',
    'managed = "yes"\n',
])
def test_a_malformed_table_is_refused(cfg, body):
    """Hand-editable like the rest of the file, so a typo in it is a config error."""
    cfg.write_text(f"schema = {SCHEMA}\n{body}", encoding="utf-8")
    with pytest.raises(MediaError):
        load_config()


def test_a_name_this_build_does_not_know_is_not_rejected_here(cfg):
    """The table records what was written; `[bindings]` and `[defaults]` validate it.

    Failing here would report a push's mistake against the table *describing* the
    entry rather than against the entry, which is one indirection away from the field
    that actually carries it.
    """
    cfg.write_text(
        f'schema = {SCHEMA}\n[managed]\nsource = "x"\nbindings = ["who/knows"]\n',
        encoding="utf-8",
    )
    assert load_config().managed.owns_binding("who/knows")


# ---------------------------------------------------------------- preservation


def test_a_write_keeps_the_ownership_record(cfg):
    """The whole point. `render_config` rebuilds the file from the parsed object."""
    cfg.write_text(MANAGED, encoding="utf-8")
    save_config(load_config())
    assert read(cfg)["managed"] == {
        "source": "https://internal.example/media-ai/setup.json",
        "revision": "2026-08-14T02:00:00Z",
        "bindings": ["mock/mock"],
        "defaults": ["image.text_to_image"],
    }


def test_a_command_about_something_else_keeps_it(cfg, capsys):
    """`bindings add` rewrites the whole file; dropping the record here would leave the
    org-written entries looking exactly like ones the user typed.
    """
    from media_ai.cli import bindings as bindings_mod

    cfg.write_text(MANAGED, encoding="utf-8")
    run(bindings_mod, "add", "local/ffmpeg", capsys=capsys)
    after = read(cfg)
    assert after["managed"]["bindings"] == ["mock/mock"]
    assert "local/ffmpeg" in after["bindings"]


def test_setting_a_default_keeps_it(cfg, capsys):
    from media_ai.cli import config as config_mod

    cfg.write_text(MANAGED, encoding="utf-8")
    run(config_mod, "set-default", "video.text_to_video", "mock/mock", capsys=capsys)
    assert read(cfg)["managed"]["source"].startswith("https://internal.example")


def test_an_absent_table_is_not_written(cfg):
    """Same rule as `[update]`: a table of nothing invites editing settings nobody chose."""
    save_config(Config(bindings={"mock/mock": UserBinding(id="mock/mock")}, exists=True))
    assert "managed" not in read(cfg)


def test_empty_lists_are_not_written(cfg):
    """A source that owns nothing yet is a real state — it just has nothing to list."""
    rendered = render_config(Config(managed=Managed(source="s"), exists=True))
    assert tomllib.loads(rendered)["managed"] == {"source": "s"}


def test_it_round_trips(cfg):
    cfg.write_text(MANAGED, encoding="utf-8")
    once = load_config()
    save_config(once)
    assert load_config().managed == once.managed


# --------------------------------------------------------------------- reporting


def test_config_show_says_where_the_entries_came_from(cfg, capsys):
    """"Why is this binding here — I never added it?" is otherwise unanswerable."""
    from media_ai.cli import config as config_mod

    cfg.write_text(MANAGED, encoding="utf-8")
    out = run(config_mod, "show", capsys=capsys)
    assert out["managed"]["source"] == "https://internal.example/media-ai/setup.json"
    assert out["managed"]["bindings"] == ["mock/mock"]


def test_config_show_omits_it_when_there_is_none(cfg, capsys):
    from media_ai.cli import config as config_mod

    cfg.write_text(f'schema = {SCHEMA}\n[bindings."mock/mock"]\n', encoding="utf-8")
    assert "managed" not in run(config_mod, "show", capsys=capsys)
