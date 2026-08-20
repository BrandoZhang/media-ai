"""``media-ai usage`` — report accumulated generation cost from the usage ledger."""

from __future__ import annotations

import argparse
import pathlib

from ..brand import cli_name
from ..core.result import SCHEMA_VERSION
from ..core.usage import summarize_usage, usage_log_path
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{cli_name()} usage", description="Summarize generation usage/cost.")
    ap.add_argument("--log", default=None, help="ledger path (default $MEDIA_AI_USAGE_LOG)")
    ap.add_argument("--pretty", action="store_true", help="pretty-print the JSON result")
    ap.add_argument("--log-level", default=None, help="stderr log level: debug, info, warning, or error")
    ap.add_argument("--metadata-out", default=None, help="also write the secret-free result JSON to this path")
    return ap


def _do(args) -> dict:
    path = pathlib.Path(args.log) if args.log else usage_log_path()
    return {"ok": True, "schema_version": SCHEMA_VERSION, "ledger": str(path), "totals": summarize_usage(path)}


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
