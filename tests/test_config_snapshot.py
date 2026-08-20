"""One invocation, one view of the configuration.

The point is not the saved read — ``config.toml`` is a small file and nobody was
waiting on it. The point is that a command reads it more than once (``bind()``
resolves against it; the update notice asks it whether checking is even on), and two
reads can disagree. An editor saving in between, or a concurrent ``bindings add``,
would let one half of a single JSON object describe a configuration the other half
never saw.

So the properties below are about *coherence*, and the last one is about the seam
where coherence would otherwise become staleness: this process writing the file.
"""

from __future__ import annotations

import json

import pytest

from media_ai.cli import common
from media_ai.cli import config as config_cli
from media_ai.core import config as config_mod
from media_ai.core.config import Config, load_config, save_config, snapshot
from media_ai.core.errors import MediaError


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MEDIA_AI_CONFIG_FILE", str(path))
    path.write_text(f'schema = {config_mod.SCHEMA}\n[bindings."mock/mock"]\n', encoding="utf-8")
    return path


@pytest.fixture
def reads(monkeypatch):
    """Count the actual file reads, whatever the callers do."""
    real, calls = config_mod._read_config, []

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(config_mod, "_read_config", counting)
    return calls


def test_without_a_snapshot_every_call_reads(cfg, reads):
    """The default is unchanged: a library caller that opens no block sees no cache."""
    load_config()
    load_config()
    assert len(reads) == 2


def test_inside_a_snapshot_the_file_is_read_once(cfg, reads):
    with snapshot():
        first, second = load_config(), load_config()
    assert len(reads) == 1
    assert first is second


def test_an_explicit_path_is_always_read(cfg, reads):
    """A caller that named a file is asking about *that* file, not the one in effect."""
    with snapshot():
        load_config()
        load_config(cfg)
    assert len(reads) == 2


def test_the_snapshot_does_not_outlive_the_block(cfg, reads):
    with snapshot():
        load_config()
    load_config()
    assert len(reads) == 2


def test_snapshots_nest_and_restore(cfg, reads):
    with snapshot():
        load_config()
        with snapshot():
            load_config()
            load_config()
        load_config()  # back to the outer one, which already has its answer
    assert len(reads) == 2


def test_a_file_that_vanishes_mid_block_does_not_change_the_answer(cfg, reads):
    """Coherence, stated at its sharpest: the second reader sees what the first did."""
    with snapshot():
        first = load_config()
        cfg.unlink()
        assert load_config() is first
        assert "mock/mock" in load_config().bindings


def test_a_failure_is_remembered_too(cfg, reads):
    """Otherwise a file repaired mid-command makes one call site fail and the next pass,
    inside one JSON object that claims to describe one invocation.
    """
    cfg.write_text("schema = 2\nthis is not toml", encoding="utf-8")
    with snapshot():
        with pytest.raises(MediaError) as first:
            load_config()
        cfg.write_text(f"schema = {config_mod.SCHEMA}\n", encoding="utf-8")
        with pytest.raises(MediaError) as second:
            load_config()
    assert first.value.message == second.value.message
    assert len(reads) == 1


def test_writing_the_config_clears_the_snapshot(cfg, reads):
    """The one party allowed to change the answer is this process.

    Without this, ``bindings add`` would write a binding and then report a config that
    does not contain it — the same disagreement the snapshot exists to prevent, arriving
    from the other side.
    """
    with snapshot():
        assert load_config().defaults == {}
        save_config(Config(defaults={"image.text_to_image": "mock/mock"}, exists=True))
        assert load_config().defaults == {"image.text_to_image": "mock/mock"}


def test_a_whole_command_reads_the_config_once(cfg, reads, capsys):
    """The end-to-end claim, through the wrapper every command goes through.

    ``config show`` loads it, and so does the update-notice source on the way out —
    which is why ``run()`` puts the emit inside the block rather than after it.
    """
    import sys

    old, sys.argv = sys.argv, ["media-ai config", "show"]
    try:
        assert config_cli.main() == 0
    finally:
        sys.argv = old
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert len(reads) == 1


def test_run_leaves_no_snapshot_behind(cfg, reads, capsys):
    """``run()`` is called in-process by tests and by embedders, not only by a shell.

    Counted as a delta rather than a total: ``run()`` itself now reads the config once,
    for ``[telemetry]``, even when the command body reads nothing. That read is inside
    the snapshot — ``test_a_whole_command_reads_the_config_once`` is what holds it there
    — and what this test is about is the two reads *after* the block, which have to be
    two reads and not a cached one.
    """
    common.run(lambda args: {"ok": True}, _args())
    capsys.readouterr()
    during = len(reads)
    load_config()
    load_config()
    assert len(reads) - during == 2


def _args():
    import argparse

    return argparse.Namespace(pretty=False, verbose=False, log_level=None, metadata_out=None)
