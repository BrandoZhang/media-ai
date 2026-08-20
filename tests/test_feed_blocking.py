"""The two things the published feed is allowed to *stop*.

Everything else it carries is display. These two change behaviour, and that asymmetry is
the whole security model: nobody authenticates this document, so it must never be able
to point a call somewhere — only to refuse one. `test_update.py` asserts the ceiling from
the reader's side; this file asserts the two exceptions actually work, and that both
directions of getting them wrong fail safe.

"Fail safe" points the same way in every case here: **towards not blocking**. A missed
block is a call that probably fails at the provider with a real error message. A wrong
block is a fleet locked out by a typo in a JSON file, with no way to say so.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

import media_ai
from media_ai.core import notices, update

RETIRED = {
    "binding": "mock/mock",
    "since": "2000-01-01",
    "severity": "block",
    "reason": "upstream endpoint removed",
    "alternatives": ["local/ffmpeg"],
    "fixed_in": "9.9.9",
}


def feed(**over) -> dict:
    return {"schema": 1, "latest": {"version": media_ai.__version__},
            "notices": [], "retired_bindings": [], **over}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_AI_CONFIG_FILE", str(tmp_path / "config.toml"))
    monkeypatch.delenv("CI", raising=False)
    notices.clear()
    yield
    notices.clear()


def cache(tmp_path, doc: dict) -> None:
    (tmp_path / "update-cache.json").write_text(
        json.dumps({"checked_at": time.time(), "feed": doc}), encoding="utf-8"
    )


def configured(tmp_path, monkeypatch) -> None:
    from media_ai.core.config import Config, UserBinding, render_config

    path = tmp_path / "config.toml"
    path.write_text(
        render_config(Config(bindings={
            "mock/mock": UserBinding(id="mock/mock"),
            "local/ffmpeg": UserBinding(id="local/ffmpeg"),
        })),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIA_AI_CONFIG_FILE", str(path))


def generate(*argv, expect=0, capsys) -> dict:
    """Drive a real generation command, which is where the policy is enforced."""
    from media_ai.cli import image as image_mod

    old, sys.argv = sys.argv, ["media-ai image", "generate", *argv]
    try:
        code = image_mod.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code == expect, f"{argv} -> {code}: {out}"
    return json.loads(out.strip().splitlines()[-1])


# ------------------------------------------------------------ a retired binding


def test_a_retired_binding_is_refused_before_the_call(tmp_path, monkeypatch, capsys):
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(retired_bindings=[RETIRED]))
    result = generate("--binding", "mock/mock", "--prompt", "x", "--output",
                      str(tmp_path / "x.png"), expect=3, capsys=capsys)
    assert result["error"]["code"] == "binding_retired"
    assert "upstream endpoint removed" in result["error"]["message"]
    assert result["error"]["details"]["fixed_in"] == "9.9.9"
    assert not (tmp_path / "x.png").exists()


def test_the_hint_names_an_alternative_this_machine_can_actually_call(tmp_path, monkeypatch, capsys):
    """A hint naming something unconfigured hands over a second problem as the answer
    to the first."""
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(retired_bindings=[
        {**RETIRED, "alternatives": ["never/configured", "local/ffmpeg"]},
    ]))
    result = generate("--binding", "mock/mock", "--prompt", "x", "--output",
                      str(tmp_path / "x.png"), expect=3, capsys=capsys)
    assert result["error"]["hint"] == "re-run with --binding local/ffmpeg"


def test_with_no_configured_alternative_it_says_where_to_look(tmp_path, monkeypatch, capsys):
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(retired_bindings=[{**RETIRED, "alternatives": ["never/configured"]}]))
    result = generate("--binding", "mock/mock", "--prompt", "x", "--output",
                      str(tmp_path / "x.png"), expect=3, capsys=capsys)
    assert result["error"]["hint"].endswith("bindings available")


def test_a_warning_retirement_lets_the_call_through_with_a_notice(tmp_path, monkeypatch, capsys):
    """`warn` is the tier for "going away", not "gone"."""
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(retired_bindings=[{**RETIRED, "severity": "warn"}]))
    result = generate("--binding", "mock/mock", "--prompt", "x", "--output",
                      str(tmp_path / "x.png"), capsys=capsys)
    assert result["ok"] is True
    assert [n["kind"] for n in result["notices"]] == ["binding_deprecated"]


def test_the_override_downgrades_a_block_to_a_notice(tmp_path, monkeypatch, capsys):
    """"This will probably fail" is a risk somebody may choose to take — unlike the
    version floor, which has no override at all."""
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(retired_bindings=[RETIRED]))
    result = generate("--binding", "mock/mock", "--allow-retired-binding", "--prompt", "x",
                      "--output", str(tmp_path / "x.png"), capsys=capsys)
    assert result["ok"] is True
    assert result["notices"][0]["kind"] == "binding_deprecated"


def test_another_binding_is_unaffected(tmp_path, monkeypatch, capsys):
    """The grain is one binding. A retirement is not a reason to stop the whole tool."""
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(retired_bindings=[{**RETIRED, "binding": "some/other"}]))
    assert generate("--binding", "mock/mock", "--prompt", "x", "--output",
                    str(tmp_path / "x.png"), capsys=capsys)["ok"] is True


# ---------------------------------------------------- and the ways it fails safe


@pytest.mark.parametrize(
    "entry,why",
    [
        ({**RETIRED, "since": "2999-01-01"}, "a retirement announced for the future"),
        ({**RETIRED, "since": "next tuesday"}, "a date that is not a date"),
        ({**RETIRED, "severity": "shutdown"}, "a severity this build does not know"),
        ({**RETIRED, "severity": None}, "no severity at all"),
        ("not even a table", "an entry that is not an object"),
    ],
)
def test_an_entry_it_cannot_read_blocks_nothing(entry, why, tmp_path, monkeypatch, capsys):
    """Every unreadable case points the same way: towards *not* blocking.

    A missed block is a call that fails at the provider with a real message. A wrong
    block is a fleet locked out by a typo in a JSON file — and `severity` is the sharp
    one: rounding an unknown word up to "block" would let a newer feed stop every older
    client by using a vocabulary they were never going to understand.
    """
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(retired_bindings=[entry]))
    assert generate("--binding", "mock/mock", "--prompt", "x", "--output",
                    str(tmp_path / "x.png"), capsys=capsys)["ok"] is True, why


def test_a_since_date_that_has_arrived_applies(tmp_path, monkeypatch):
    from datetime import date, timedelta

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert update.retirement_for("mock/mock", feed(retired_bindings=[{**RETIRED, "since": yesterday}]))


def test_no_cached_feed_blocks_nothing(tmp_path, monkeypatch, capsys):
    """A machine that has never fetched is not a machine to stop."""
    configured(tmp_path, monkeypatch)
    assert generate("--binding", "mock/mock", "--prompt", "x", "--output",
                    str(tmp_path / "x.png"), capsys=capsys)["ok"] is True


# --------------------------------------------------------------- a version floor


def test_a_build_below_the_floor_cannot_reach_a_provider(tmp_path, monkeypatch, capsys):
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(min_supported="9.9.9"))
    result = generate("--binding", "mock/mock", "--prompt", "x", "--output",
                      str(tmp_path / "x.png"), expect=3, capsys=capsys)
    assert result["error"]["code"] == "version_unsupported"
    assert result["error"]["details"]["min_supported"] == "9.9.9"
    assert result["error"]["hint"].endswith("upgrade")


def test_the_floor_has_no_override(tmp_path, monkeypatch, capsys):
    """A retirement is "this will probably fail"; a floor is only ever set for a
    compliance or safety reason, and a switch that waved it away would be asked for by
    exactly the person who should not have it."""
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(min_supported="9.9.9"))
    generate("--binding", "mock/mock", "--allow-retired-binding", "--prompt", "x",
             "--output", str(tmp_path / "x.png"), expect=3, capsys=capsys)


@pytest.mark.parametrize("floor", ["9.9.9", None])
def test_diagnosis_and_recovery_keep_working_under_a_floor(floor, tmp_path, monkeypatch, capsys):
    """A floor that locks somebody out of the tools for finding out why is a floor they
    answer by deleting the config directory.

    `doctor`, `version` and `upgrade` never go through `bind`, which is what makes the
    exemption structural rather than a list somebody has to maintain.
    """
    from media_ai.cli import version as version_mod

    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(**({"min_supported": floor} if floor else {})))
    old, sys.argv = sys.argv, ["media-ai version", "check", "--offline"]
    try:
        assert version_mod.main() == 0
    finally:
        sys.argv = old
    capsys.readouterr()


def test_a_floor_at_or_below_this_build_changes_nothing(tmp_path, monkeypatch, capsys):
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(min_supported=media_ai.__version__))
    assert generate("--binding", "mock/mock", "--prompt", "x", "--output",
                    str(tmp_path / "x.png"), capsys=capsys)["ok"] is True


@pytest.mark.parametrize("floor", ["not-a-version", "", 9, None, {"version": "9.9.9"}])
def test_an_unreadable_floor_is_no_floor(floor):
    """Guessing at a floor nobody can read is how a published typo becomes an outage."""
    assert update.minimum_supported(feed(min_supported=floor)) is None


def test_an_absent_floor_is_the_ordinary_state():
    assert update.minimum_supported(feed()) is None
    assert update.below_floor("0.0.1", feed()) is None


# ------------------------------------------------------------------ the ceiling


def test_neither_check_ever_reaches_the_network(tmp_path, monkeypatch, capsys):
    """A blocked call must not also be a slow one. conftest points the feed at a file
    that is not there, so a fetch would fail loudly rather than silently."""
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(retired_bindings=[RETIRED]))
    monkeypatch.setenv("MEDIA_AI_UPDATE_FEED", "https://definitely.invalid/feed.json")
    result = generate("--binding", "mock/mock", "--prompt", "x", "--output",
                      str(tmp_path / "x.png"), expect=3, capsys=capsys)
    assert result["error"]["code"] == "binding_retired"


def test_the_feed_still_cannot_point_a_call_anywhere(tmp_path, monkeypatch, capsys):
    """Blocking is the *only* behaviour it gained. A retirement entry carrying an
    endpoint must not become a redirection."""
    configured(tmp_path, monkeypatch)
    cache(tmp_path, feed(retired_bindings=[
        {**RETIRED, "severity": "warn", "base_url": "https://attacker.invalid",
         "credential": "env://STOLEN", "replace_with": {"base_url": "https://attacker.invalid"}},
    ]))
    result = generate("--binding", "mock/mock", "--prompt", "x", "--output",
                      str(tmp_path / "x.png"), capsys=capsys)
    assert result["ok"] is True
    assert "attacker.invalid" not in json.dumps(result)
