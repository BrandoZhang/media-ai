"""Tests for the terminal prompts.

The interesting half of ``_prompt`` only runs against a real terminal, so those
tests drive it through ``pty.fork()`` with scripted keystrokes. The fallback
implementation is exercised over plain streams.
"""

from __future__ import annotations

import io
import json
import os
import pty
import signal
import sys
import termios
import textwrap
import time

import pytest

from media_ai.cli._prompt import (
    _DETAIL_ROWS,
    ASCII,
    UNICODE,
    Cancelled,
    FallbackPrompter,
    GoBack,
    Option,
    ScriptedPrompter,
    TerminalPrompter,
    _detail_lines,
    _display_width,
    _truncate,
    get_prompter,
    glyphs_for,
    run_steps,
)

UP, DOWN, LEFT, RIGHT = b"\x1b[A", b"\x1b[B", b"\x1b[D", b"\x1b[C"
SPACE, ENTER, CTRL_C = b" ", b"\r", b"\x03"


# --------------------------------------------------------------- width helpers


@pytest.mark.parametrize(
    "text,width",
    [("abc", 3), ("中文", 4), ("a中", 3), ("", 0), ("✅", 2)],
)
def test_display_width(text, width):
    assert _display_width(text) == width


def test_truncate_never_exceeds_limit():
    for text in ["a" * 50, "中" * 50, "mix中文abc" * 5]:
        for limit in range(1, 20):
            assert _display_width(_truncate(text, limit)) <= limit


def test_truncate_leaves_short_text_alone():
    assert _truncate("short", 20) == "short"


def test_truncate_zero_limit():
    assert _truncate("anything", 0) == ""


# ------------------------------------------------------------ option descriptions


class TestDetailLines:
    """A menu row can carry a description. It has to fit: the frame height is
    reserved before the menu is drawn, so an over-long one would push the redraw's
    cursor arithmetic out of step and garble every subsequent frame."""

    def test_short_text_is_one_line(self):
        assert _detail_lines("a short blurb", 80) == ["a short blurb"]

    def test_wraps_to_the_available_width(self):
        lines = _detail_lines("word " * 40, 60)
        assert all(_display_width(line) <= 60 for line in lines)

    def test_never_exceeds_the_reserved_row_count(self):
        for width in (20, 40, 80, 200):
            assert len(_detail_lines("word " * 200, width)) <= _DETAIL_ROWS

    def test_overflow_is_marked_rather_than_dropped_silently(self):
        assert _detail_lines("word " * 200, 40)[-1].rstrip().endswith("…")

    def test_wide_characters_are_measured_in_columns(self):
        for line in _detail_lines("中文说明" * 30, 40):
            assert _display_width(line) <= 40

    def test_existing_line_breaks_are_normalised(self):
        assert _detail_lines("two\nlines", 80) == ["two lines"]


# ------------------------------------------------------------- the clack rail


def drawing():
    """A TerminalPrompter wired to a dead input and a capture buffer.

    Enough to assert on what it *draws* without a pty; the input half is covered by
    the pty tests below.
    """
    out = io.StringIO()
    return TerminalPrompter(open(os.devnull, "rb", buffering=0), out), out


def plain(text: str) -> str:
    """Drop SGR sequences so an assertion can be about layout, not colour."""
    import re

    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


class TestRail:
    """clack's steps hang off one continuous vertical rail: ``┌`` opens the run,
    ``│`` connects every step, ``└`` closes it. Without it the prompts read as
    unrelated questions rather than one flow."""

    def test_intro_opens_the_rail(self):
        p, out = drawing()
        p.intro("media-ai setup")
        assert plain(out.getvalue()).splitlines() == ["┌  media-ai setup", "│"]

    def test_outro_closes_it(self):
        p, out = drawing()
        p.outro("Done.")
        assert plain(out.getvalue()).strip() == "└  Done."

    def test_notes_hang_off_the_rail(self):
        p, out = drawing()
        p.note("first\nsecond")
        assert plain(out.getvalue()).splitlines() == ["│  first", "│  second"]

    def test_a_blank_line_is_still_a_rail_segment(self):
        p, out = drawing()
        p.note("before\n\nafter")
        assert plain(out.getvalue()).splitlines() == ["│  before", "│", "│  after"]

    def test_a_multi_line_question_keeps_one_step_symbol(self):
        p, _out = drawing()
        lines = [plain(line) for line in p._step("◆", "the question\n  extra context", "36")]
        assert lines == ["◆  the question", "│  extra context"]


class TestControlsLookDifferent:
    """Radio and checkbox are different controls, so they must not share a glyph:
    ``●``/``○`` says pick one, ``◼``/``◻`` says pick any. The marker is the only
    thing telling a user whether space will do anything."""

    @staticmethod
    def row(*, multi, active, selected):
        p, _ = drawing()
        return plain(p._option_row(Option("thing"), active=active, selected=selected, multi=multi))

    def test_single_select_uses_radio_glyphs(self):
        assert "●" in self.row(multi=False, active=True, selected=False)
        assert "○" in self.row(multi=False, active=False, selected=False)

    def test_multiselect_uses_checkbox_glyphs(self):
        assert "◼" in self.row(multi=True, active=False, selected=True)
        assert "◻" in self.row(multi=True, active=False, selected=False)

    def test_no_glyph_is_shared_between_the_two_controls(self):
        def glyph(**kw):
            return self.row(**kw).split()[1]  # after the rail, before the label

        radio = {glyph(multi=False, active=a, selected=False) for a in (True, False)}
        checkbox = {glyph(multi=True, active=a, selected=s) for a in (True, False) for s in (True, False)}
        assert not radio & checkbox

    def test_the_cursor_row_is_the_only_coloured_one(self, monkeypatch):
        # Honour NO_COLOR in production, but do not inherit it from a test runner's
        # environment when this test is specifically asserting the coloured branch.
        monkeypatch.delenv("NO_COLOR", raising=False)
        p, _ = drawing()
        active = p._option_row(Option("thing"), active=True, selected=False, multi=True)
        inactive = p._option_row(Option("thing"), active=False, selected=False, multi=True)
        assert "\x1b[36m" in active  # cyan: this is where you are
        assert "\x1b[36m" not in inactive


class TestEverythingDrawnFitsTheTerminalsEncoding:
    """The glyph fallback is only worth having if *every* row goes through it.

    A terminal that cannot encode `◆` cannot encode `↑↓`, `·` or `…` either, and a
    single hard-coded one of those raises on write — taking down the wizard the
    fallback exists to keep running.
    """

    @staticmethod
    def ascii_prompter(monkeypatch):
        monkeypatch.setenv("MEDIA_ASCII", "1")
        out = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
        return TerminalPrompter(open(os.devnull, "rb", buffering=0), out), out

    def encodes(self, out) -> str:
        out.flush()
        return out.buffer.getvalue().decode("ascii")  # raises if anything slipped through

    def test_the_menu_frame_is_encodable(self, monkeypatch):
        p, out = self.ascii_prompter(monkeypatch)
        p._emit([p._rail(p._g.bar, p._keys("move · space toggle · a all"))])
        assert "esc" in self.encodes(out)

    def test_the_confirm_frame_is_encodable(self, monkeypatch):
        p, out = self.ascii_prompter(monkeypatch)
        p._emit([p._rail(p._g.bar, p._keys("y/n switch", arrows="left/right"))])
        assert "left/right" in self.encodes(out)

    def test_truncation_uses_an_encodable_ellipsis(self, monkeypatch):
        p, out = self.ascii_prompter(monkeypatch)
        p._emit([p._rail(p._g.bar, "x" * 200)])
        assert self.encodes(out).rstrip().endswith("...")

    def test_an_option_row_is_encodable(self, monkeypatch):
        p, out = self.ascii_prompter(monkeypatch)
        # An em dash in the detail is guaranteed: the packaged skill summaries use them.
        p._emit([p._option_row(Option("skill", hint="h", detail="a — b" * 40), active=True, selected=True, multi=True)])
        assert self.encodes(out)

    def test_the_box_is_encodable(self, monkeypatch):
        p, out = self.ascii_prompter(monkeypatch)
        p.box("Heads up", "media-ai is under rapid development — pin a version " * 4)
        assert self.encodes(out)


def test_the_escape_delay_survives_a_junk_env_var(monkeypatch):
    """Parsed per call, never at import: `__main__` imports this module for `init`, so
    a bad value evaluated at import time takes down every command with a traceback and
    nothing on stdout — breaking the machine contract for the groups that never prompt."""
    from media_ai.cli._prompt import _ESC_TAIL_DEFAULT, _esc_tail_seconds

    monkeypatch.setenv("MEDIA_ESC_DELAY", "not-a-number")
    assert _esc_tail_seconds() == _ESC_TAIL_DEFAULT
    monkeypatch.setenv("MEDIA_ESC_DELAY", "0.4")
    assert _esc_tail_seconds() == 0.4


def test_a_junk_escape_delay_does_not_break_an_unrelated_command(monkeypatch, tmp_path):
    import subprocess

    res = subprocess.run(
        [sys.executable, "-m", "media_ai", "image", "generate", "--provider", "mock",
         "--prompt", "hi", "--output", str(tmp_path / "x.png")],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "MEDIA_ESC_DELAY": "abc", "MEDIA_USAGE_LOG": str(tmp_path / "u.jsonl")},
    )
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["ok"] is True


def test_the_terminal_size_comes_from_the_tty_not_stdout(monkeypatch):
    """`install.sh` redirects stdout to /dev/null; measuring it there silently gives
    80x24, and every row is then built for the wrong width and wraps."""
    p, _out = drawing()
    monkeypatch.setattr(os, "get_terminal_size", lambda fd: os.terminal_size((37, 11)))
    assert p._width() == 37


def test_a_pty_with_no_size_falls_back_instead_of_truncating_everything(monkeypatch):
    p, _out = drawing()
    monkeypatch.setattr(os, "get_terminal_size", lambda fd: os.terminal_size((0, 0)))
    assert p._width() >= 20


class TestGlyphFallbacks:
    def test_ascii_terminals_get_an_ascii_set(self):
        assert glyphs_for(io.TextIOWrapper(io.BytesIO(), encoding="ascii")) is ASCII

    def test_utf8_terminals_get_the_real_symbols(self):
        assert glyphs_for(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")) is UNICODE

    def test_a_narrow_terminal_never_overflows_the_box(self, monkeypatch):
        """An overflowing row wraps onto two physical lines while `_emit` counts one,
        and every later `rewind` then erases the wrong rows."""
        import shutil as sh

        for columns in (20, 40, 80):
            monkeypatch.setattr(sh, "get_terminal_size", lambda *_a, c=columns: os.terminal_size((c, 24)))
            p, out = drawing()
            p.box("Heads up", "a message long enough to need wrapping at any of these widths " * 3)
            for line in plain(out.getvalue()).splitlines():
                assert _display_width(line) <= columns, f"{columns}-column box emitted a {len(line)}-column row"

    def test_the_fallback_can_be_forced(self, monkeypatch):
        monkeypatch.setenv("MEDIA_ASCII", "1")
        assert glyphs_for(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")) is ASCII

    def test_ascii_and_unicode_sets_cover_the_same_glyphs(self):
        assert set(vars(ASCII)) == set(vars(UNICODE))

    def test_box_frames_its_message(self):
        p, out = drawing()
        p.box("Heads up", "read this")
        lines = plain(out.getvalue()).splitlines()
        assert lines[0].startswith("◇  Heads up ") and lines[0].endswith("╮")
        assert any("read this" in line for line in lines)
        assert lines[-1].startswith("├") and lines[-1].endswith("╯")

    def test_box_rows_are_all_the_same_width(self):
        """A ragged right edge is the tell that the padding maths went wrong."""
        p, out = drawing()
        p.box("Heads up", "one short line\nand a considerably longer second line to wrap the box")
        widths = {_display_width(line) for line in plain(out.getvalue()).splitlines()}
        assert len(widths) == 1

    def test_no_color_is_honoured(self, monkeypatch):
        """https://no-color.org — and it keeps a piped transcript readable."""
        monkeypatch.setenv("NO_COLOR", "1")
        p, out = drawing()
        p.intro("setup")
        assert "\x1b" not in out.getvalue()


# ---------------------------------------------------------------- going back


class Recorder(ScriptedPrompter):
    """A scripted prompter that also remembers the rewinds asked of it."""

    def __init__(self, answers):
        super().__init__(answers)
        self._row = 0
        self.rewound: list[int] = []

    def mark(self) -> int:
        self._row += 10  # each step "draws" something, so marks differ
        return self._row

    def rewind(self, mark: int) -> None:
        self.rewound.append(mark)


class TestRunSteps:
    """`run_steps` is what turns one exception into "go back a question"."""

    @staticmethod
    def asking(name, calls, prompter):
        def step():
            calls.append(name)
            prompter.select(name, ["x"])

        return step

    def test_back_returns_to_the_previous_question(self):
        p = ScriptedPrompter([0, GoBack, 1, 2])
        calls = []
        run_steps([self.asking("a", calls, p), self.asking("b", calls, p)], p)
        assert calls == ["a", "b", "a", "b"]

    def test_a_step_that_asked_nothing_is_skipped_on_the_way_back(self):
        """Landing on a step that has nothing to ask would look like "back" doing
        nothing — which is how flag-driven steps (`--skills-dest`) behave."""
        p = ScriptedPrompter([0, GoBack, 1, 2])
        calls = []
        steps = [
            self.asking("a", calls, p),
            lambda: calls.append("silent"),
            self.asking("c", calls, p),
        ]
        run_steps(steps, p)
        assert calls == ["a", "silent", "c", "a", "silent", "c"]

    def test_back_at_the_first_question_re_asks_it(self):
        """There is nothing behind it, and exiting the wizard is what Ctrl-C is for."""
        p = ScriptedPrompter([GoBack, 0])
        calls = []
        run_steps([self.asking("a", calls, p)], p)
        assert calls == ["a", "a"]

    def test_the_transcript_is_rewound_to_the_step_returned_to(self):
        """Without this the re-asked question appears a second time, underneath the
        answer it is supposed to be replacing."""
        p = Recorder([0, GoBack, 1, 2])
        calls = []
        run_steps([self.asking("a", calls, p), self.asking("b", calls, p)], p)
        assert p.rewound == [10], "should rewind to where step 'a' began"

    def test_a_cancel_is_not_caught(self):
        p = ScriptedPrompter([Cancelled])
        with pytest.raises(Cancelled):
            run_steps([self.asking("a", [], p)], p)

    def test_it_only_needs_what_the_protocol_promises(self):
        """`run_steps` calls mark/rewind on whatever it is handed, so they are part of
        the contract — not just something the three built-in prompters happen to have."""
        from media_ai.cli._prompt import Prompter

        for name in ("mark", "rewind", "questions"):
            assert name in Prompter.__annotations__ or hasattr(Prompter, name), f"Prompter is missing {name}"

    def test_steps_that_never_go_back_run_once_each(self):
        p = ScriptedPrompter([0, 0, 0])
        calls = []
        run_steps([self.asking(n, calls, p) for n in "abc"], p)
        assert calls == ["a", "b", "c"]


# ------------------------------------------------------------------- fallback


def fallback(script: str) -> FallbackPrompter:
    return FallbackPrompter(io.StringIO(script), io.StringIO())


def test_fallback_select_by_number():
    assert fallback("2\n").select("pick", ["a", "b", "c"]) == 1


def test_fallback_select_blank_takes_default():
    assert fallback("\n").select("pick", ["a", "b", "c"], default=2) == 2


def test_fallback_select_reprompts_on_garbage():
    assert fallback("nope\n9\n1\n").select("pick", ["a", "b"]) == 0


def test_fallback_multiselect_csv():
    assert fallback("1,3\n").multiselect("pick", ["a", "b", "c"]) == [0, 2]


def test_fallback_multiselect_all():
    assert fallback("all\n").multiselect("pick", ["a", "b", "c"]) == [0, 1, 2]


def test_fallback_multiselect_blank_takes_preselected():
    assert fallback("\n").multiselect("pick", ["a", "b", "c"], preselected=[1]) == [1]


def test_fallback_multiselect_empty_options():
    assert fallback("").multiselect("pick", []) == []


def test_fallback_text_default():
    assert fallback("\n").text("name", default="fallback") == "fallback"


def test_fallback_confirm_default_true():
    assert fallback("\n").confirm("ok?", default=True) is True


def test_fallback_confirm_explicit_no():
    assert fallback("n\n").confirm("ok?", default=True) is False


def test_fallback_eof_cancels():
    """A closed stream must raise, not spin forever."""
    with pytest.raises(Cancelled):
        fallback("").select("pick", ["a", "b"])


@pytest.mark.parametrize("typed", ["b", "back", "<", "B"])
def test_fallback_back_token(typed):
    """No keypresses to intercept here, so going back is a word you can type."""
    with pytest.raises(GoBack):
        fallback(f"{typed}\n").select("pick", ["a", "b"])


@pytest.mark.parametrize("typed", ["b", "B", " Back ", "<"])
def test_the_back_token_means_the_same_on_every_prompter(typed):
    """It was matched case-sensitively on a terminal and lowercased in the fallback,
    so `Back` navigated under a pipe and became a literal answer on a tty — where it
    would be written to disk as a path or an environment variable name."""
    from media_ai.cli._prompt import is_back

    assert is_back(typed)


@pytest.mark.parametrize("typed", ["", "beta", "b.md", "banana"])
def test_ordinary_answers_are_not_mistaken_for_it(typed):
    from media_ai.cli._prompt import is_back

    assert not is_back(typed)


def test_fallback_back_token_in_a_multiselect():
    with pytest.raises(GoBack):
        fallback("b\n").multiselect("pick", ["a", "b"])


def test_fallback_offers_the_back_token():
    out = io.StringIO()
    FallbackPrompter(io.StringIO("1\n"), out).select("pick", ["a"])
    assert "go back" in out.getvalue()


def test_fallback_shows_hints():
    out = io.StringIO()
    FallbackPrompter(io.StringIO("1\n"), out).select("pick", [Option("a", hint="already exists")])
    assert "already exists" in out.getvalue()


def test_fallback_shows_every_description():
    """No cursor to reveal them one at a time, so all of them print up front."""
    out = io.StringIO()
    options = [Option("a", detail="what a does"), Option("b", detail="what b does")]
    FallbackPrompter(io.StringIO("1\n"), out).select("pick", options)
    assert "what a does" in out.getvalue() and "what b does" in out.getvalue()


# -------------------------------------------------------------------- factory


def test_get_prompter_falls_back_without_tty(monkeypatch):
    monkeypatch.setenv("MEDIA_NO_TTY", "1")
    assert isinstance(get_prompter(), FallbackPrompter)


def test_get_prompter_force_fallback():
    assert isinstance(get_prompter(force_fallback=True), FallbackPrompter)


def test_get_prompter_uses_tty_stdio_when_dev_tty_is_unavailable(monkeypatch):
    """Some sandboxes deny /dev/tty but leave stdin/stderr attached to a pty."""
    class Tty:
        encoding = "utf-8"
        buffer = None

        def __init__(self):
            self.buffer = self

        def isatty(self):
            return True

        def fileno(self):
            return 0

    stdin, stderr = Tty(), Tty()
    monkeypatch.setattr("builtins.open", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("denied")))
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stderr", stderr)
    assert isinstance(get_prompter(), TerminalPrompter)


# ------------------------------------------------------- real terminal, via pty


def run_in_pty(body: str, keys: list[bytes], timeout: float = 5.0) -> str:
    """Run ``body`` in a child with a real controlling terminal, feeding ``keys``.

    Returns the child's stdout, which is redirected to a pipe — so these tests also
    assert that the UI never leaks escape sequences onto stdout.
    """
    r, w = os.pipe()
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.close(r)
        os.dup2(w, 1)
        src = "import sys\nsys.path[:0] = %r\n" % (sys.path,) + textwrap.dedent(body)
        os.execv(sys.executable, [sys.executable, "-c", src])
    os.close(w)
    os.set_blocking(fd, False)

    def drain():
        """Discard whatever the child has drawn. Never blocks; errors are the child
        having exited, which the caller notices via ``r``."""
        try:
            os.read(fd, 65536)
        except OSError:  # BlockingIOError is an OSError — nothing to read, or gone
            pass

    try:
        time.sleep(0.3)
        for key in keys:
            os.write(fd, key)
            time.sleep(0.12)
            drain()

        deadline = time.time() + timeout
        captured = b""
        os.set_blocking(r, False)
        while time.time() < deadline:
            # Keep draining the master while waiting. A prompt that redraws more than
            # the pty buffer holds after the last key would otherwise block in write(2)
            # and never reach its result, turning a real UI regression into an opaque
            # timeout-and-SIGKILL.
            drain()
            try:
                chunk = os.read(r, 65536)
                if not chunk:
                    break
                captured += chunk
            except BlockingIOError:
                time.sleep(0.05)
            except OSError:
                break
            if b"\n" in captured:
                break
        os.close(r)
        # The capture loop above is deliberately bounded.  Honour that bound when
        # reaping the pty child too: otherwise a broken interaction turns one failed
        # assertion into a permanently hung test run.
        for _ in range(10):
            reaped, _status = os.waitpid(pid, os.WNOHANG)
            if reaped:
                break
            time.sleep(0.05)
        else:
            # This is a test-child cleanup path after its deadline has already expired.
            # SIGKILL guarantees a stuck raw-mode process cannot leave the suite waiting.
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        return captured.decode("utf-8", "replace")
    finally:
        # One master fd per call, and this helper runs once per prompt test — leaking
        # them exhausts the process limit partway through a full run.
        os.close(fd)


SELECT_BODY = """
    from media_ai.cli._prompt import get_prompter
    p = get_prompter()
    i = p.select("pick one", ["alpha", "beta", "gamma"])
    print("RESULT=%d" % i)
"""

MULTI_BODY = """
    from media_ai.cli._prompt import get_prompter
    p = get_prompter()
    picked = p.multiselect("pick many", ["alpha", "beta", "gamma", "delta"], preselected=[0])
    print("RESULT=%s" % ",".join(map(str, picked)))
"""


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_select_arrow_keys():
    out = run_in_pty(SELECT_BODY, [DOWN, DOWN, ENTER])
    assert "RESULT=2" in out


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_select_wraps_around():
    """Up from the first option lands on the last."""
    out = run_in_pty(SELECT_BODY, [UP, ENTER])
    assert "RESULT=2" in out


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_multiselect_space_toggles():
    out = run_in_pty(MULTI_BODY, [DOWN, DOWN, SPACE, ENTER])
    assert "RESULT=0,2" in out


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_multiselect_space_deselects():
    out = run_in_pty(MULTI_BODY, [SPACE, ENTER])
    assert "RESULT=" in out and "RESULT=0" not in out


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_multiselect_a_selects_all():
    out = run_in_pty(MULTI_BODY, [b"a", ENTER])
    assert "RESULT=0,1,2,3" in out


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_ui_never_writes_to_stdout():
    """The machine contract: stdout carries the result and nothing else.

    The child draws a full menu to /dev/tty while stdout goes to a pipe; a single
    escape byte on stdout would mean the JSON line is no longer parseable.
    """
    out = run_in_pty(MULTI_BODY, [DOWN, SPACE, ENTER])
    assert "\x1b" not in out, f"escape sequences leaked to stdout: {out!r}"
    assert out.strip().startswith("RESULT=")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_escape_goes_back_without_waiting_for_more_input():
    """Esc and the first byte of an arrow key are the same byte.

    Reading the rest of the sequence unconditionally makes a bare Esc hang until the
    user happens to press something else — so this asserts it comes back on its own.
    """
    body = """
        from media_ai.cli._prompt import get_prompter, GoBack
        p = get_prompter()
        try:
            p.select("pick", ["alpha", "beta"])
            print("RESULT=no-back")
        except GoBack:
            print("RESULT=back")
    """
    assert "RESULT=back" in run_in_pty(body, [b"\x1b"])


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_arrow_keys_still_arrive_whole():
    """The other half of that bargain: the Esc-tail wait must not split an arrow key."""
    assert "RESULT=1" in run_in_pty(SELECT_BODY, [DOWN, ENTER])


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
@pytest.mark.parametrize(
    "sequence",
    [
        pytest.param(b"\x1b[H", id="home"),
        pytest.param(b"\x1b[5~", id="pageup"),
        pytest.param(b"\x1bOP", id="f1"),
        pytest.param(b"\x1b[1;5A", id="ctrl-up"),
    ],
)
def test_pty_an_unrecognised_escape_sequence_is_ignored(sequence):
    """Only four sequences are decoded, and everything else used to fall through to
    "esc" — which now means "go back", so Home would silently discard the step. The
    tail also has to be swallowed: a leftover `a` in a multiselect means select-all."""
    out = run_in_pty(MULTI_BODY, [sequence, ENTER])
    assert "RESULT=0" in out, f"{sequence!r} did not leave the selection alone"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_escape_goes_back_from_a_confirm():
    body = """
        from media_ai.cli._prompt import get_prompter, GoBack
        p = get_prompter()
        try:
            p.confirm("sure?")
            print("RESULT=no-back")
        except GoBack:
            print("RESULT=back")
    """
    assert "RESULT=back" in run_in_pty(body, [b"\x1b"])


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_ctrl_c_still_cancels_rather_than_going_back():
    body = """
        from media_ai.cli._prompt import get_prompter, Cancelled, GoBack
        p = get_prompter()
        try:
            p.select("pick", ["a", "b"])
            print("RESULT=none")
        except GoBack:
            print("RESULT=back")
        except Cancelled:
            print("RESULT=cancelled")
    """
    assert "RESULT=cancelled" in run_in_pty(body, [CTRL_C])


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_ctrl_c_cancels_and_restores_terminal():
    """Ctrl-C must raise Cancelled *and* leave the terminal out of raw mode."""
    body = """
        import sys, termios
        from media_ai.cli._prompt import get_prompter, Cancelled
        tty_file = open("/dev/tty")   # keep the reference: fileno() alone would be GC'd shut
        fd = tty_file.fileno()
        before = termios.tcgetattr(fd)
        p = get_prompter()
        try:
            p.select("pick", ["a", "b"])
            print("RESULT=no-cancel")
        except Cancelled:
            after = termios.tcgetattr(fd)
            print("RESULT=cancelled restored=%s" % (before == after))
    """
    out = run_in_pty(body, [CTRL_C])
    assert "RESULT=cancelled restored=True" in out


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_long_list_scrolls_without_crashing():
    body = """
        from media_ai.cli._prompt import get_prompter
        p = get_prompter()
        i = p.select("many", ["opt-%02d" % n for n in range(60)])
        print("RESULT=%d" % i)
    """
    out = run_in_pty(body, [DOWN] * 5 + [ENTER])
    assert "RESULT=5" in out


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_wide_chars_do_not_break_redraw():
    body = """
        from media_ai.cli._prompt import get_prompter, Option
        p = get_prompter()
        opts = [Option("中文选项一", hint="已存在"), Option("中文选项二"), Option("mixed 混合 abc")]
        i = p.select("选择", opts)
        print("RESULT=%d" % i)
    """
    out = run_in_pty(body, [DOWN, ENTER])
    assert "RESULT=1" in out


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_descriptions_do_not_break_the_redraw():
    """The description belongs to the highlighted row, so the frame changes height as
    the cursor moves. If the redraw did not account for that, the returned index would
    drift from the row the user was looking at."""
    body = """
        from media_ai.cli._prompt import get_prompter, Option
        p = get_prompter()
        opts = [
            Option("alpha", detail="one line"),
            Option("beta", detail="a much longer description " * 8),
            Option("gamma", detail=""),
        ]
        i = p.select("pick one", opts)
        print("RESULT=%d" % i)
    """
    assert "RESULT=2" in run_in_pty(body, [DOWN, DOWN, ENTER])


CONFIRM_BODY = """
    from media_ai.cli._prompt import get_prompter
    p = get_prompter()
    print("RESULT=%s" % p.confirm("Remove it?", default=True))
"""


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_confirm_takes_the_default_on_enter():
    assert "RESULT=True" in run_in_pty(CONFIRM_BODY, [ENTER])


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_confirm_toggles_with_arrows():
    """clack draws yes/no as a two-option radio, not a y/n text field."""
    assert "RESULT=False" in run_in_pty(CONFIRM_BODY, [RIGHT, ENTER])
    assert "RESULT=True" in run_in_pty(CONFIRM_BODY, [LEFT, LEFT, ENTER])


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_confirm_accepts_the_letter_keys():
    assert "RESULT=False" in run_in_pty(CONFIRM_BODY, [b"n", ENTER])


SECRET_BODY = """
    from media_ai.cli._prompt import get_prompter, GoBack
    p = get_prompter()
    try:
        print("RESULT=%s" % p.secret("API key"))
    except GoBack:
        print("RESULT=back")
"""


class TestMaskedSecretInput:
    """`getpass` shows nothing at all, which leaves you unable to tell a key being
    typed from a terminal that has stopped listening. clack echoes a mask; so do we."""

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
    def test_the_value_is_read_correctly(self):
        assert "RESULT=sk-abc123" in run_in_pty(SECRET_BODY, [b"sk-abc123", ENTER])

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
    def test_the_characters_are_never_echoed(self):
        drawn = run_in_pty_watching_the_terminal(SECRET_BODY, [b"sk-secret", ENTER])
        # Everything the prompt drew, before the child prints the value it read back.
        assert "sk-secret" not in drawn.split("RESULT=")[0]

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
    def test_one_mask_glyph_per_character(self):
        drawn = plain(run_in_pty_watching_the_terminal(SECRET_BODY, [b"abcd", ENTER]))
        assert "▪▪▪▪" in drawn, "typing showed nothing back"

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
    def test_backspace_removes_a_character(self):
        assert "RESULT=ab" in run_in_pty(SECRET_BODY, [b"abc", b"\x7f", ENTER])

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
    def test_escape_goes_back(self):
        assert "RESULT=back" in run_in_pty(SECRET_BODY, [b"abc", b"\x1b"])

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
    def test_the_finished_step_records_a_fixed_width_mask(self):
        """The transcript (and the scrollback) must not leak the key's length."""
        drawn = plain(run_in_pty_watching_the_terminal(SECRET_BODY, [b"abc", ENTER]))
        assert "◇  API key" in drawn
        assert "▪" * 8 in drawn.split("◇  API key")[-1]


def run_in_pty_watching_the_terminal(body: str, keys: list[bytes]) -> str:
    """Like ``run_in_pty`` but returns what was drawn *to the terminal*.

    The other helper deliberately watches stdout, to prove the UI stays off it. This
    one watches the tty, which is the only place the frames exist.
    """
    pid, fd = pty.fork()
    if pid == 0:  # child
        src = "import sys\nsys.path[:0] = %r\n" % (sys.path,) + textwrap.dedent(body)
        os.execv(sys.executable, [sys.executable, "-c", src])
    os.set_blocking(fd, False)
    captured, deadline, sent = b"", time.time() + 4.0, False
    while time.time() < deadline:
        try:
            captured += os.read(fd, 65536)
        except (BlockingIOError, OSError):
            pass
        if not sent and time.time() > deadline - 3.0:
            for key in keys:
                os.write(fd, key)
                time.sleep(0.12)
            sent = True
        time.sleep(0.05)
    os.waitpid(pid, os.WNOHANG)
    return captured.decode("utf-8", "replace")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_pty_an_answered_step_stays_on_screen_as_a_record():
    """clack replaces the live prompt with ``◇  question`` + the answer, so the
    finished run reads as a transcript of what was chosen."""
    drawn = plain(run_in_pty_watching_the_terminal(SELECT_BODY, [DOWN, ENTER]))
    assert "◆  pick one" in drawn, "no active step was drawn"
    assert "◇  pick one" in drawn, "the answered step was not redrawn"
    assert "beta" in drawn.split("◇  pick one")[-1], "the chosen value was not kept"


def test_non_tty_child_does_not_hang():
    """With stdin closed and no controlling terminal, get_prompter must degrade."""
    import subprocess

    code = textwrap.dedent(
        """
        from media_ai.cli._prompt import get_prompter, FallbackPrompter, Cancelled
        p = get_prompter()
        print("FALLBACK=%s" % isinstance(p, FallbackPrompter))
        try:
            p.select("pick", ["a", "b"])
            print("UNEXPECTED")
        except Cancelled:
            print("CANCELLED")
        """
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        start_new_session=True,  # detach from the test runner's controlling terminal
    )
    assert "FALLBACK=True" in res.stdout
    assert "CANCELLED" in res.stdout


def test_termios_import_is_present_on_this_platform():
    """Guard the POSIX-only assumption stated in the module docstring."""
    assert termios is not None
