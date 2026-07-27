"""``media-ai capabilities`` — machine-readable capability discovery.

An Agent Skill queries this to learn what each provider/model supports (operations,
geometry modes, options, async) *before* asking for a generation, so it can pick a
valid request instead of guessing and hitting an ``unsupported`` error.
"""

from __future__ import annotations

import argparse

from ..core import registry
from ..core.errors import MediaError
from ..core.logging import get_logger
from ..core.result import SCHEMA_VERSION
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai capabilities", description="Describe provider/model capabilities.")
    ap.add_argument("--provider", default=None, help="limit to one provider")
    ap.add_argument("--model", default=None, help="limit to one model")
    ap.add_argument("--all-models", dest="all_models", action="store_true",
                    help="also describe deprecated/removed models normally withheld from discovery")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--log-level", default=None)
    ap.add_argument("--metadata-out", default=None)
    return ap


def _do(args) -> dict:
    names = [args.provider] if args.provider else registry.provider_names()
    providers = []
    for name in names:
        try:
            prov = registry.get_provider(name)
            models = [args.model] if args.model else (
                prov.all_models() if args.all_models else prov.models()
            )
            caps = []
            for m in models:
                try:
                    caps.append(prov.capabilities(m).to_dict())
                except MediaError as exc:
                    # A removed model refuses to describe itself. Report it as the
                    # retired entry it is rather than dropping it — knowing a model is
                    # gone, and what replaced it, is the point of asking for all of them.
                    spec = prov.catalog.get(m) if prov.catalog is not None else None
                    caps.append({
                        "provider": name, "model": m, "modalities": [],
                        "status": spec.status.value if spec else "removed",
                        "replacement": spec.replacement if spec else None,
                        "verified": spec.verified if spec else None,
                        "notes": [exc.message],
                    })
        except Exception as exc:  # noqa: BLE001 - a misconfigured provider shouldn't hide the rest
            get_logger().warning("could not describe provider %s: %s", name, exc)
            caps = []
        providers.append({"provider": name, "models": caps})
    return {"ok": True, "schema_version": SCHEMA_VERSION, "providers": providers}


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
