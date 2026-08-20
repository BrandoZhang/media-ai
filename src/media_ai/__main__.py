"""Unified dispatcher: ``<cli> <group> <op> [args...]``.

Groups: ``init``, ``doctor``, ``upgrade``, ``uninstall``, ``image``, ``video``, ``speech``,
``music``, ``sound``, ``animation``, ``job``, ``capabilities``, ``bindings``,
``config``, ``usage``, ``version``.
Each group is also reachable directly; this umbrella reshapes argv so the group's own
argparse sees a clean program name — built from :mod:`media_ai.brand`, so ``--help``
under a renamed build spells the command the way the user has to type it.

One group per **scene group** (``video.*`` → ``<cli> video``), so a skill covering
a command group covers exactly the scenes under it — which is why joining clips is
``video concat`` and not a group of its own.
"""

from __future__ import annotations

import sys
from importlib import import_module

from .brand import cli_name

# Group -> the `cli` module implementing it, imported on dispatch rather than up front.
# `init` alone pulls in the terminal UI, skill discovery, the frontmatter parser and
# the install store; a generation command can reach none of that and should not pay
# for importing it.
_GROUPS = {
    "init": "init",
    "doctor": "doctor",
    "uninstall": "uninstall",
    "upgrade": "upgrade",
    "image": "image",
    "video": "video",
    "speech": "speech",
    "music": "music",
    "sound": "sound",
    "animation": "animation",
    "job": "job",
    "capabilities": "capabilities",
    "bindings": "bindings",
    "config": "config",
    "usage": "usage",
    "version": "version",
}

_GROUP_HELP = {
    "init": "configure credentials, bindings, and Agent Skills",
    "doctor": "check the installation and configuration offline",
    "uninstall": "remove installed Agent Skills and configuration",
    "upgrade": "install the latest published release",
    "image": "generate or edit images",
    "video": "generate videos or concatenate clips",
    "speech": "generate single-voice or dialogue speech",
    "music": "compose music or build a composition plan",
    "sound": "generate sound effects from text",
    "animation": "export an animated image (GIF, animated WebP, APNG)",
    "job": "query, download, or cancel an asynchronous job",
    "capabilities": "list binding capabilities",
    "bindings": "list or configure callable bindings",
    "config": "show or set scene defaults",
    "usage": "summarize the local usage ledger",
    "version": "report this installation, and whether a newer one is published",
}


def group_main(group: str):
    """The entry point for one group, imported now."""
    return import_module(f".cli.{_GROUPS[group]}", __package__).main


def _hidden_main(group: str):
    """The entry point for an internal, undocumented group, or ``None``.

    Deliberately not a second entry in :data:`_GROUPS`. That mapping is a contract:
    ``tests/test_scene.py`` reads it to assert that every scene group has a command and
    nothing else does, ``_usage`` prints it, and ``tests/test_cli_help.py`` requires a
    help line for each key. A background errand is none of those things — it takes no
    arguments, produces no JSON, and appearing in ``--help`` would be an invitation to
    run it.

    Reached only for a group starting with an underscore, which no real one does, so the
    ordinary path never pays for the import. The name comes from
    :mod:`media_ai.core.update`, which is also what builds the argv the child is
    launched with: one declaration, so a rename cannot leave the spawner calling a group
    the dispatcher no longer has.
    """
    from .core.update import REFRESH_COMMAND

    if group != REFRESH_COMMAND:
        return None
    return import_module(".cli._refresh", __package__).main


# ``(argv after the command name, trailing comment or "")``. Held as data rather than
# as finished lines because the command name is not a fixed width — see `_usage`.
_EXAMPLES = (
    ("init", "configure keys, bindings, and skills"),
    ("doctor", "check the install offline (PATH, ffmpeg, keys, skills)"),
    ("uninstall", "remove the skills; keeps config unless asked"),
    ("image generate --prompt 'a red bicycle' --output bike.png", ""),
    ("video generate --prompt 'twin suns setting' --output clip.mp4", ""),
    ("video concat --inputs a.mp4 b.mp4 --output film.mp4", ""),
    ("speech generate --text 'hello there' --output hi.mp3", ""),
    ("music generate --prompt 'lofi hip hop beat' --output song.mp3", ""),
    ("sound generate --text 'a spooky whoosh' --output sfx.mp3", ""),
    ("animation export --input clip.mp4 --output demo.webp --max-width 640", ""),
    ("bindings list", "what this machine can call, and what is default"),
    ("bindings available", "declared but not configured yet"),
    ("config set-default video.text_to_video volc-ark/seedance-2.0", ""),
    ("capabilities --scene video.image_to_video", ""),
    ("version check", "is a newer release published?"),
)


def _usage(stream) -> None:
    cli = cli_name()
    print(f"usage: {cli} <group> <op> [args...]\n\ngroups:", file=stream)
    for name in _GROUPS:
        print(f"  {name:<14} {_GROUP_HELP[name]}", file=stream)
    print("\nexamples:", file=stream)
    # The comment column is measured, not typed: a longer or shorter brand would
    # otherwise leave the `#`s in a ragged line down the middle of the help.
    rows = [(f"  {cli} {argv}", note) for argv, note in _EXAMPLES]
    width = max((len(line) for line, note in rows if note), default=0)
    for line, note in rows:
        print(f"{line:<{width}}  # {note}" if note else line, file=stream)


def _usage_error(message: str) -> int:
    """Emit the failure half of the machine contract for a dispatch-level mistake.

    These were the only paths that exited non-zero with nothing parseable on stdout —
    while the Agent Skills tell callers that *every* command prints exactly one JSON
    object. An agent that mistyped a group got an empty stream and no way to find out
    why. Human detail still goes to stderr, as everywhere else.
    """
    from .cli.common import _dump
    from .core.errors import ErrorCategory, MediaError
    from .core.result import error_payload

    err = MediaError(message, category=ErrorCategory.CLI)
    print(_dump(error_payload(err), False))
    print(f"{cli_name()}: {message}", file=sys.stderr)
    _usage(sys.stderr)
    return err.exit_code


def main() -> int:
    argv = sys.argv[1:]
    # --help is a request, not a mistake: human text on stdout, exit 0. Standard CLI
    # behaviour, and the same exemption cli/common.parse_args makes.
    if argv and argv[0] in ("-h", "--help"):
        _usage(sys.stdout)
        return 0
    if argv and (argv[0] in ("-V", "--version") or argv == ["version"]):
        # Same exemption: a bare version query is a request, so plain text and exit 0.
        # `version show` and `version check` are the machine-readable route — asking
        # for one is asking a different question, and only the bare word is intercepted
        # here so the group below still receives its own subcommands.
        from . import __version__

        print(f"{cli_name()} {__version__}")
        return 0
    if not argv:
        return _usage_error(f"no command given; expected: {cli_name()} <group> <op> [args...]")
    group, rest = argv[0], argv[1:]
    if group.startswith("_"):
        # Before the membership test below, so an internal errand is dispatched rather
        # than reported as an unknown group — and after everything else, so it costs a
        # `str.startswith` on the path every real command takes.
        if hidden := _hidden_main(group):
            sys.argv = [f"{cli_name()} {group}", *rest]
            return hidden()
    if group not in _GROUPS:
        return _usage_error(f"unknown group {group!r}; expected one of: {', '.join(_GROUPS)}")
    sys.argv = [f"{cli_name()} {group}", *rest]
    return group_main(group)()


if __name__ == "__main__":
    raise SystemExit(main())
