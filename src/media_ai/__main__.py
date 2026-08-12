"""Unified dispatcher: ``media-ai <group> <op> [args...]``.

Groups: ``init``, ``doctor``, ``uninstall``, ``image``, ``video``, ``speech``,
``music``, ``sound``, ``animation``, ``job``, ``capabilities``, ``bindings``,
``config``, ``usage``.
Each group is also reachable directly; this umbrella reshapes argv so the group's own
argparse sees a clean program name.

One group per **scene group** (``video.*`` → ``media-ai video``), so a skill covering
a command group covers exactly the scenes under it — which is why joining clips is
``video concat`` and not a group of its own.
"""

from __future__ import annotations

import sys
from importlib import import_module

# Group -> the `cli` module implementing it, imported on dispatch rather than up front.
# `init` alone pulls in the terminal UI, skill discovery, the frontmatter parser and
# the install store; a generation command can reach none of that and should not pay
# for importing it.
_GROUPS = {
    "init": "init",
    "doctor": "doctor",
    "uninstall": "uninstall",
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
}

_GROUP_HELP = {
    "init": "configure credentials, bindings, and Agent Skills",
    "doctor": "check the installation and configuration offline",
    "uninstall": "remove installed Agent Skills and configuration",
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
}


def group_main(group: str):
    """The entry point for one group, imported now."""
    return import_module(f".cli.{_GROUPS[group]}", __package__).main


def _usage(stream) -> None:
    print("usage: media-ai <group> <op> [args...]\n\ngroups:", file=stream)
    for name in _GROUPS:
        print(f"  {name:<14} {_GROUP_HELP[name]}", file=stream)
    print("\nexamples:", file=stream)
    print("  media-ai init                      # configure keys, bindings, and skills", file=stream)
    print("  media-ai doctor                    # check the install offline (PATH, ffmpeg, keys, skills)", file=stream)
    print("  media-ai uninstall                 # remove the skills; keeps config unless asked", file=stream)
    print("  media-ai image generate --prompt 'a red bicycle' --output bike.png", file=stream)
    print("  media-ai video generate --prompt 'twin suns setting' --output clip.mp4", file=stream)
    print("  media-ai video concat --inputs a.mp4 b.mp4 --output film.mp4", file=stream)
    print("  media-ai speech generate --text 'hello there' --output hi.mp3", file=stream)
    print("  media-ai music generate --prompt 'lofi hip hop beat' --output song.mp3", file=stream)
    print("  media-ai sound generate --text 'a spooky whoosh' --output sfx.mp3", file=stream)
    print("  media-ai animation export --input clip.mp4 --output demo.webp --max-width 640", file=stream)
    print("  media-ai bindings list              # what this machine can call, and what is default", file=stream)
    print("  media-ai bindings available         # declared but not configured yet", file=stream)
    print("  media-ai config set-default video.text_to_video volc-ark/seedance-2.0", file=stream)
    print("  media-ai capabilities --scene video.image_to_video", file=stream)


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
    print(f"media-ai: {message}", file=sys.stderr)
    _usage(sys.stderr)
    return err.exit_code


def main() -> int:
    argv = sys.argv[1:]
    # --help is a request, not a mistake: human text on stdout, exit 0. Standard CLI
    # behaviour, and the same exemption cli/common.parse_args makes.
    if argv and argv[0] in ("-h", "--help"):
        _usage(sys.stdout)
        return 0
    if argv and argv[0] in ("-V", "--version", "version"):
        # Same exemption: a version query is a request, so plain text and exit 0.
        # `media-ai doctor` is the machine-readable route to the same number.
        from . import __version__

        print(f"media-ai {__version__}")
        return 0
    if not argv:
        return _usage_error("no command given; expected: media-ai <group> <op> [args...]")
    group, rest = argv[0], argv[1:]
    if group not in _GROUPS:
        return _usage_error(f"unknown group {group!r}; expected one of: {', '.join(_GROUPS)}")
    sys.argv = [f"media-ai {group}", *rest]
    return group_main(group)()


if __name__ == "__main__":
    raise SystemExit(main())
