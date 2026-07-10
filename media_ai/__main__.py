"""Unified dispatcher: ``media-ai <tool> [args...]`` / ``python -m media_ai <tool> ...``.

Each tool is also installed as its own console script (``text2image``, ...);
this dispatcher is a convenience umbrella over the same ``main()`` functions.
"""

from __future__ import annotations

import sys

from media_ai.cli import (
    concat_video,
    image2image,
    image2video,
    media_usage,
    ref2video,
    text2image,
    text2video,
    video_task,
)

_COMMANDS = {
    "text2image": text2image.main,
    "image2image": image2image.main,
    "text2video": text2video.main,
    "image2video": image2video.main,
    "ref2video": ref2video.main,
    "concat_video": concat_video.main,
    "video_task": video_task.main,
    "media_usage": media_usage.main,
}


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: media-ai <command> [args...]\n\ncommands:")
        for name in _COMMANDS:
            print(f"  {name}")
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd not in _COMMANDS:
        print(f"media-ai: unknown command {cmd!r}. Try: {', '.join(_COMMANDS)}", file=sys.stderr)
        return 2
    # Re-shape argv so the subcommand's argparse sees a clean program name.
    sys.argv = [f"media-ai {cmd}", *rest]
    return _COMMANDS[cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
