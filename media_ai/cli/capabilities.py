"""``media-ai capabilities`` — machine-readable capability discovery.

An Agent Skill queries this to learn what each provider/model supports (operations,
geometry modes, options, async) *before* asking for a generation, so it can pick a
valid request instead of guessing and hitting an ``unsupported`` error.
"""

from __future__ import annotations

import argparse

from ..core import registry
from ..core.logging import get_logger
from ..core.result import SCHEMA_VERSION
from . import common


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="media-ai capabilities", description="Describe provider/model capabilities.")
    ap.add_argument("--provider", default=None, help="limit to one provider")
    ap.add_argument("--model", default=None, help="limit to one model")
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
            models = [args.model] if args.model else prov.models()
            caps = [prov.capabilities(m).to_dict() for m in models]
        except Exception as exc:  # noqa: BLE001 - a misconfigured provider shouldn't hide the rest
            get_logger().warning("could not describe provider %s: %s", name, exc)
            caps = []
        providers.append({"provider": name, "models": caps})
    return {"ok": True, "schema_version": SCHEMA_VERSION, "providers": providers}


def main() -> int:
    args = _build_parser().parse_args()
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
