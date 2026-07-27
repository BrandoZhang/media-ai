"""Tests for the terminal prompts.

The interesting half of ``_prompt`` only runs against a real terminal, so those
tests drive it through ``pty.fork()`` with scripted keystrokes. The fallback
implementation is exercised over plain streams.
"""

from __future__ import annotations

import io
import os
import pty
import sys
import termios
import textwrap
import time

import pytest

from media_ai.cli._prompt import (
    Cancelled,
    FallbackPrompter,
    Option,
    _display_width,
    _truncate,
    get_prompter,
)

UP, DOWN, SPACE, ENTER, CTRL_C = b"\x1b[A", b"\x1b[B", b" ", b"\r", b"\x03"


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


def test_fallback_shows_hints():
    out = io.StringIO()
    FallbackPrompter(io.StringIO("1\n"), out).select("pick", [Option("a", hint="already exists")])
    assert "already exists" in out.getvalue()


# -------------------------------------------------------------------- factory


def test_get_prompter_falls_back_without_tty(monkeypatch):
    monkeypatch.setenv("MEDIA_NO_TTY", "1")
    assert isinstance(get_prompter(), FallbackPrompter)


def test_get_prompter_force_fallback():
    assert isinstance(get_prompter(force_fallback=True), FallbackPrompter)


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

    time.sleep(0.3)
    for key in keys:
        os.write(fd, key)
        time.sleep(0.12)
        try:
            os.read(fd, 65536)
        except OSError:
            break

    deadline = time.time() + timeout
    captured = b""
    os.set_blocking(r, False)
    while time.time() < deadline:
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
    try:
        os.read(fd, 65536)
    except OSError:
        pass
    os.close(r)
    os.waitpid(pid, 0)
    return captured.decode("utf-8", "replace")


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
