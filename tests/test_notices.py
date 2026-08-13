"""``notices[]`` — telling the caller something that is not the answer it asked for.

"Your installed skills are older than this CLI" is not a result and not an error. It
belongs to the installation, and the call it arrives with succeeded on its own terms.
But the party that can act on it is the agent driving this CLI, and an agent reads
stdout — a line on stderr goes nowhere whenever the harness captures stderr and shows
the model only the JSON.

So the questions worth asking are: does it reach stdout on *every* exit path, does it
stay out of the way when there is nothing to say, and can it never be the reason a
command fails?
"""

from __future__ import annotations

import json
import sys

import pytest

import media_ai
from media_ai.cli import common
from media_ai.core import notices


@pytest.fixture(autouse=True)
def _clean_notices():
    """Each test gets the real sources plus whatever it registers, and no leakage."""
    saved = list(notices._SOURCES)
    notices.reset()
    yield
    notices._SOURCES[:] = saved
    notices.reset()


@pytest.fixture
def one_notice():
    notices.register_source(
        lambda: [notices.Notice(kind="skills_stale", message="something drifted", action="do-the-thing")]
    )


class _Args:
    pretty = False
    metadata_out = None


# --------------------------------------------------------------- the shape


def test_a_notice_kind_outside_the_closed_set_is_refused():
    """`kind` is what a program branches on, so it has to be enumerable.

    Refused at construction rather than at emission: a typo should fail in the test
    that produces it, not quietly become a value no consumer can match.
    """
    with pytest.raises(ValueError, match="unknown notice kind"):
        notices.Notice(kind="skills_staleee", message="…")


def test_every_declared_kind_is_lowercase_snake_case():
    """They appear in agent-facing JSON beside `error.code`, which reads the same way."""
    assert all(k.replace("_", "").isalnum() and k.islower() for k in notices.KINDS)


def test_an_action_free_notice_omits_the_key_rather_than_nulling_it():
    """An absent field is absent — the rule the whole config layer follows."""
    assert "action" not in notices.Notice(kind="skills_stale", message="…").to_dict()


# ------------------------------------------------------------- when it is quiet


def test_nothing_to_say_means_no_key_at_all(tmp_path):
    """Every payload would otherwise grow an empty list forever."""
    assert "notices" not in json.loads(common._dump({"ok": True}, False))


def test_a_source_that_raises_is_not_a_failed_command():
    """Accounting for the installation must never break the thing being accounted."""

    def broken():
        raise RuntimeError("the receipt is on fire")

    notices.register_source(broken)
    assert json.loads(common._dump({"ok": True}, False)) == {"ok": True}


def test_sources_are_consulted_once_per_process():
    """A source may touch the filesystem, and `--metadata-out` renders twice."""
    calls = []

    def counted():
        calls.append(1)
        return []

    notices.register_source(counted)
    common._dump({"ok": True}, False)
    common._dump({"ok": True}, False)
    assert calls == [1]


# --------------------------------------------------------- when it has something


def test_it_rides_on_a_successful_payload(one_notice):
    payload = json.loads(common._dump({"ok": True, "schema_version": 2}, False))
    assert payload["notices"] == [
        {"kind": "skills_stale", "severity": "info", "message": "something drifted", "action": "do-the-thing"}
    ]


def test_it_rides_on_a_failure_payload_too(one_notice):
    """A failure is when a caller is least able to work out what is wrong on its own."""
    from media_ai.core.errors import ErrorCategory, MediaError
    from media_ai.core.result import error_payload

    payload = json.loads(common._dump(error_payload(MediaError("no", category=ErrorCategory.CLI)), False))
    assert payload["ok"] is False and payload["notices"]


def test_it_survives_an_argparse_rejection(one_notice, capsys):
    """The path where no command body runs — and the one that matters most.

    Following out-of-date skill text looks exactly like this from here: a flag this
    build does not have, exit 2, and nothing else to explain why.
    """
    import argparse

    with pytest.raises(SystemExit) as ei:
        common.parse_args(argparse.ArgumentParser(), ["--no-such-flag"])
    assert ei.value.code == 2
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["notices"][0]["kind"] == "skills_stale"


def test_it_survives_an_unknown_command_group(one_notice, capsys):
    """The dispatcher builds its own failure object without going through `emit`."""
    from media_ai.__main__ import _usage_error

    assert _usage_error("no such group") == 2
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert payload["notices"][0]["kind"] == "skills_stale"


def test_the_payload_is_not_mutated_by_rendering_it(one_notice):
    """`emit` renders the same object twice when `--metadata-out` is given."""
    obj = {"ok": True}
    common._dump(obj, False)
    assert obj == {"ok": True}


def test_a_notice_is_redacted_like_everything_else_on_stdout():
    from media_ai.credentials.secret import Secret

    Secret("sk-notice-leak-abcdef123456", provider="mock", source="env")
    notices.register_source(
        lambda: [notices.Notice(kind="skills_stale", message="saw sk-notice-leak-abcdef123456 go by")]
    )
    assert "sk-notice-leak-abcdef123456" not in common._dump({"ok": True}, False)


# ------------------------------------------------------- the first real source


def receipt(tmp_path, monkeypatch, version: str):
    """An install receipt claiming a destination was written by ``version``."""
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "config.toml"))
    (tmp_path / "installed-skills.toml").write_text(
        f'[dests."{tmp_path}/skills"]\nskills = ["x"]\nversion = "{version}"\n', encoding="utf-8"
    )


def test_skills_from_this_build_say_nothing(tmp_path, monkeypatch):
    receipt(tmp_path, monkeypatch, media_ai.__version__)
    assert list(common._skills_from_another_build()) == []


def test_skills_from_another_build_are_reported_with_a_runnable_fix(tmp_path, monkeypatch):
    receipt(tmp_path, monkeypatch, "0.0.1")
    (found,) = list(common._skills_from_another_build())
    assert found.kind == "skills_stale" and found.severity == "warn"
    assert media_ai.__version__ in found.message
    assert found.action.endswith("init --skills-only")


def test_no_receipt_at_all_says_nothing(tmp_path, monkeypatch):
    """A hand-copied install, or none — neither is drift, and neither is ours to nag about."""
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "config.toml"))
    assert list(common._skills_from_another_build()) == []


def test_the_fix_follows_a_renamed_build(tmp_path, monkeypatch):
    """`action` is contractual — an agent runs it verbatim — so it cannot say `media-ai`
    in a build called something else."""
    from media_ai import brand

    monkeypatch.setattr(brand, "CLI_NAME", "other-tool")
    receipt(tmp_path, monkeypatch, "0.0.1")
    (found,) = list(common._skills_from_another_build())
    assert found.action.startswith("other-tool ")


# ------------------------------------------------------------------ end to end


def test_it_reaches_a_real_command(tmp_path, monkeypatch, capsys):
    """Through a whole command, since a notice nobody receives is not a notice."""
    from media_ai.cli import bindings as bindings_mod

    receipt(tmp_path, monkeypatch, "0.0.1")
    argv = ["media-ai bindings", "list"]
    old, sys.argv = sys.argv, argv
    try:
        bindings_mod.main()
    finally:
        sys.argv = old
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["notices"][0]["kind"] == "skills_stale"
