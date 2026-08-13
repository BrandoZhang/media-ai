"""Which build produced this artifact, and which build wrote this ledger line.

``meta`` already answers *what* produced a result — the binding, the scene — and the
one thing it could not answer was *which version of this CLI*. That is the first
question of every bug report, every bisect, and every "why does this look different
from last week", and by the time it is asked the shell that ran the command is gone.
The JSON beside the artifact is what is left.

The ledger needs the same field for a different reason: it is append-only, so one file
routinely holds lines written by several versions and can never be migrated into
agreement. Per-record is the only place the answer can live.
"""

from __future__ import annotations

import json

import pytest
from conftest import adapter_for

import media_ai
from media_ai.cli import common
from media_ai.core.result import Artifact, GenerationResult
from media_ai.core.scene import Scene


class _Binding:
    """The two attributes `stamp` reads off a resolved binding."""

    id = "mock/mock"


def result(**meta) -> GenerationResult:
    return GenerationResult(
        modality="image", provider="mock", model="mock",
        artifacts=[Artifact(path="/tmp/x.png", kind="image")], meta=dict(meta),
    )


# ------------------------------------------------------------------- meta


def test_a_stamped_result_names_the_build_that_made_it():
    stamped = common.stamp(result(), _Binding(), Scene.IMAGE_TEXT_TO_IMAGE)
    assert stamped.meta["tool_version"] == media_ai.__version__


def test_the_version_is_stamped_even_when_the_scene_is_unknowable():
    """`job query` finalizes work submitted by an earlier process.

    The scene is genuinely gone and stays absent; the version is not — this process
    is the one writing the file, whoever submitted the job.
    """
    stamped = common.stamp(result(), _Binding(), None)
    assert "scene" not in stamped.meta
    assert stamped.meta["tool_version"] == media_ai.__version__


def test_a_version_an_adapter_supplied_is_overwritten():
    """Unlike `binding` and `scene`, which an adapter may legitimately have set.

    The running version is something only this process knows, so a value already
    sitting here came from somewhere that cannot be right about it.
    """
    stamped = common.stamp(result(tool_version="0.0.1-from-somewhere-else"), _Binding(), None)
    assert stamped.meta["tool_version"] == media_ai.__version__


def test_stamping_something_without_meta_is_still_fine():
    """`stamp` is handed whatever a command returns; not all of it carries `meta`."""
    common.stamp(object(), _Binding(), Scene.IMAGE_TEXT_TO_IMAGE)


# ----------------------------------------------------------------- the ledger


def test_every_ledger_line_carries_the_version(_ledger):
    from media_ai.core import usage

    usage.record_usage({"binding": "mock/mock", "scene": "image.text_to_image", "generated_images": 1})
    line = json.loads(_ledger.read_text(encoding="utf-8").splitlines()[0])
    assert line["tool_version"] == media_ai.__version__


def test_the_version_comes_from_the_sink_not_the_caller(_ledger):
    """`Adapter.record` is the funnel adapters use, but it is not the only one.

    Putting the stamp in `record_usage` means a line cannot be written without it —
    including from `job query`, which records through the same sink with no scene.
    """
    from media_ai.core import usage

    adapter_for("mock/mock").record(None, generated_images=1)
    usage.record_usage({"binding": "hand/written", "total_tokens": 3})
    lines = [json.loads(x) for x in _ledger.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert all(line["tool_version"] == media_ai.__version__ for line in lines)


def test_a_caller_cannot_pass_its_own_version(_ledger):
    """The sink's value wins: `{"ts", "tool_version", **entry}` would let it through."""
    from media_ai.core import usage

    usage.record_usage({"binding": "mock/mock", "tool_version": "9.9.9"})
    line = json.loads(_ledger.read_text(encoding="utf-8").splitlines()[0])
    assert line["tool_version"] == media_ai.__version__


def test_summarize_still_groups_the_way_it_did(_ledger):
    """A new field on every line must not become a new grouping key."""
    from media_ai.core import usage

    usage.record_usage({"binding": "mock/mock", "scene": "image.text_to_image", "generated_images": 2})
    totals = usage.summarize_usage(_ledger)
    assert set(totals["by_binding"]) == {"mock/mock"}
    assert set(totals["by_scene"]) == {"image.text_to_image"}


# ------------------------------------------------------------------ end to end


def test_it_reaches_the_json_a_caller_keeps(tmp_path, monkeypatch, capsys):
    """Through a real command, since `meta` is only useful if it survives to stdout."""
    pytest.importorskip("PIL")
    import sys

    from media_ai.cli import image as image_mod
    from media_ai.core.config import Config, UserBinding, render_config

    cfg = tmp_path / "config.toml"
    cfg.write_text(render_config(Config(bindings={"mock/mock": UserBinding(id="mock/mock")})), encoding="utf-8")
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(cfg))

    argv = ["media-ai image", "generate", "--binding", "mock/mock",
            "--prompt", "a red bicycle", "--output", str(tmp_path / "out.png")]
    old, sys.argv = sys.argv, argv
    try:
        assert image_mod.main() == 0
    finally:
        sys.argv = old

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["meta"]["tool_version"] == media_ai.__version__
