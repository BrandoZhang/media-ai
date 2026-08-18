"""``<cli> job query|cancel`` — poll/finalize or cancel an async generation job.

Use ``query --output`` to finalize a task submitted with ``video generate
--wait false`` (downloads the finished video). ``cancel`` stops a queued task
(a way to cut cost) where the provider supports it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..brand import cli_name
from ..core.types import JobRef
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{cli_name()} job", description="Query or cancel an async job.")
    sub = ap.add_subparsers(dest="op", required=True)
    for op, help_text in (("query", "poll a job and optionally download its result"),
                          ("cancel", "cancel a queued or running job")):
        p = sub.add_parser(op, help=help_text)
        p.add_argument("--id", required=True, help="the job/task id")
        if op == "query":
            p.add_argument("--output", default=None, help="download the finished artifact here")
        common.add_global_args(p)
        common.add_call_headers(p)
    return ap


def _do(args):
    from ..core import telemetry
    from ..core.registry import build_adapter
    from ..core.resolve import resolve

    # No scene to default from — a job is identified by the binding that created it,
    # which is why a JobHandle's `poll` command names one.
    rb = common.call_headers(resolve(binding=args.binding, provider=args.provider, model=args.model), args)
    adapter = build_adapter(rb)
    ref = JobRef(provider=rb.provider.name, id=args.id, model=rb.model_id)
    # The job id goes on the span and nowhere near a metric label. It is the one field
    # that joins this process to the one that submitted the job — the trace of the
    # submit is minutes old and in another process, and the id is what a reader has to
    # search for to find it — and it is also unbounded, so as a metric label it would be
    # a new series per generation.
    with telemetry.span(f"job.{args.op}", binding=rb.id, provider=rb.provider.name,
                        job_id=args.id, wire_id=rb.model_id) as sp:
        if args.op == "cancel":
            return adapter.cancel_job(ref)
        out = Path(args.output) if getattr(args, "output", None) else None
        status = adapter.get_job(ref, output=out)
        sp.set(status=status.status)
        # No scene, here or in the event: the request that implied one belonged to
        # another process, and a guess in a cost report is worse than a missing field.
        telemetry.event(telemetry.JOB_POLLED, binding=rb.id, provider=rb.provider.name,
                        job_id=args.id, status=status.status,
                        artifacts=len(status.result.artifacts) if status.result else None)
        if status.result is not None:
            common.stamp(status.result, rb)
        return status


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
