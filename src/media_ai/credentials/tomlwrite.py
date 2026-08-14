"""Minimal TOML *writer* plus the private-file write used for ``credentials.toml``.

``tomllib`` (stdlib, py311+) only reads. Rather than take a dependency to write the
two small files ``init`` generates, this emits the narrow subset they need:
a flat namespace of tables whose values are strings or lists of strings.

The subset is deliberately narrow — anything outside it raises instead of guessing,
because a credentials file that *almost* round-trips is worse than one that refuses
to be written. :func:`dumps` output is checked against ``tomllib`` in the tests.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

from ..brand import cli_name

__all__ = ["atomic_write", "dumps", "write_private", "write_public", "TomlWriteError"]


class TomlWriteError(ValueError):
    """A value outside the supported subset was handed to :func:`dumps`."""


# A bare key needs no quoting; anything else is emitted as a quoted key.
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")

# TOML's named escapes for the control characters that have them.
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _escape(value: str) -> str:
    """Escape a Python string into a TOML *basic string* body (no surrounding quotes)."""
    out = []
    for ch in value:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ch < "\x20" or ch == "\x7f":
            # Remaining C0 controls (and DEL) have no named escape; \uXXXX them.
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return "".join(out)


def _fmt_key(key: str) -> str:
    if not isinstance(key, str):
        raise TomlWriteError(f"table/key names must be str, got {type(key).__name__}")
    if not key:
        raise TomlWriteError("empty key is not writable")
    return key if _BARE_KEY.match(key) else f'"{_escape(key)}"'


def _fmt_value(value: object, *, where: str) -> str:
    if isinstance(value, str):
        return f'"{_escape(value)}"'
    if isinstance(value, bool):
        # Checked before int: bool is an int subclass and would otherwise emit 1/0.
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        if not all(isinstance(item, str) for item in value):
            raise TomlWriteError(f"{where}: only lists of strings are supported")
        inner = ", ".join(f'"{_escape(item)}"' for item in value)
        return f"[{inner}]"
    raise TomlWriteError(f"{where}: unsupported value type {type(value).__name__}")


def _emit_table(path: list[str], body: dict, lines: list[str]) -> None:
    """Emit ``[a.b.c]`` and its contents, recursing into sub-tables.

    Scalars are emitted before any sub-table header. TOML assigns every key after a
    ``[a.b]`` line to ``a.b``, so a scalar written later would silently land in the
    sub-table instead of its own — a corruption that still parses.
    """
    where = ".".join(path)
    scalars = {k: v for k, v in body.items() if not isinstance(v, dict)}
    subtables = {k: v for k, v in body.items() if isinstance(v, dict)}
    # A header for a table that holds nothing but sub-tables is noise — `[bindings]`
    # above `[bindings."volc-ark/seedance-2.0"]` declares nothing the next line does
    # not. An empty table still gets one: there it is the only evidence it exists.
    if scalars or not subtables:
        lines.append(f"[{'.'.join(_fmt_key(p) for p in path)}]")
    for key, value in scalars.items():
        lines.append(f"{_fmt_key(key)} = {_fmt_value(value, where=f'{where}.{key}')}")
    for key, sub in subtables.items():
        _emit_table([*path, key], sub, lines)


def dumps(data: dict, *, header: str | None = None) -> str:
    """Serialize nested tables to TOML.

    Values may be ``str``, ``bool``, ``int``, or ``list[str]``; tables nest to any
    depth. Top-level scalars are emitted **before** every table header — TOML assigns
    each key to the most recent header, so ``schema = 2`` written after ``[bindings]``
    would silently become ``bindings.schema``: still valid TOML, and wrong.

    ``header`` is emitted as leading ``#`` comment lines.
    """
    if not isinstance(data, dict):
        raise TomlWriteError(f"top level must be a dict of tables, got {type(data).__name__}")

    lines: list[str] = []
    if header:
        lines.extend(f"# {ln}" if ln else "#" for ln in header.splitlines())
        lines.append("")

    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for key, value in scalars.items():
        lines.append(f"{_fmt_key(key)} = {_fmt_value(value, where=key)}")
    if scalars:
        lines.append("")

    for table, body in data.items():
        if not isinstance(body, dict):
            continue
        _emit_table([table], body, lines)
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def atomic_write(path: Path, text: str, *, mode: int) -> None:
    """Write ``text`` to ``path`` with ``mode``, never exposing a wider mode in between.

    The temp file is created 0600 by ``mkstemp`` and narrowed/widened *before* any
    content is written, then renamed over the destination. Writing first and
    ``chmod``-ing after would leave a real window in which a secret sat in a
    world-readable file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{cli_name()}-", suffix=".toml")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Leave no half-written temp file behind, including on KeyboardInterrupt.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def backup(path: Path, *, mode_ceiling: int | None = None) -> Path | None:
    """Copy a config file aside before rewriting it, returning the copy (or ``None``).

    :func:`dumps` cannot round-trip comments, so the original file is the only record
    of anything hand-written in it — why a binding points at a particular endpoint, what
    a poll timeout was raised for. Every writer of a user-owned TOML file goes through
    here first, or an edit to one field silently discards the reasoning beside it.

    Created at the source file's mode from the start rather than written and then
    chmod-ed: a backup of ``credentials.toml`` holds every key in it, and the second
    order leaves them in a world-readable file for as long as the write takes — and
    permanently if the process dies in between.

    ``mode_ceiling`` is the other half of that, and it is the half inheriting the source
    mode gets wrong. A caller pairing this with :func:`write_private` is usually
    *repairing* a loose mode — that is the documented remedy for one — so inheriting it
    leaves a copy of every key at 0644 beside a file just fixed to 0600, permanently,
    as a side effect of the fix. The mode written is the tightest of the two, so a 0400
    file keeps its 0400 and a 0644 one does not hand its keys on.
    """
    if not path.is_file():
        return None
    mode = path.stat().st_mode & 0o777
    if mode_ceiling is not None:
        mode &= mode_ceiling
    for n in range(1, 1000):
        candidate = path.with_suffix(path.suffix + f".bak{'' if n == 1 else n}")
        if not candidate.exists():
            atomic_write(candidate, path.read_text(encoding="utf-8"), mode=mode)
            return candidate
    raise OSError(f"too many backups beside {path}")


def write_private(path: Path, text: str) -> None:
    """Write a secret-bearing file as 0600 inside a 0700 directory.

    ``stores._read_credentials_toml`` refuses any file with a group or world bit set,
    so 0600 is not advisory here — a looser mode makes the file unreadable to the CLI.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)  # 0700
    atomic_write(path, text, mode=0o600)


def write_public(path: Path, text: str) -> None:
    """Write a non-secret file (``config.toml``) as 0644, atomically."""
    atomic_write(path, text, mode=0o644)
