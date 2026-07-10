"""video_task: query or cancel an async video-generation task (volc backend).

Use this to finalize a task submitted with ``--wait false``, or to cancel a
queued task (a way to cut cost). Polling here (instead of blocking inside the
generate call) lets an agent yield its slot between checks.

Parameters:
  --op (string, required): query | cancel.
  --id (string, required): the task id (as returned by the Ark API).
  --output (string, optional): on a successful query, download the finished
      video (and last frame) to this path.
  --backend (string, optional): mock (no async tasks) or volc.
"""

import argparse
import json
import sys

from media_ai import mediakit


def main() -> int:
    ap = argparse.ArgumentParser(description="Query or cancel an async video task.")
    ap.add_argument("--op", required=True, choices=["query", "cancel"])
    ap.add_argument("--id", required=True)
    ap.add_argument("--output", default=None)
    ap.add_argument("--backend", default=None)
    args = ap.parse_args()
    try:
        out = mediakit.get_backend(args.backend).video_task(op=args.op, task_id=args.id, output=args.output)
    except mediakit.MediaError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
