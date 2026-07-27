"""Unified dispatcher: ``media-ai <group> <op> [args...]``.

Groups: ``init``, ``doctor``, ``uninstall``, ``image``, ``video``, ``speech``,
``music``, ``sound``, ``concat``, ``job``, ``capabilities``, ``usage``. Each group is
also reachable directly; this umbrella reshapes argv so the group's own argparse sees
a clean program name.
"""

from __future__ import annotations

import sys

from .cli import capabilities, concat, doctor, image, init, job, music, sound, speech, uninstall, usage, video

_GROUPS = {
    "init": init.main,
    "doctor": doctor.main,
    "uninstall": uninstall.main,
    "image": image.main,
    "video": video.main,
    "speech": speech.main,
    "music": music.main,
    "sound": sound.main,
    "concat": concat.main,
    "job": job.main,
    "capabilities": capabilities.main,
    "usage": usage.main,
}


def _usage(stream) -> None:
    print("usage: media-ai <group> <op> [args...]\n\ngroups:", file=stream)
    for name in _GROUPS:
        print(f"  {name}", file=stream)
    print("\nexamples:", file=stream)
    print("  media-ai init                      # first-run setup: keys, models, skills", file=stream)
    print("  media-ai doctor                    # check the install offline (PATH, ffmpeg, keys, skills)", file=stream)
    print("  media-ai uninstall                 # remove the skills; keeps config unless asked", file=stream)
    print("  media-ai image generate --prompt 'a red bicycle' --output bike.png", file=stream)
    print("  media-ai video generate --prompt 'twin suns setting' --output clip.mp4", file=stream)
    print("  media-ai speech generate --text 'hello there' --output hi.mp3 --provider elevenlabs", file=stream)
    print("  media-ai music generate --prompt 'lofi hip hop beat' --output song.mp3 --provider elevenlabs", file=stream)
    print("  media-ai sound generate --text 'a spooky whoosh' --output sfx.mp3 --provider elevenlabs", file=stream)
    print("  media-ai capabilities --provider openai", file=stream)


def _usage_error(message: str) -> int:
    """Emit the failure half of the machine contract for a dispatch-level mistake.

    These were the only paths that exited non-zero with nothing parseable on stdout —
    while the Agent Skills tell callers that *every* command prints exactly one JSON
    object. An agent that mistyped a group got an empty stream and no way to find out
    why. Human detail still goes to stderr, as everywhere else.
    """
    from .cli.common import _dump
    from .core.errors import ErrorCategory, MediaError

    err = MediaError(message, category=ErrorCategory.CLI)
    print(_dump({"ok": False, "error": err.to_dict()}, False))
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
    return _GROUPS[group]()


if __name__ == "__main__":
    raise SystemExit(main())
