"""``media-ai usage`` — report accumulated generation cost from the usage ledger."""

from __future__ import annotations

import argparse
import pathlib

from ..core.result import SCHEMA_VERSION
from ..core.usage import summarize_usage, usage_log_path
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai usage", description="Summarize generation usage/cost.")
    ap.add_argument("--log", default=None, help="ledger path (default $MEDIA_USAGE_LOG)")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--log-level", default=None)
    ap.add_argument("--metadata-out", default=None)
    return ap


def _do(args) -> dict:
    path = pathlib.Path(args.log) if args.log else usage_log_path()
    return {"ok": True, "schema_version": SCHEMA_VERSION, "ledger": str(path), "totals": summarize_usage(path)}


def main() -> int:
    args = _build_parser().parse_args()
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
