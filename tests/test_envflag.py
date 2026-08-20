"""Environment variables that mean yes or no, and the one that deliberately does not.

`bool(os.getenv("CI"))` is wrong in the single case anybody writes on purpose:
`CI=false` is how you tell a tool "I know this looks like a runner, it is not", and
every non-empty string is truthy. The variable is honoured, backwards, and only for
the person who went out of their way to set it.

The three-state read is what fixes it, and the third state is the load-bearing one —
"unset" and "set to off" have to differ wherever something *else* decides in the
absence of an answer. That is the shape of every override here.
"""

from __future__ import annotations

import pytest

from media_ai.core import update
from media_ai.core.envflag import env_flag


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", " Off "])
def test_the_spellings_of_off(monkeypatch, value):
    monkeypatch.setenv("MEDIA_TEST_FLAG", value)
    assert env_flag("MEDIA_TEST_FLAG") is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "woodpecker"])
def test_anything_else_non_empty_is_on(monkeypatch, value):
    """A runner setting `CI=woodpecker` is still a runner — the false set is closed,
    the true set is everything else."""
    monkeypatch.setenv("MEDIA_TEST_FLAG", value)
    assert env_flag("MEDIA_TEST_FLAG") is True


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_says_nothing(monkeypatch, value):
    """`CI=""` is what unsetting looks like in a shell that cannot unset. Reading it as
    *on* would be the same defect one step along."""
    monkeypatch.setenv("MEDIA_TEST_FLAG", value)
    assert env_flag("MEDIA_TEST_FLAG") is None


def test_unset_says_nothing(monkeypatch):
    monkeypatch.delenv("MEDIA_TEST_FLAG", raising=False)
    assert env_flag("MEDIA_TEST_FLAG") is None


# ------------------------------------------------------- what it is used for


def test_ci_false_is_not_ci(monkeypatch):
    """The bug this exists for. `CI=false` used to steer the wizard to the numbered
    menu and turn the update notice off — the opposite of what it says."""
    from media_ai.cli._prompt import _nobody_is_watching

    monkeypatch.setenv("CI", "false")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert _nobody_is_watching() is False


def test_media_no_tty_can_force_interactive_against_ci(monkeypatch):
    """The state that did not exist before. Someone on a runner shell, or in a dev
    container whose image sets `CI`, had no way to say "there is still somebody here":
    the variable could only ever force the fallback on, never off.
    """
    from media_ai.cli._prompt import _nobody_is_watching

    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("MEDIA_AI_NO_TTY", "0")
    assert _nobody_is_watching() is False


def test_media_no_tty_still_forces_the_fallback(monkeypatch):
    from media_ai.cli._prompt import _nobody_is_watching

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("MEDIA_AI_NO_TTY", "1")
    assert _nobody_is_watching() is True


def test_an_override_beats_a_dumb_terminal_too(monkeypatch):
    """`MEDIA_AI_NO_TTY` is the local override for the whole question, not only for `CI`."""
    from media_ai.cli._prompt import _nobody_is_watching

    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("MEDIA_AI_NO_TTY", "0")
    assert _nobody_is_watching() is False


def test_media_ascii_zero_does_not_force_ascii(monkeypatch):
    """Same defect, same fix. It cannot force *Unicode* — that would raise on a stream
    that cannot encode it — so off means "detect", which is what unset already meant.
    """
    import io

    from media_ai.cli._prompt import UNICODE, glyphs_for

    monkeypatch.setenv("MEDIA_AI_ASCII", "0")
    assert glyphs_for(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")) is UNICODE


def test_no_color_is_deliberately_not_a_flag(monkeypatch):
    """https://no-color.org asks for any non-empty value *regardless of its value*, so
    `NO_COLOR=0` disables colour. Routing it through `env_flag` for consistency would
    break a cross-tool contract to match a convention this project invented.
    """
    from media_ai.cli._prompt import _color_enabled

    monkeypatch.setenv("NO_COLOR", "0")
    assert _color_enabled(None) is False


def test_the_update_check_reads_ci_the_same_way(monkeypatch):
    """The other `CI` site — it gates the unsolicited *network* fetch. Both had the
    defect, so a fix in one would have been half of one."""
    # `conftest` now turns the check off for the whole suite (it forks a real process
    # otherwise); this test is about how `CI` is *read*, so it asks the question with
    # nothing else answering it — which is also the only state in which `CI` decides,
    # since an explicit `MEDIA_AI_UPDATE_CHECK` overrules it in both directions.
    monkeypatch.delenv("MEDIA_AI_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("CI", "false")
    assert update.should_check("0.1.0") is True
    monkeypatch.setenv("CI", "true")
    assert update.should_check("0.1.0") is False


def test_media_update_check_off_still_turns_it_off(monkeypatch):
    """The one site that already parsed properly keeps behaving, now through one reader."""
    monkeypatch.setenv("MEDIA_AI_UPDATE_CHECK", "0")
    assert update.settings().check is False
    assert update.settings_from()["check"] == "env"
