"""``media-ai job query|cancel`` — poll/finalize or cancel an async generation job.

Use ``query --output`` to finalize a task submitted with ``video generate
--wait false`` (downloads the finished video). ``cancel`` stops a queued task
(a way to cut cost) where the provider supports it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

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
    from ..core.registry import build_adapter
    from ..core.resolve import resolve

    # No scene to default from — a job is identified by the binding that created it,
    # which is why a JobHandle's `poll` command names one.
    rb = resolve(binding=args.binding, provider=args.provider, model=args.model)
    adapter = build_adapter(rb)
    ref = JobRef(provider=rb.provider.name, id=args.id, model=rb.model_id)
    if args.op == "cancel":
        return adapter.cancel_job(ref)
    out = Path(args.output) if getattr(args, "output", None) else None
    status = adapter.get_job(ref, output=out)
    if status.result is not None:
        common.stamp(status.result, rb)  # no scene: the request that implied one is gone
    return status


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
