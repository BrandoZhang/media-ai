"""Read the YAML frontmatter of a packaged ``SKILL.md``.

``media-ai init`` needs each skill to describe itself — what it is for, whether it is
a user's choice or infrastructure the others build on — and the natural home for that
is the skill's own frontmatter, beside the ``name``/``description`` an agent already
reads. That means parsing YAML at install time.

Taking a YAML dependency for it would be out of proportion: the CLI's only runtime
deps are Pillow and ffmpeg, both for the offline mock provider, and real providers use
nothing but the stdlib. So this parses the **subset the packaged skills actually
use** — nested mappings, block sequences, flow sequences (``[a, b]``), plain and
quoted scalars, and folded/literal block scalars (``>-``, ``|``).

It is deliberately not a YAML implementation, and it is only ever pointed at files
that ship inside this package. Known departures, all fine for that input:

- blank lines and ``#`` comment lines are dropped **before** parsing, so a blank line
  inside a folded block scalar does not start a new paragraph;
- block scalars are dedented to their content, so relative indentation inside a
  literal (``|``) block is not preserved;
- sequence items are scalars; a mapping opened on a ``-`` line stays a string;
- only ``true``/``false``/``null`` are coerced — every other scalar stays a ``str``
  (which is what ``version: 1.0.0`` and every id in these files wants anyway).

Anything it cannot make sense of is skipped rather than guessed at: a malformed
SKILL.md should cost the wizard one skill's description, not the whole run.
"""

from __future__ import annotations

__all__ = ["parse"]

_BLOCK_MARKERS = (">", ">-", ">+", "|", "|-", "|+")
_SCALARS = {"true": True, "false": False, "null": None, "~": None}


def parse(text: str) -> dict:
    """Parse the leading ``---`` frontmatter block of ``text``.

    Returns ``{}`` when there is no frontmatter, when it is unterminated, or when it
    is not a mapping — every caller treats an empty result as "this skill declares
    nothing", which is the same thing it would do with a parse error.
    """
    lines = [ln for ln in _fence(text) if _is_content(ln)]
    if not lines:
        return {}
    value = _parse_map(_Cursor(lines), 0)
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- lines


def _fence(text: str) -> list[str]:
    """The raw lines between the opening and closing ``---``, or ``[]``."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    for i, line in enumerate(lines[1:], 1):
        if line.strip() in ("---", "..."):
            return lines[1:i]
    return []  # unterminated: refuse to guess where the body starts


def _is_content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


class _Cursor:
    """A read position over the frontmatter lines, shared by the parse functions."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.i = 0

    def peek(self) -> str | None:
        return self.lines[self.i] if self.i < len(self.lines) else None

    def take(self) -> str:
        line = self.lines[self.i]
        self.i += 1
        return line


# -------------------------------------------------------------------------- nodes


def _parse_map(cur: _Cursor, indent: int) -> dict:
    out: dict = {}
    while (line := cur.peek()) is not None:
        here = _indent(line)
        if here < indent:
            break
        body = line.strip()
        if body.startswith("-"):
            break  # a sequence at this level; not ours to consume
        key, sep, rest = body.partition(":")
        if not sep:
            cur.take()  # not a mapping entry — skip rather than guess
            continue
        cur.take()
        rest = rest.strip()
        if rest in _BLOCK_MARKERS:
            out[_unquote(key.strip())] = _block_scalar(cur, here, folded=rest.startswith(">"))
        elif rest:
            out[_unquote(key.strip())] = _scalar(rest)
        else:
            out[_unquote(key.strip())] = _parse_child(cur, here)
    return out


def _parse_child(cur: _Cursor, parent_indent: int):
    """The value of a ``key:`` line with nothing after the colon."""
    line = cur.peek()
    if line is None:
        return None
    here, body = _indent(line), line.strip()
    if body == "-" or body.startswith("- "):
        # A block sequence may sit at the parent's own indentation — legal YAML.
        return _parse_seq(cur, here) if here >= parent_indent else None
    return _parse_map(cur, here) if here > parent_indent else None


def _parse_seq(cur: _Cursor, indent: int) -> list:
    out: list = []
    while (line := cur.peek()) is not None:
        body = line.strip()
        if _indent(line) != indent or not (body == "-" or body.startswith("- ")):
            break
        cur.take()
        item = body[1:].strip()
        out.append(_parse_child(cur, indent) if not item else _scalar(item))
    return out


def _block_scalar(cur: _Cursor, key_indent: int, *, folded: bool) -> str:
    """Everything indented deeper than the ``key:`` line, joined."""
    parts: list[str] = []
    while (line := cur.peek()) is not None and _indent(line) > key_indent:
        parts.append(cur.take().strip())
    return (" " if folded else "\n").join(parts)


# ------------------------------------------------------------------------ scalars


def _scalar(raw: str):
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [_scalar(item) for item in _split_flow(inner)] if inner else []
    raw = _strip_comment(raw)
    if raw[:1] not in ("'", '"') and raw.lower() in _SCALARS:
        return _SCALARS[raw.lower()]
    return _unquote(raw)


def _split_flow(inner: str) -> list[str]:
    """Split ``a, "b, c"`` on commas that are not inside quotes."""
    out, buf, quote = [], [], ""
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [item for item in out if item]


def _strip_comment(raw: str) -> str:
    """Drop a trailing ``# comment`` from an unquoted scalar."""
    if raw[:1] in ("'", '"'):
        return raw
    cut = raw.find(" #")
    return raw[:cut].rstrip() if cut != -1 else raw


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    return raw
