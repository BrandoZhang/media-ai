"""media_usage: report accumulated generation cost from the usage ledger.

Every text2image/image2image/text2video/image2video/ref2video call appends
a line to the usage ledger ($MEDIA_USAGE_LOG, default ./media_usage.jsonl).
This tool aggregates it into totals (the cost metric). Call it to report
total token cost for the run.

Parameters:
  --log (string, optional): ledger path. Defaults to $MEDIA_USAGE_LOG.
"""

import argparse
import json
import pathlib

from media_ai import mediakit


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize media generation usage/cost.")
    ap.add_argument("--log", default=None)
    ap.add_argument("--backend", default=None)  # accepted + ignored
    args = ap.parse_args()
    path = pathlib.Path(args.log) if args.log else mediakit.usage_log_path()
    totals = mediakit.summarize_usage(path)
    print(json.dumps({"ok": True, "ledger": str(path), "totals": totals}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
