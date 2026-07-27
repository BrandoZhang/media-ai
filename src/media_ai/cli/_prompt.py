"""Terminal prompts for ``media-ai init`` — arrow keys, space to toggle, enter to confirm.

Nothing here touches **stdout**: the UI is drawn to ``/dev/tty`` (falling back to
stderr), because ``init`` still has to satisfy the machine contract of emitting
exactly one JSON object on stdout. Drawing to the tty directly also means the UI
still works when stdout is redirected to a file or a pipe.

The stdlib has no ready-made multiselect widget, but it has the primitives: ``termios``
to put the terminal in raw mode and ANSI escape sequences to move the cursor. That is
what this builds, in the ~200 lines it takes, rather than taking a dependency.

POSIX only — ``termios`` does not exist on Windows, which this project does not target
(CI is ubuntu-only and nothing under ``src/`` branches on platform). :func:`get_prompter`
degrades to a plain numbered-menu implementation whenever a real terminal is unavailable,
so piped and CI invocations get deterministic behaviour instead of hanging.
"""

from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from typing import Protocol, Sequence

__all__ = ["Option", "Cancelled", "Prompter", "TerminalPrompter", "FallbackPrompter", "get_prompter"]

ESC = "\x1b"
_CTRL_C = "\x03"
_CTRL_D = "\x04"

# Reserve rows for the title plus the "n more" hint lines around a scrolled viewport.
_CHROME_ROWS = 4


class Cancelled(Exception):
    """The user aborted a prompt (Ctrl-C / Ctrl-D / Esc)."""


@dataclass(frozen=True)
class Option:
    """One row in a menu. ``hint`` is dimmed trailing context, e.g. ``already exists``."""

    label: str
    hint: str = ""
    value: object = None


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


class Prompter(Protocol):
    """What ``init`` needs from a UI. Implemented for real terminals, for dumb
    streams, and (in tests) by a scripted fake — so the wizard's flow is testable
    without a tty."""

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
    """Full-fidelity prompts on a real terminal."""

    def __init__(self, tty_in, tty_out):
        self._in = tty_in
        self._out = tty_out
        self._fd = tty_in.fileno()

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

    def _viewport(self, count: int, cursor: int) -> tuple[int, int]:
        """Which slice of a long option list to show, keeping the cursor visible."""
        rows = max(3, shutil.get_terminal_size((80, 24)).lines - _CHROME_ROWS)
        if count <= rows:
            return 0, count
        start = max(0, min(cursor - rows // 2, count - rows))
        return start, start + rows

    # -- menus ---------------------------------------------------------------

    def _menu(self, title: str, options: list[Option], cursor: int, chosen: set[int] | None) -> list[int]:
        """Shared render/input loop. ``chosen is None`` means single-select."""
        width = shutil.get_terminal_size((80, 24)).columns
        drawn = 0

        def draw() -> int:
            nonlocal drawn
            if drawn:
                self._write(f"{ESC}[{drawn}A")
            lines = [_truncate(title, width)]
            start, end = self._viewport(len(options), cursor)
            if start:
                lines.append(f"  … {start} more above")
            for i in range(start, end):
                opt = options[i]
                mark = "" if chosen is None else ("◉ " if i in chosen else "◯ ")
                pointer = "❯ " if i == cursor else "  "
                body = opt.label + (f"  ({opt.hint})" if opt.hint else "")
                line = _truncate(f"{pointer}{mark}{body}", width)
                lines.append(f"{ESC}[36m{line}{ESC}[0m" if i == cursor else line)
            if end < len(options):
                lines.append(f"  … {len(options) - end} more below")
            hint = "↑↓ move · space toggle · a all · enter confirm" if chosen is not None else "↑↓ move · enter select"
            lines.append(_truncate(f"{ESC}[2m{hint}{ESC}[0m", width + 8))
            self._write("".join(f"{ESC}[2K{ln}\r\n" for ln in lines))
            drawn = len(lines)
            return drawn

        with self._raw():
            draw()
            while True:
                key = self._read_key()
                if key in (_CTRL_C, _CTRL_D, "esc"):
                    raise Cancelled
                if key in ("\r", "\n"):
                    return sorted(chosen) if chosen is not None else [cursor]
                if key == "up":
                    cursor = (cursor - 1) % len(options)
                elif key == "down":
                    cursor = (cursor + 1) % len(options)
                elif chosen is not None and key == " ":
                    chosen.symmetric_difference_update({cursor})
                elif chosen is not None and key == "a":
                    chosen.clear() if len(chosen) == len(options) else chosen.update(range(len(options)))
                draw()

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

    def text(self, title: str, *, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        self._write(f"{title}{suffix}: ")
        line = self._in.readline()
        if not line:
            raise Cancelled
        return line.decode("utf-8", "replace").strip() or default

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
        self._write(message.rstrip("\n") + "\n")


# ----------------------------------------------------------------------- fallback


class FallbackPrompter:
    """Numbered menus over plain streams, for when there is no usable terminal.

    Keeps ``init`` usable (and, more importantly, non-hanging) under a pipe, in CI,
    or anywhere ``/dev/tty`` cannot be opened.
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
