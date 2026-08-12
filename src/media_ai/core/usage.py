"""Usage ledger (cost tracking).

Every generation appends one JSONL line to ``$MEDIA_USAGE_LOG`` (default
``./media_usage.jsonl``) so an agent harness can aggregate token/artifact cost as
an evaluation metric. Writes are best-effort (never raise) and lock-guarded so
concurrent batch generations don't interleave partial lines. Point
``MEDIA_USAGE_LOG`` and each ``--output`` at a per-task directory to isolate
concurrent runs on a shared filesystem.

Records never contain credentials (only provider names, model ids, token counts).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


def usage_log_path() -> Path:
    """Where usage lines are appended. ``$MEDIA_USAGE_LOG`` or ``./media_usage.jsonl``."""
    return Path(os.getenv("MEDIA_USAGE_LOG", "media_usage.jsonl")).expanduser()


_LEDGER_LOCK = threading.Lock()


def record_usage(entry: dict) -> None:
    """Append one usage record (JSONL). Best-effort: never raises.

    Stamped with the version that wrote it, here rather than at any caller. A ledger
    is append-only, so one file routinely holds lines written by several versions and
    can never be migrated into agreement — per-record is the only place the answer to
    "which build produced this line" can live. It is also the field that makes a
    changed accounting rule (a token count that starts including something it did not)
    readable afterwards instead of a step in the graph nobody can explain.

    ``__version__`` is imported here rather than at module scope because
    ``media_ai/__init__.py`` assigns it *after* its own imports, so a module reachable
    from that chain cannot read it at import time.
    """
    from .. import __version__

    try:
        # After the spread, not before it: the running version is the one field a
        # caller cannot be right about, so it is not theirs to pass.
        entry = {"ts": round(time.time(), 3), **entry, "tool_version": __version__}
        path = usage_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with _LEDGER_LOCK, path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001 - accounting must never break generation
        pass


def summarize_usage(path: Path | None = None) -> dict:
    """Aggregate the ledger into totals (the cost metric).

    Grouped by **binding** and by **scene** — the two things that decide what a call
    costs. Grouping by provider alone would add up two models with different prices,
    and a ledger that says "gemini: 40k tokens" cannot tell you which of them to stop
    calling. A line missing either key (a job finalized by a later process) lands
    under ``"?"`` rather than being dropped.
    """
    path = path or usage_log_path()
    totals = {
        "calls": 0,
        "images_generated": 0,
        "video_seconds": 0,
        "speech_characters": 0,
        "total_tokens": 0,
        "by_binding": {},
        "by_scene": {},
    }
    if not path.is_file():
        return totals
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        totals["calls"] += 1
        totals["images_generated"] += int(e.get("generated_images", 0) or 0)
        totals["video_seconds"] += int(e.get("seconds", 0) or 0)
        totals["speech_characters"] += int(e.get("characters", 0) or 0)
        tok = int(e.get("total_tokens", 0) or 0)
        totals["total_tokens"] += tok
        binding = e.get("binding") or "?"
        scene = e.get("scene") or "?"
        totals["by_binding"][binding] = totals["by_binding"].get(binding, 0) + tok
        totals["by_scene"][scene] = totals["by_scene"].get(scene, 0) + tok
    return totals
