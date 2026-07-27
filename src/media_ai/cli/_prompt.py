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
import select
import shutil
import sys
import textwrap
import unicodedata
from dataclasses import dataclass
from typing import Protocol, Sequence

__all__ = [
    "Option",
    "Cancelled",
    "GoBack",
    "Prompter",
    "TerminalPrompter",
    "FallbackPrompter",
    "ScriptedPrompter",
    "get_prompter",
    "is_back",
    "run_steps",
]

# What a user types to step back where there are no keypresses to read — the numbered
# menus, and the line prompts even on a real terminal (they run in cooked mode, so
# there is no Esc to intercept). Deliberately not a plausible answer to any of them.
BACK_TOKENS = ("<", "b", "back")


def is_back(typed: str) -> bool:
    """Whether a typed line means "go back".

    One function so every prompt agrees: matching case-sensitively in one
    implementation and not in another made ``Back`` navigate under a pipe and become
    a literal answer on a terminal — where it would be written to disk as a path or
    an environment variable name.
    """
    return typed.strip().lower() in BACK_TOKENS


ESC = "\x1b"
_CTRL_C = "\x03"
_CTRL_D = "\x04"

_ESC_TAIL_DEFAULT = 0.15


def _esc_tail_seconds() -> float:
    """How long to wait for the rest of an escape sequence before calling it a bare Esc.

    A terminal emits the whole sequence in one write, so locally this only has to
    outlast scheduling jitter; over ssh or a loaded multiplexer the tail can arrive
    later, and an arrow key that misses the deadline reads as Esc. Hence a value well
    above local jitter, and an escape hatch for links slow enough to need more.

    Read per call and never at import: a junk ``MEDIA_ESC_DELAY`` evaluated at import
    time takes down *every* command — `__main__` imports this module for `init` — with
    a bare traceback and nothing on stdout, breaking the machine contract for the
    twelve groups that never open a prompt. A bad value is ignored instead.
    """
    try:
        return max(0.0, float(os.environ["MEDIA_ESC_DELAY"]))
    except (KeyError, ValueError):
        return _ESC_TAIL_DEFAULT

# Reserve rows for the step title, the rail's closing line, the key hint, and the
# "n more" markers around a scrolled viewport.
_CHROME_ROWS = 5

# How many wrapped lines of an option's `detail` to show under the highlighted row.
_DETAIL_ROWS = 3

# SGR parameters. Cyan = where the cursor is, green = chosen, dim = out of play.
_DIM, _CYAN, _GREEN, _RED = "2", "36", "32", "31"


class Cancelled(Exception):
    """The user aborted the whole run (Ctrl-C / Ctrl-D)."""


class GoBack(Exception):
    """The user asked to return to the previous question (Esc).

    Raised *out of* a prompt, which is what makes it work: the step driver
    (:func:`run_steps`) catches it and re-runs the previous step, so a wizard's steps
    stay ordinary straight-line functions instead of a state machine.
    """


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
    bar_h: str  #: the box drawn by `box()` — clack's `note`
    corner_tr: str
    connect_left: str
    corner_br: str


UNICODE = Glyphs("┌", "│", "└", "◆", "◇", "■", "●", "○", "◼", "◻", "▪", "…", "─", "╮", "├", "╯")
ASCII = Glyphs("T", "|", "-", "*", "o", "x", "(*)", "( )", "[+]", "[ ]", "*", "...", "-", "+", "+", "+")


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


def _truncate(text: str, limit: int, ellipsis: str = "…") -> str:
    """Cut ``text`` to ``limit`` columns.

    ``ellipsis`` is a parameter because this is the one place every rendered row
    passes through: hard-coding ``…`` here would put a character an ASCII terminal
    cannot encode into rows the glyph fallback had already made safe.
    """
    if limit <= 0:
        return ""
    if _display_width(text) <= limit:
        return text
    room = limit - _display_width(ellipsis)
    out, width = [], 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + w > room:
            break
        out.append(ch)
        width += w
    return "".join(out) + ellipsis


def _detail_lines(text: str, width: int, limit: int = _DETAIL_ROWS, indent: str = "", ellipsis: str = "…") -> list[str]:
    """Wrap an option's description into rows, ellipsing what does not fit.

    Bounded on purpose: the row count is reserved before the menu is drawn, so a
    long description must not be allowed to grow the frame past what was reserved.
    """
    room = max(20, width - len(indent))
    wrapped = textwrap.wrap(" ".join(text.split()), room) or [""]
    lines = [_truncate(line, room, ellipsis) for line in wrapped[:limit]]
    if len(wrapped) > limit:
        lines[-1] = _truncate(f"{lines[-1]} {ellipsis}", room, ellipsis)
    return [indent + line for line in lines]


class Prompter(Protocol):
    """What the setup commands need from a UI. Implemented for real terminals, for
    dumb streams, and (in tests) by a scripted fake — so a wizard's flow is testable
    without a tty."""

    #: Questions asked so far. :func:`run_steps` reads it to tell a step that asked
    #: something from one that silently did nothing, so "back" skips over the latter.
    questions: int

    def intro(self, title: str) -> None: ...
    def outro(self, message: str) -> None: ...
    def box(self, title: str, message: str) -> None: ...
    # Both are called by run_steps on whatever it is handed, so they belong to the
    # contract even though only the terminal implementation has anything to undo.
    def mark(self) -> int: ...
    def rewind(self, mark: int) -> None: ...
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
        self.questions = 0
        # How many rows below the start of the run the cursor sits. Kept exact so
        # `rewind` can un-draw whole steps when the user goes back; see `mark`.
        self._rows = 0

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
        try:
            self._out.write(text)
        except UnicodeEncodeError:
            # The glyph set covers the decoration this module draws; the *content* is
            # not ours — skill summaries, announcements, paths, provider errors — and
            # one em dash in it must not take the wizard down. TextIOWrapper encodes
            # the whole string before emitting any of it, so nothing was half-written.
            encoding = getattr(self._out, "encoding", None) or "ascii"
            self._out.write(text.encode(encoding, "replace").decode(encoding, "replace"))
        self._out.flush()

    def _read_key(self) -> str:
        ch = os.read(self._fd, 1).decode("utf-8", "replace")
        return self._escape() if ch == ESC else ch

    def _escape(self) -> str:
        """Classify an ESC byte that has already been read.

        A bare Esc and the first byte of an arrow key are the same byte. Reading the
        rest unconditionally hangs on a bare Esc until the user presses something
        else — so wait for a tail that a terminal sends in one go, and treat silence
        as the key itself.
        """
        deadline = _esc_tail_seconds()
        if not select.select([self._fd], [], [], deadline)[0]:
            return "esc"
        # The tail is two bytes, but nothing guarantees they arrive in one read — over
        # a slow link `[` lands first and `A` a moment later. Reading once would call
        # that "unknown" and then hand the `A` back as a literal keypress.
        seq = b""
        while len(seq) < 2 and select.select([self._fd], [], [], deadline)[0]:
            chunk = os.read(self._fd, 2 - len(seq))
            if not chunk:
                break
            seq += chunk
        key = {b"[A": "up", b"[B": "down", b"[C": "right", b"[D": "left"}.get(seq)
        if key:
            return key
        # Something longer or unrecognised: Home, PageUp, F-keys, Alt-chords, an arrow
        # in application-cursor mode. Drain the rest of it and report nothing rather
        # than guessing — mapping these onto Esc would silently throw away the step
        # the user is on, and leaving the tail buffered would feed `~`/`;`/`A` back as
        # keystrokes (and `a` in a multiselect means select-all).
        self._drain()
        return "unknown"

    def _drain(self) -> None:
        """Swallow whatever is left of an escape sequence already in the buffer."""
        while select.select([self._fd], [], [], 0)[0]:
            if not os.read(self._fd, 32):
                return

    def _size(self) -> os.terminal_size:
        """The size of the terminal being *drawn on*.

        `shutil.get_terminal_size()` measures stdout, which the UI never writes to —
        and which `install.sh` redirects to /dev/null. Measuring it there silently
        falls back to 80x24, so on any other size every row is built for the wrong
        width, wraps, and throws off the cursor arithmetic the redraw depends on.
        """
        try:
            size = os.get_terminal_size(self._out.fileno())
            # A pty whose size was never set answers 0x0 rather than raising, and a
            # zero width truncates every row to an ellipsis. `shutil` guards the same
            # case for stdout; this is that guard for the tty.
            if size.columns > 0 and size.lines > 0:
                return size
        except (OSError, ValueError, AttributeError):
            pass
        return shutil.get_terminal_size((80, 24))

    def _width(self) -> int:
        return self._size().columns

    # -- the rail ------------------------------------------------------------

    def _fit(self, text: str, limit: int) -> str:
        """Truncate with an ellipsis this terminal can encode."""
        return _truncate(text, limit, self._g.ellipsis)

    def _keys(self, actions: str, *, arrows: str = "up/down") -> str:
        """The key hint, in glyphs the terminal can render.

        The arrows and the separator are as much a Unicode hazard as ``◆`` is: an
        ASCII terminal that got the fallback symbol set still died on this line.
        """
        if self._g is ASCII:
            return f"[{arrows}] {actions.replace(' · ', ', ')}, [enter] confirm, [esc] back"
        glyph = "↑↓" if arrows == "up/down" else "←→"
        return f"{glyph} {actions} · enter confirm · esc back"

    def _rail(self, symbol: str, text: str = "", *, color: str = _DIM, body_color: str = "") -> str:
        """One rendered row: a coloured rail glyph, two spaces, then text.

        Text is truncated *before* it is coloured — an escape sequence cut in half
        leaks control bytes into the frame and, worse, is invisible to a width
        calculation, so the next redraw walks the cursor up by the wrong count.
        """
        body = self._fit(text, max(1, self._width() - _display_width(symbol) - 2))
        painted = _paint(body, body_color) if body_color else body
        return f"{_paint(symbol, color)}  {painted}" if body else _paint(symbol, color)

    def _step(self, symbol: str, title: str, color: str) -> list[str]:
        """A step's heading. Extra lines of a multi-line title hang under the rail."""
        head, *rest = title.strip("\n").split("\n") or [""]
        return [self._rail(symbol, head, color=color)] + [self._rail(self._g.bar, line.strip()) for line in rest]

    def _emit(self, lines: list[str]) -> None:
        """Write rows that stay on screen, keeping the row count honest."""
        self._write("".join(f"{line}\r\n" for line in lines))
        self._rows += len(lines)

    def _flush_frame(self, lines: list[str], previous: int) -> int:
        """Redraw a frame in place and report its height.

        ``[2K`` clears each line actually rewritten; the trailing ``[0J`` clears
        whatever a *taller* previous frame left below it — which happens on every
        cursor move, because the highlighted row carries a description of its own.
        """
        if previous:
            self._write(f"{ESC}[{previous}A")
        self._write("".join(f"{ESC}[2K{line}\r\n" for line in lines) + f"{ESC}[0J")
        self._rows += len(lines) - previous
        return len(lines)

    # -- rewinding -----------------------------------------------------------

    def mark(self) -> int:
        """A position in the transcript to come back to."""
        return self._rows

    def rewind(self, mark: int) -> None:
        """Un-draw everything written since ``mark``.

        This is what "go back" looks like: the step being returned to is erased along
        with the one being abandoned, so it can be asked again in place rather than
        appearing a second time under its own answer.

        Only what is still on screen can be erased — a run long enough to have
        scrolled loses the top of itself. That is cosmetic, and the alternative
        (a full-screen alternate buffer) would take the transcript with it.
        """
        delta = self._rows - mark
        if delta > 0:
            self._write(f"{ESC}[{delta}A{ESC}[0J")
            self._rows = mark

    # -- lifecycle -----------------------------------------------------------

    def intro(self, title: str) -> None:
        self._emit([self._rail(self._g.bar_start, title), self._rail(self._g.bar)])

    def outro(self, message: str) -> None:
        lines = message.strip("\n").split("\n") or [""]
        rendered = [self._rail(self._g.bar, line) for line in lines[:-1]]
        rendered.append(self._rail(self._g.bar_end, lines[-1]))
        self._emit(rendered)

    def note(self, message: str) -> None:
        self._emit([self._rail(self._g.bar, line) for line in message.strip("\n").split("\n")])

    def box(self, title: str, message: str) -> None:
        """A framed aside — clack's ``note``. For things to be read, not answered.

        Framed rather than dimmed because the one thing it is used for (an
        announcement) has to survive being skimmed past on the way to the first
        question.
        """
        g = self._g
        # Every row is `bar + 2 spaces + inner-1 columns + bar`, so the box can be at
        # most four columns narrower than the terminal. Overflowing would wrap each
        # row onto two physical lines while `_emit` counted one, and every later
        # `rewind` would then erase the wrong rows.
        room = max(16, self._width() - 4) - 3
        body = [
            line
            for para in message.strip("\n").split("\n")
            for line in _detail_lines(para, room, limit=99, ellipsis=self._g.ellipsis)
        ]
        title = self._fit(title, room)
        inner = min(max([_display_width(title) + 2, *(_display_width(line) for line in body)]) + 2, room + 1)

        def row(content: str = "") -> str:
            """Fit content to the box width, padding *or* trimming. Built by hand,
            not through ``_rail``: the closing bar is a coloured suffix, and
            truncating a string with escapes in it both corrupts the sequence and
            miscounts the width."""
            fitted = self._fit(content, inner - 1)
            return f"{_paint(g.bar, _DIM)}  {fitted}{' ' * (inner - _display_width(fitted) - 1)}{_paint(g.bar, _DIM)}"

        dashes = g.bar_h * max(1, inner - _display_width(title) - 2)
        lines = [
            f"{_paint(g.step_submit, _CYAN)}  {title} {_paint(dashes + g.corner_tr, _DIM)}",
            row(),
            *(row(line) for line in body),
            row(),
            # inner + 1: the body rows carry two leading spaces and one leading rail,
            # so the closing edge sits one column further right than `inner` alone.
            _paint(g.connect_left + g.bar_h * (inner + 1) + g.corner_br, _DIM),
        ]
        self._emit(lines)

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
        painted = _paint(self._fit(body, room), label_color) if label_color else self._fit(body, room)
        return f"{_paint(self._g.bar, _DIM)}  {glyph} {painted}"

    def _viewport(self, count: int, cursor: int, *, chrome: int = _CHROME_ROWS) -> tuple[int, int]:
        """Which slice of a long option list to show, keeping the cursor visible."""
        rows = max(3, self._size().lines - chrome)
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
                        for line in _detail_lines(opt.detail, self._width() - 5, detail_rows, ellipsis=self._g.ellipsis)
                    ]
            if end < len(options):
                lines.append(
                    self._rail(self._g.bar, f"{self._g.ellipsis} {len(options) - end} more below", body_color=_DIM)
                )
            keys = self._keys("move · space toggle · a all" if multi else "move")
            lines.append(self._rail(self._g.bar, keys, body_color=_DIM))
            lines.append(self._rail(self._g.bar_end))
            drawn = self._flush_frame(lines, drawn)

        self.questions += 1
        with self._raw():
            draw()
            while True:
                key = self._read_key()
                if key in (_CTRL_C, _CTRL_D):
                    self._cancelled(title, drawn)
                    raise Cancelled
                if key == "esc":
                    # Esc steps back rather than aborting: Ctrl-C is the abort every
                    # terminal user already reaches for, and a wizard that cannot undo
                    # a mistyped answer makes people restart the whole run. The frame
                    # is left for `rewind` to erase along with the step being returned to.
                    raise GoBack
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
        self.questions += 1
        lines = self._step(self._g.step_active, title, _CYAN)
        if hint:
            lines[0] += _paint(f"  {hint}", _DIM)
        self._emit(lines)
        self._write(f"{_paint(self._g.bar, _DIM)}  ")
        self._rows += 1  # the input row the terminal is about to echo onto
        return read(), len(lines) + 1

    def text(self, title: str, *, default: str = "") -> str:
        hint = f"[{default}]" if default else ""
        value, height = self._ask_line(title, f"{hint}  (< to go back)".strip(), self._readline)
        if is_back(value):
            raise GoBack
        answer = value or default
        # Redrawing over typed input is only safe while it fits on one line; a wrapped
        # entry occupies rows this has not counted, and rewriting would eat the wrong ones.
        if _display_width(value) + 3 < self._width():
            self._submitted(title, answer or "(empty)", height)
        return answer

    def _readline(self) -> str:
        # Cooked mode here, so there is no Esc to intercept — a typed token stands in.
        line = self._in.readline()
        if not line:
            raise Cancelled
        return line.decode("utf-8", "replace").strip()

    def secret(self, title: str) -> str:
        value, height = self._ask_line(title, "masked · esc to go back", self._read_masked)
        if is_back(value):
            raise GoBack
        # The typed value is echoed as it is typed but never *recorded*: the finished
        # step shows a fixed-width mask, so the transcript left on screen (and in any
        # scrollback) says nothing about the key, not even how long it is.
        #
        # Same wrap guard as `text()`: an OpenAI project key is ~160 characters, whose
        # mask row wraps on any normal terminal. `_ask_line` counted one row for it, so
        # rewinding over it would walk the cursor into the rows above.
        if self._masked_width(value) + 3 < self._width():
            self._submitted(title, self._g.mask * 8 if value.strip() else "(empty)", height)
        return value

    def _masked_width(self, value: str) -> int:
        return len(value) * _display_width(self._g.mask)

    def _read_masked(self) -> str:
        """Read a line, echoing one mask glyph per character.

        ``getpass`` shows nothing at all, which leaves you unable to tell a key that
        is being typed from a terminal that has stopped listening — and pasting a
        60-character key into apparent silence is exactly when you want to see
        *something*. So this echoes the way clack's password prompt does: the
        characters are hidden, the fact that you are typing is not.
        """
        data = bytearray()

        def redraw() -> None:
            shown = self._g.mask * len(data.decode("utf-8", "ignore"))
            self._write(f"\r{ESC}[2K{_paint(self._g.bar, _DIM)}  {shown}")

        try:
            with self._raw():
                self._write(f"{ESC}[?25h")  # a cursor to type at; _raw hides it by default
                while True:
                    byte = os.read(self._fd, 1)
                    if not byte or byte in (b"\x03", b"\x04"):
                        raise Cancelled
                    if byte == b"\x1b":
                        if self._escape() == "esc":
                            raise GoBack
                        continue  # arrows and friends have no meaning in a password field
                    if byte in (b"\r", b"\n"):
                        break
                    if byte in (b"\x7f", b"\x08"):  # backspace: drop a whole character,
                        text = data.decode("utf-8", "ignore")  # not one byte of one
                        data = bytearray(text[:-1].encode("utf-8"))
                    elif byte[0] >= 0x20:  # printable, or a byte of a multi-byte character
                        data += byte
                    redraw()
        finally:
            # Raw mode echoes nothing, so the newline `_ask_line` already counted has to
            # be written here — on *every* exit. Skipping it when the user pressed Esc
            # left `_rows` permanently one too high, and the next rewind then erased a
            # row of the step it was returning to.
            self._write("\r\n")
        return data.decode("utf-8", "replace")

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
            lines.append(self._rail(self._g.bar, self._keys("y/n switch", arrows="left/right"), body_color=_DIM))
            lines.append(self._rail(self._g.bar_end))
            drawn = self._flush_frame(lines, drawn)

        self.questions += 1
        with self._raw():
            draw()
            while True:
                key = self._read_key()
                if key in (_CTRL_C, _CTRL_D):
                    self._cancelled(title, drawn)
                    raise Cancelled
                if key == "esc":
                    raise GoBack
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
        self.questions = 0

    def _say(self, text: str) -> None:
        print(text, file=self._out, flush=True)

    def _readline(self) -> str:
        line = self._in.readline()
        if not line:
            raise Cancelled
        return line.strip()

    def _answer(self) -> str:
        """A line of input, with the typed back-token turned into :class:`GoBack`."""
        raw = self._readline()
        if is_back(raw):
            raise GoBack
        return raw

    def mark(self) -> int:
        return 0

    def rewind(self, mark: int) -> None:
        """Nothing to un-draw: this stream is a transcript, not a canvas."""

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

    def box(self, title: str, message: str) -> None:
        self._say(f"{title}:")
        for line in message.strip("\n").split("\n"):
            self._say(f"  {line}")

    def select(self, title: str, options: Sequence[Option | str], *, default: int = 0) -> int:
        opts = _coerce(options)
        if not opts:
            raise ValueError("select() needs at least one option")
        self.questions += 1
        self._list(title, opts)
        while True:
            self._say(f"Enter a number [1-{len(opts)}, default {default + 1}], or 'b' to go back:")
            raw = self._answer()
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
        self.questions += 1
        self._list(title, opts)
        default = sorted(preselected)
        shown = ",".join(str(i + 1) for i in default) or "none"
        while True:
            self._say(f"Comma-separated numbers, 'all', 'b' to go back, or blank for [{shown}]:")
            raw = self._answer()
            if not raw:
                return default
            if raw.lower() == "all":
                return list(range(len(opts)))
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            if all(p.isdigit() and 1 <= int(p) <= len(opts) for p in parts):
                return sorted({int(p) - 1 for p in parts})
            self._say("  not a valid selection")

    def text(self, title: str, *, default: str = "") -> str:
        self.questions += 1
        self._say(f"{title}{f' [{default}]' if default else ''}:")
        return self._answer() or default

    def secret(self, title: str) -> str:
        import getpass

        self.questions += 1
        try:
            value = getpass.getpass(f"{title}: ", stream=self._out)
        except (EOFError, KeyboardInterrupt) as exc:
            raise Cancelled from exc
        if is_back(value):
            raise GoBack
        return value

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

    @property
    def questions(self) -> int:
        return len(self.asked)

    def _next(self, question: str):
        self.asked.append(question)
        if not self._answers:
            raise AssertionError(f"scripted prompter ran out of answers at: {question!r}")
        answer = self._answers.pop(0)
        # Scripting an exception *class* as an answer is how a test drives the two
        # control-flow outcomes: `Cancelled` aborts, `GoBack` steps back.
        if answer is Cancelled:
            raise Cancelled
        if answer is GoBack:
            raise GoBack
        return answer

    def mark(self) -> int:
        return 0

    def rewind(self, mark: int) -> None:
        """No canvas to un-draw."""

    def intro(self, title: str) -> None:
        self.notes.append(title)

    def outro(self, message: str) -> None:
        self.notes.append(message)

    def box(self, title: str, message: str) -> None:
        self.notes.append(f"{title}: {message}")

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


# --------------------------------------------------------------------- step driver


def run_steps(steps: Sequence, prompter) -> None:
    """Run a wizard's steps in order, letting the user walk back through them.

    Each step is a zero-argument callable that asks its questions and records the
    answers wherever the caller wants them. Raising :class:`GoBack` from *any* prompt
    inside a step abandons it and re-runs the previous one — so the steps stay
    ordinary top-to-bottom functions, and only this loop knows about history.

    Two details make it behave the way people expect:

    - **Steps that asked nothing are skipped on the way back.** A step may decide it
      has nothing to do (``--skills-dest`` was passed, no provider needs a key).
      Stopping on one of those would look like "back" doing nothing, so the counter of
      questions asked is what decides, not the step list.
    - **Going back un-draws.** The transcript is rewound to where the target step
      began, so it is asked again in place instead of appearing a second time
      underneath its own answer.

    A step must therefore be safe to run more than once — which for a question step
    means it may not write anything. That is the same constraint that already keeps
    Ctrl-C from leaving a half-applied config, so the two hold each other up.
    """
    steps = list(steps)
    marks = [0] * len(steps)
    asked = [False] * len(steps)
    i = 0
    while i < len(steps):
        marks[i] = prompter.mark()
        before = prompter.questions
        try:
            steps[i]()
        except GoBack:
            target = next((j for j in range(i - 1, -1, -1) if asked[j]), None)
            if target is None:
                target = i  # nothing behind this step: re-ask it rather than exiting
            prompter.rewind(marks[target])
            i = target
            continue
        asked[i] = prompter.questions > before
        i += 1


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
    tty_in = tty_out = None
    try:
        tty_in = open("/dev/tty", "rb", buffering=0)
        tty_out = open("/dev/tty", "w")
        os.tcgetpgrp(tty_in.fileno())  # raises if it is not a controlling terminal
    except (OSError, ValueError):
        # With no controlling terminal both opens can still succeed and the check
        # still fail; leaving the handles behind leaks two descriptors per call.
        for handle in (tty_in, tty_out):
            if handle is not None:
                handle.close()
        return FallbackPrompter()
    return TerminalPrompter(tty_in, tty_out)
