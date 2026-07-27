"""Terminal prompts for ``media-ai init`` / ``uninstall``.

Nothing here touches **stdout**: the UI is drawn to ``/dev/tty`` (falling back to
stderr), because those commands still have to satisfy the machine contract of emitting
exactly one JSON object on stdout. Drawing to the tty directly also means the UI still
works when stdout is redirected to a file or a pipe.

The look follows **clack** (``@clack/prompts``), which is the house style for setup
wizards people already recognise, and which is a set of drawing conventions rather
than a library — so it is reproducible here without a dependency:

.. code-block:: text

    ┌  media-ai setup
    │
    ◇  Which skills should be installed?
    │  media-ai-image, media-ai-video
    │
    ◆  Where should they be installed?
    │  ◼ ~/.claude/skills
    │  ◻ ./.claude/skills
    └

The parts that carry meaning, and that a hand-rolled menu tends to get wrong:

- **A continuous left rail.** ``┌`` opens the run, ``│`` connects every step, ``└``
  closes it. Answered steps stay on screen under a ``◇`` with the value beneath them,
  so the transcript reads as a record of what was chosen rather than a smear of
  overwritten frames.
- **Radio and checkbox are different controls.** ``●``/``○`` means *pick one* and
  ``◼``/``◻`` means *pick any*, so the shape of the marker tells you whether space
  does anything before you press it.
- **Colour marks state, never decoration**: cyan is where the cursor is, green is
  chosen, dim is everything not currently in play.

The stdlib has no widget toolkit, but it has the primitives: ``termios`` for raw mode
and ANSI escapes for cursor movement. POSIX only — ``termios`` does not exist on
Windows, which this project does not target (CI is ubuntu-only and nothing under
``src/`` branches on platform). :func:`get_prompter` degrades to a plain numbered-menu
implementation whenever a real terminal is unavailable, so piped and CI invocations get
deterministic behaviour instead of hanging.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
import unicodedata
from dataclasses import dataclass
from typing import Protocol, Sequence

__all__ = [
    "Option",
    "Cancelled",
    "Prompter",
    "TerminalPrompter",
    "FallbackPrompter",
    "ScriptedPrompter",
    "get_prompter",
]

ESC = "\x1b"
_CTRL_C = "\x03"
_CTRL_D = "\x04"

# Reserve rows for the step title, the rail's closing line, the key hint, and the
# "n more" markers around a scrolled viewport.
_CHROME_ROWS = 5

# How many wrapped lines of an option's `detail` to show under the highlighted row.
_DETAIL_ROWS = 3

# SGR parameters. Cyan = where the cursor is, green = chosen, dim = out of play.
_DIM, _CYAN, _GREEN, _RED = "2", "36", "32", "31"


class Cancelled(Exception):
    """The user aborted a prompt (Ctrl-C / Ctrl-D / Esc)."""


@dataclass(frozen=True)
class Option:
    """One row in a menu.

    ``hint`` is dimmed trailing context on the row itself, e.g. ``already exists``.
    ``detail`` is a longer description — shown wrapped beneath the row while it is
    highlighted, so a menu of unfamiliar names (which skill is ``media-ai-sound``?)
    can explain itself without turning into a wall of text.
    """

    label: str
    hint: str = ""
    value: object = None
    detail: str = ""


@dataclass(frozen=True)
class Glyphs:
    """The clack symbol set, with an ASCII stand-in for terminals that cannot render it.

    Falling back matters more than it looks: a terminal that cannot encode ``◆``
    raises on write, which would take down the wizard rather than degrade it.
    """

    bar_start: str
    bar: str
    bar_end: str
    step_active: str
    step_submit: str
    step_cancel: str
    radio_on: str
    radio_off: str
    check_on: str
    check_off: str
    mask: str
    ellipsis: str


UNICODE = Glyphs("┌", "│", "└", "◆", "◇", "■", "●", "○", "◼", "◻", "▪", "…")
ASCII = Glyphs("T", "|", "-", "*", "o", "x", "(*)", "( )", "[+]", "[ ]", "*", "...")


def glyphs_for(stream) -> Glyphs:
    """The richest symbol set ``stream`` can actually encode."""
    if os.getenv("MEDIA_ASCII"):
        return ASCII
    encoding = getattr(stream, "encoding", None) or sys.getdefaultencoding()
    try:
        "".join(vars(UNICODE).values()).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return ASCII
    return UNICODE


def _color_enabled() -> bool:
    # https://no-color.org — respected by enough tooling to be the expected knob.
    return not os.getenv("NO_COLOR")


def _paint(text: str, code: str) -> str:
    return text if not text or not _color_enabled() else f"{ESC}[{code}m{text}{ESC}[0m"


def _coerce(options: Sequence[Option | str]) -> list[Option]:
    return [opt if isinstance(opt, Option) else Option(label=str(opt)) for opt in options]


def _display_width(text: str) -> int:
    """Rendered column count, counting CJK/emoji as two columns.

    Truncation has to be by *columns*, not characters: the redraw walks the cursor up
    a fixed number of lines, so a single line that wraps throws off every subsequent
    frame. This is the usual cause of garbled full-screen TUIs.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _truncate(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if _display_width(text) <= limit:
        return text
    out, width = [], 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + w > limit - 1:
            break
        out.append(ch)
        width += w
    return "".join(out) + "…"


def _detail_lines(text: str, width: int, limit: int = _DETAIL_ROWS, indent: str = "") -> list[str]:
    """Wrap an option's description into rows, ellipsing what does not fit.

    Bounded on purpose: the row count is reserved before the menu is drawn, so a
    long description must not be allowed to grow the frame past what was reserved.
    """
    room = max(20, width - len(indent))
    wrapped = textwrap.wrap(" ".join(text.split()), room) or [""]
    lines = [_truncate(line, room) for line in wrapped[:limit]]
    if len(wrapped) > limit:
        lines[-1] = _truncate(lines[-1] + " …", room)
    return [indent + line for line in lines]


class Prompter(Protocol):
    """What the setup commands need from a UI. Implemented for real terminals, for
    dumb streams, and (in tests) by a scripted fake — so a wizard's flow is testable
    without a tty."""

    def intro(self, title: str) -> None: ...
    def outro(self, message: str) -> None: ...
    def select(self, title: str, options: Sequence[Option | str], *, default: int = 0) -> int: ...
    def multiselect(
        self, title: str, options: Sequence[Option | str], *, preselected: Sequence[int] = ()
    ) -> list[int]: ...
    def text(self, title: str, *, default: str = "") -> str: ...
    def secret(self, title: str) -> str: ...
    def confirm(self, title: str, *, default: bool = True) -> bool: ...
    def note(self, message: str) -> None: ...


# --------------------------------------------------------------------------- real tty


class TerminalPrompter:
    """Full-fidelity clack-style prompts on a real terminal."""

    def __init__(self, tty_in, tty_out):
        self._in = tty_in
        self._out = tty_out
        self._fd = tty_in.fileno()
        self._g = glyphs_for(tty_out)

    # -- terminal plumbing ---------------------------------------------------

    def _raw(self):
        """Context manager putting the tty in raw mode and *always* restoring it.

        A prompt that exits without restoring leaves the user's shell with no echo
        and no line editing — so the restore runs on exceptions and on Ctrl-C alike.
        """
        import contextlib
        import termios
        import tty

        @contextlib.contextmanager
        def _ctx():
            saved = termios.tcgetattr(self._fd)
            try:
                tty.setraw(self._fd)
                self._write(f"{ESC}[?25l")  # hide cursor
                yield
            finally:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, saved)
                self._write(f"{ESC}[?25h")  # show cursor

        return _ctx()

    def _write(self, text: str) -> None:
        self._out.write(text)
        self._out.flush()

    def _read_key(self) -> str:
        ch = os.read(self._fd, 1).decode("utf-8", "replace")
        if ch != ESC:
            return ch
        seq = os.read(self._fd, 2).decode("utf-8", "replace")
        return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(seq, "esc")

    def _width(self) -> int:
        return shutil.get_terminal_size((80, 24)).columns

    # -- the rail ------------------------------------------------------------

    def _rail(self, symbol: str, text: str = "", *, color: str = _DIM, body_color: str = "") -> str:
        """One rendered row: a coloured rail glyph, two spaces, then text.

        Text is truncated *before* it is coloured — an escape sequence cut in half
        leaks control bytes into the frame and, worse, is invisible to a width
        calculation, so the next redraw walks the cursor up by the wrong count.
        """
        body = _truncate(text, max(1, self._width() - _display_width(symbol) - 2))
        painted = _paint(body, body_color) if body_color else body
        return f"{_paint(symbol, color)}  {painted}" if body else _paint(symbol, color)

    def _step(self, symbol: str, title: str, color: str) -> list[str]:
        """A step's heading. Extra lines of a multi-line title hang under the rail."""
        head, *rest = title.strip("\n").split("\n") or [""]
        return [self._rail(symbol, head, color=color)] + [self._rail(self._g.bar, line.strip()) for line in rest]

    def _flush_frame(self, lines: list[str], previous: int) -> int:
        """Redraw a frame in place and report its height.

        ``[2K`` clears each line actually rewritten; the trailing ``[0J`` clears
        whatever a *taller* previous frame left below it — which happens on every
        cursor move, because the highlighted row carries a description of its own.
        """
        if previous:
            self._write(f"{ESC}[{previous}A")
        self._write("".join(f"{ESC}[2K{line}\r\n" for line in lines) + f"{ESC}[0J")
        return len(lines)

    # -- lifecycle -----------------------------------------------------------

    def intro(self, title: str) -> None:
        self._write(f"{self._rail(self._g.bar_start, title)}\r\n{self._rail(self._g.bar)}\r\n")

    def outro(self, message: str) -> None:
        lines = message.strip("\n").split("\n") or [""]
        rendered = [self._rail(self._g.bar, line) for line in lines[:-1]]
        rendered.append(self._rail(self._g.bar_end, lines[-1]))
        self._write("".join(f"{line}\r\n" for line in rendered))

    def note(self, message: str) -> None:
        lines = message.strip("\n").split("\n")
        self._write("".join(f"{self._rail(self._g.bar, line)}\r\n" for line in lines))

    def _submitted(self, title: str, value: str, previous: int) -> None:
        """Replace a finished step with its answered form: ``◇  title`` + the value."""
        lines = self._step(self._g.step_submit, title, _GREEN)
        lines.append(self._rail(self._g.bar, value, body_color=_DIM))
        lines.append(self._rail(self._g.bar))
        self._flush_frame(lines, previous)

    def _cancelled(self, title: str, previous: int) -> None:
        lines = self._step(self._g.step_cancel, title, _RED)
        lines.append(self._rail(self._g.bar, "cancelled", body_color=_DIM))
        self._flush_frame(lines, previous)

    # -- menus ---------------------------------------------------------------

    def _mark(self, *, active: bool, selected: bool, multi: bool) -> tuple[str, str]:
        """clack's four option states, as ``(glyph, label colour)``.

        Radio and checkbox are deliberately different shapes: ``●``/``○`` reads as
        "one of these", ``◼``/``◻`` as "any of these", so the control announces what
        space will do before it is pressed.
        """
        if not multi:
            return (_paint(self._g.radio_on, _GREEN), "") if active else (_paint(self._g.radio_off, _DIM), _DIM)
        if active and selected:
            return _paint(self._g.check_on, _GREEN), ""
        if active:
            return _paint(self._g.check_off, _CYAN), ""
        if selected:
            return _paint(self._g.check_on, _GREEN), _DIM
        return _paint(self._g.check_off, _DIM), _DIM

    def _option_row(self, opt: Option, *, active: bool, selected: bool, multi: bool) -> str:
        glyph, label_color = self._mark(active=active, selected=selected, multi=multi)
        raw = self._g.check_off if multi else self._g.radio_off
        body = opt.label + (f"  ({opt.hint})" if opt.hint else "")
        room = max(1, self._width() - 3 - _display_width(raw) - 1)
        painted = _paint(_truncate(body, room), label_color) if label_color else _truncate(body, room)
        return f"{_paint(self._g.bar, _DIM)}  {glyph} {painted}"

    def _viewport(self, count: int, cursor: int, *, chrome: int = _CHROME_ROWS) -> tuple[int, int]:
        """Which slice of a long option list to show, keeping the cursor visible."""
        rows = max(3, shutil.get_terminal_size((80, 24)).lines - chrome)
        if count <= rows:
            return 0, count
        start = max(0, min(cursor - rows // 2, count - rows))
        return start, start + rows

    def _menu(self, title: str, options: list[Option], cursor: int, chosen: set[int] | None) -> list[int]:
        """Shared render/input loop. ``chosen is None`` means single-select."""
        multi = chosen is not None
        # The highlighted row's description makes the frame taller, and how much
        # taller changes as the cursor moves. Reserve those rows for the whole menu so
        # the viewport stays a fixed size instead of resizing under the cursor.
        detail_rows = _DETAIL_ROWS if any(o.detail for o in options) else 0
        drawn = 0

        def draw() -> None:
            nonlocal drawn
            lines = self._step(self._g.step_active, title, _CYAN)
            start, end = self._viewport(len(options), cursor, chrome=_CHROME_ROWS + detail_rows)
            if start:
                lines.append(self._rail(self._g.bar, f"{self._g.ellipsis} {start} more above", body_color=_DIM))
            for i in range(start, end):
                opt = options[i]
                lines.append(self._option_row(opt, active=i == cursor, selected=multi and i in chosen, multi=multi))
                if i == cursor and opt.detail:
                    lines += [
                        self._rail(self._g.bar, f"  {line}", body_color=_DIM)
                        for line in _detail_lines(opt.detail, self._width() - 5, detail_rows)
                    ]
            if end < len(options):
                lines.append(
                    self._rail(self._g.bar, f"{self._g.ellipsis} {len(options) - end} more below", body_color=_DIM)
                )
            keys = "↑↓ move · space toggle · a all · enter confirm" if multi else "↑↓ move · enter select"
            lines.append(self._rail(self._g.bar, keys, body_color=_DIM))
            lines.append(self._rail(self._g.bar_end))
            drawn = self._flush_frame(lines, drawn)

        with self._raw():
            draw()
            while True:
                key = self._read_key()
                if key in (_CTRL_C, _CTRL_D, "esc"):
                    self._cancelled(title, drawn)
                    raise Cancelled
                if key in ("\r", "\n"):
                    picked = sorted(chosen) if multi else [cursor]
                    self._submitted(title, self._answer(options, picked, multi), drawn)
                    return picked
                if key == "up":
                    cursor = (cursor - 1) % len(options)
                elif key == "down":
                    cursor = (cursor + 1) % len(options)
                elif multi and key == " ":
                    chosen.symmetric_difference_update({cursor})
                elif multi and key == "a":
                    chosen.clear() if len(chosen) == len(options) else chosen.update(range(len(options)))
                draw()

    @staticmethod
    def _answer(options: list[Option], picked: list[int], multi: bool) -> str:
        if not picked:
            return "none"
        return ", ".join(options[i].label for i in picked) if multi else options[picked[0]].label

    def select(self, title: str, options: Sequence[Option | str], *, default: int = 0) -> int:
        opts = _coerce(options)
        if not opts:
            raise ValueError("select() needs at least one option")
        return self._menu(title, opts, default, None)[0]

    def multiselect(
        self, title: str, options: Sequence[Option | str], *, preselected: Sequence[int] = ()
    ) -> list[int]:
        opts = _coerce(options)
        if not opts:
            return []
        return self._menu(title, opts, 0, set(preselected))

    # -- line input ----------------------------------------------------------

    def _ask_line(self, title: str, hint: str, read) -> tuple[str, int]:
        """Draw an input step, run ``read`` for the value, and report the frame height.

        The tty is in cooked mode for this, so the user's own echo lands on the
        ``│  `` line we leave the cursor on — which is exactly where clack puts it.
        """
        lines = self._step(self._g.step_active, title, _CYAN)
        if hint:
            lines[0] += _paint(f"  {hint}", _DIM)
        self._write("".join(f"{line}\r\n" for line in lines))
        self._write(f"{_paint(self._g.bar, _DIM)}  ")
        return read(), len(lines) + 1

    def text(self, title: str, *, default: str = "") -> str:
        value, height = self._ask_line(title, f"[{default}]" if default else "", self._readline)
        answer = value or default
        # Redrawing over typed input is only safe while it fits on one line; a wrapped
        # entry occupies rows this has not counted, and rewriting would eat the wrong ones.
        if _display_width(value) + 3 < self._width():
            self._submitted(title, answer or "(empty)", height)
        return answer

    def _readline(self) -> str:
        line = self._in.readline()
        if not line:
            raise Cancelled
        return line.decode("utf-8", "replace").strip()

    def secret(self, title: str) -> str:
        import getpass

        def read() -> str:
            try:
                return getpass.getpass("", stream=self._out)
            except (EOFError, KeyboardInterrupt) as exc:
                raise Cancelled from exc

        value, height = self._ask_line(title, "input hidden", read)
        # Never echo the value, not even its length: a fixed-width mask.
        self._submitted(title, self._g.mask * 8 if value.strip() else "(empty)", height)
        return value

    def confirm(self, title: str, *, default: bool = True) -> bool:
        """A two-option radio, the way clack renders yes/no — arrows, y/n, or enter."""
        answer = default
        drawn = 0

        def draw() -> None:
            nonlocal drawn
            on, off = self._g.radio_on, self._g.radio_off
            yes = f"{_paint(on, _GREEN)} Yes" if answer else f"{_paint(off, _DIM)} {_paint('Yes', _DIM)}"
            no = f"{_paint(off, _DIM)} {_paint('No', _DIM)}" if answer else f"{_paint(on, _GREEN)} No"
            lines = self._step(self._g.step_active, title, _CYAN)
            lines.append(f"{_paint(self._g.bar, _DIM)}  {yes} / {no}")
            lines.append(self._rail(self._g.bar_end))
            drawn = self._flush_frame(lines, drawn)

        with self._raw():
            draw()
            while True:
                key = self._read_key()
                if key in (_CTRL_C, _CTRL_D, "esc"):
                    self._cancelled(title, drawn)
                    raise Cancelled
                if key in ("\r", "\n"):
                    self._submitted(title, "Yes" if answer else "No", drawn)
                    return answer
                if key in ("left", "right", "\t", " "):
                    answer = not answer
                elif key.lower() == "y":
                    answer = True
                elif key.lower() == "n":
                    answer = False
                draw()


# ----------------------------------------------------------------------- fallback


class FallbackPrompter:
    """Numbered menus over plain streams, for when there is no usable terminal.

    Keeps the setup commands usable (and, more importantly, non-hanging) under a pipe,
    in CI, or anywhere ``/dev/tty`` cannot be opened. No cursor addressing, so no rail:
    this output is read as a transcript, never redrawn.
    """

    def __init__(self, stream_in=None, stream_out=None):
        self._in = stream_in or sys.stdin
        self._out = stream_out or sys.stderr

    def _say(self, text: str) -> None:
        print(text, file=self._out, flush=True)

    def _readline(self) -> str:
        line = self._in.readline()
        if not line:
            raise Cancelled
        return line.strip()

    def _list(self, title: str, opts: list[Option]) -> None:
        self._say(title)
        for i, opt in enumerate(opts, 1):
            self._say(f"  {i}) {opt.label}" + (f"  ({opt.hint})" if opt.hint else ""))
            # No cursor to reveal them one at a time, so every description is printed
            # up front, and in full: there is no frame height to keep to.
            for line in _detail_lines(opt.detail, 76, limit=8, indent="       ") if opt.detail else ():
                self._say(line)

    def intro(self, title: str) -> None:
        self._say(title)

    def outro(self, message: str) -> None:
        self._say(message.rstrip("\n"))

    def select(self, title: str, options: Sequence[Option | str], *, default: int = 0) -> int:
        opts = _coerce(options)
        if not opts:
            raise ValueError("select() needs at least one option")
        self._list(title, opts)
        while True:
            self._say(f"Enter a number [1-{len(opts)}, default {default + 1}]:")
            raw = self._readline()
            if not raw:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(opts):
                return int(raw) - 1
            self._say("  not a valid choice")

    def multiselect(
        self, title: str, options: Sequence[Option | str], *, preselected: Sequence[int] = ()
    ) -> list[int]:
        opts = _coerce(options)
        if not opts:
            return []
        self._list(title, opts)
        default = sorted(preselected)
        shown = ",".join(str(i + 1) for i in default) or "none"
        while True:
            self._say(f"Comma-separated numbers, 'all', or blank for [{shown}]:")
            raw = self._readline()
            if not raw:
                return default
            if raw.lower() == "all":
                return list(range(len(opts)))
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            if all(p.isdigit() and 1 <= int(p) <= len(opts) for p in parts):
                return sorted({int(p) - 1 for p in parts})
            self._say("  not a valid selection")

    def text(self, title: str, *, default: str = "") -> str:
        self._say(f"{title}{f' [{default}]' if default else ''}:")
        return self._readline() or default

    def secret(self, title: str) -> str:
        import getpass

        try:
            return getpass.getpass(f"{title}: ", stream=self._out)
        except (EOFError, KeyboardInterrupt) as exc:
            raise Cancelled from exc

    def confirm(self, title: str, *, default: bool = True) -> bool:
        answer = self.text(f"{title} {'[Y/n]' if default else '[y/N]'}").lower()
        return default if not answer else answer.startswith("y")

    def note(self, message: str) -> None:
        self._say(message.rstrip("\n"))


# ------------------------------------------------------------------- scripted


class ScriptedPrompter:
    """Answers from a fixed script — lets a caller's *flow* be tested without a tty.

    Each entry is consumed by the next question in order. Running out is an error
    rather than a default, so a test that changes the number of questions fails
    loudly instead of silently taking a different path.
    """

    def __init__(self, answers: Sequence[object]):
        self._answers = list(answers)
        self.asked: list[str] = []
        self.notes: list[str] = []
        self.offered: list[list[Option]] = []  #: the menus shown, in order

    def _next(self, question: str):
        self.asked.append(question)
        if not self._answers:
            raise AssertionError(f"scripted prompter ran out of answers at: {question!r}")
        answer = self._answers.pop(0)
        if answer is Cancelled:
            raise Cancelled
        return answer

    def intro(self, title: str) -> None:
        self.notes.append(title)

    def outro(self, message: str) -> None:
        self.notes.append(message)

    def select(self, title: str, options: Sequence[Option | str], *, default: int = 0) -> int:
        self.offered.append(_coerce(options))
        return int(self._next(title))

    def multiselect(
        self, title: str, options: Sequence[Option | str], *, preselected: Sequence[int] = ()
    ) -> list[int]:
        self.offered.append(_coerce(options))
        return list(self._next(title))

    def text(self, title: str, *, default: str = "") -> str:
        value = self._next(title)
        return default if value is None else str(value)

    def secret(self, title: str) -> str:
        return str(self._next(title))

    def confirm(self, title: str, *, default: bool = True) -> bool:
        return bool(self._next(title))

    def note(self, message: str) -> None:
        self.notes.append(message)


# ------------------------------------------------------------------------ factory


def get_prompter(*, force_fallback: bool = False) -> Prompter:
    """Pick the best prompter the environment can support.

    ``/dev/tty`` rather than stdin, so the wizard still works when invoked from
    ``curl … | bash`` — where the pipe owns stdin — and when stdout is redirected.
    """
    if force_fallback or os.getenv("MEDIA_NO_TTY"):
        return FallbackPrompter()
    try:
        import termios  # noqa: F401 - POSIX check; absent on Windows
    except ModuleNotFoundError:
        return FallbackPrompter()
    try:
        tty_in = open("/dev/tty", "rb", buffering=0)
        tty_out = open("/dev/tty", "w")
        os.tcgetpgrp(tty_in.fileno())  # raises if it is not a controlling terminal
    except (OSError, ValueError):
        return FallbackPrompter()
    return TerminalPrompter(tty_in, tty_out)
