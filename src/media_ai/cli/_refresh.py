"""The errand a detached refresh child runs: fetch the release feed, write the cache, exit.

Not a command group. It is dispatched by :mod:`media_ai.__main__` under a name that
starts with two underscores precisely so it is not one — a group is a word this CLI
invites people to type, and this takes no arguments, answers no question and has
``version check`` as the supported way to ask what it went and found out.

It is a separate process rather than a thread because of what it may cost. A fetch is
bounded at ten seconds, and the command that triggered it has already printed its JSON
object: keeping that process alive to finish an errand nobody asked for is exactly the
"why is it hanging?" that makes people turn update checks off. See
:mod:`media_ai.core.update` for the shape and why it is the one every comparable tool
converged on.

Three rules follow from being that process, and all three are about being invisible:

- **It prints nothing.** Not an empty JSON object, not an error. Its streams are on
  ``/dev/null`` before it starts, so anything written here is written into a void — but
  the intent matters more than the redirection: this is the one place in the CLI that
  is not answering a caller, so the machine contract (one JSON object on stdout) has
  nobody to serve and must not be extended to invent one.
- **It cannot fail.** Every exception is swallowed and the exit status is always ``0``.
  Nothing waits on it, so a status is a fact with no reader; and a traceback from a
  process the user did not start, surfacing in a shell's job table or a supervisor's
  log, is a bug report about a feature whose entire promise is that it costs nothing.
- **It does not go through** :func:`media_ai.cli.common.run`. That wrapper exists to
  turn a failure into the JSON error contract and a category exit code, which is the
  opposite of both rules above.
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> int:
    from .. import __version__
    from ..core import update

    try:
        update.refresh_now(__version__)
    except Exception:  # noqa: BLE001 - an errand nobody waited for cannot report anything
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
