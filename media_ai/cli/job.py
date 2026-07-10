"""``media-ai job query|cancel`` — poll/finalize or cancel an async generation job.

Use ``query --output`` to finalize a task submitted with ``video generate
--wait false`` (downloads the finished video). ``cancel`` stops a queued task
(a way to cut cost) where the provider supports it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..core import registry
from ..core.types import JobRef
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai job", description="Query or cancel an async job.")
    sub = ap.add_subparsers(dest="op", required=True)
    for op in ("query", "cancel"):
        p = sub.add_parser(op)
        p.add_argument("--id", required=True, help="the job/task id")
        if op == "query":
            p.add_argument("--output", default=None, help="download the finished artifact here")
        common.add_global_args(p)
    return ap


def _do(args):
    name = common.provider_name(args) or registry.default_provider_name()
    provider = registry.get_provider(name)
    ref = JobRef(provider=name, id=args.id, model=args.model)
    if args.op == "cancel":
        return provider.cancel_job(ref)
    out = Path(args.output) if getattr(args, "output", None) else None
    return provider.get_job(ref, output=out)


def main() -> int:
    args = _build_parser().parse_args()
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
