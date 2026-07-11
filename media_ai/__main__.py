"""Unified dispatcher: ``media-ai <group> <op> [args...]``.

Groups: ``image``, ``video``, ``speech``, ``music``, ``sound``, ``concat``, ``job``,
``capabilities``, ``usage``. Each group is also reachable directly; this umbrella
reshapes argv so the group's own argparse sees a clean program name. The eight legacy
console-scripts (``text2image`` …) remain installed separately as compatibility shims.
"""

from __future__ import annotations

import sys

from .cli import capabilities, concat, image, job, music, sound, speech, usage, video

_GROUPS = {
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


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: media-ai <group> <op> [args...]\n\ngroups:")
        for name in _GROUPS:
            print(f"  {name}")
        print("\nexamples:")
        print("  media-ai image generate --prompt 'a red bicycle' --output bike.png")
        print("  media-ai video generate --prompt 'twin suns setting' --output clip.mp4")
        print("  media-ai speech generate --text 'hello there' --output hi.mp3 --provider elevenlabs")
        print("  media-ai music generate --prompt 'lofi hip hop beat' --output song.mp3 --provider elevenlabs")
        print("  media-ai sound generate --text 'a spooky whoosh' --output sfx.mp3 --provider elevenlabs")
        print("  media-ai capabilities --provider openai")
        return 0 if argv else 2
    group, rest = argv[0], argv[1:]
    if group not in _GROUPS:
        print(f"media-ai: unknown group {group!r}. Try: {', '.join(_GROUPS)}", file=sys.stderr)
        return 2
    sys.argv = [f"media-ai {group}", *rest]
    return _GROUPS[group]()


if __name__ == "__main__":
    raise SystemExit(main())
