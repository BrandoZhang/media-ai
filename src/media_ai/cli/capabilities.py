"""``media-ai capabilities`` — machine-readable capability discovery.

An Agent Skill queries this to learn what each provider/model supports (operations,
geometry modes, options, async) *before* asking for a generation, so it can pick a
valid request instead of guessing and hitting an ``unsupported`` error.
"""

from __future__ import annotations

import argparse

from ..core import registry
from ..core.capabilities import ModelCapabilities
from ..core.errors import MediaError
from ..core.modelspec import ModelStatus
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


def _retired_entry(provider: str, model: str, prov) -> dict:
    """A listing entry for a model that refuses to describe itself.

    Carries the same keys as a real entry — agents parse ``providers[].models[]`` as
    one uniform shape, and a short dict would make a retired model look like a
    malformed one.
    """
    spec = prov.catalog.get(model) if prov.catalog is not None else None
    caps = ModelCapabilities(
        provider=provider, model=model, modalities=frozenset(),
        status=spec.status.value if spec else ModelStatus.REMOVED.value,
        replacement=spec.replacement if spec else None,
        verified=spec.verified if spec else None,
        notes=(spec.notes + spec.lifecycle_notes()) if spec else (),
    )
    return caps.to_dict()


def _describe(name: str, args) -> list[dict]:
    """Describe one provider's models, surviving a misconfigured adapter."""
    try:
        prov = registry.get_provider(name)
        models = prov.all_models() if args.all_models else prov.models()
        caps = []
        for m in models:
            try:
                caps.append(prov.capabilities(m).to_dict())
            except MediaError:
                # A listing substitutes a stub for a model that refuses to describe
                # itself: seeing what is gone, and what replaced it, is the point of
                # asking for all of them.
                caps.append(_retired_entry(name, m, prov))
        return caps
    except Exception as exc:  # noqa: BLE001 - a misconfigured provider shouldn't hide the rest
        get_logger().warning("could not describe provider %s: %s", name, exc)
        return []


def _do(args) -> dict:
    names = [args.provider] if args.provider else registry.provider_names()

    if args.provider and args.model:
        # An unambiguous question about one model gets an unambiguous answer, errors
        # included: naming a retired model must stay exit 3, not become ok:true with a
        # stub. The resilience below exists to stop one broken adapter hiding the
        # others, which is not what is being asked here.
        prov = registry.get_provider(args.provider)
        return {
            "ok": True, "schema_version": SCHEMA_VERSION,
            "providers": [{"provider": args.provider, "models": [prov.capabilities(args.model).to_dict()]}],
        }

    if args.model:
        # No --provider: every provider is asked about the id, which is rarely what a
        # caller wants (see the warning in docs) but is long-standing behaviour.
        providers = []
        for name in names:
            try:
                providers.append({
                    "provider": name,
                    "models": [registry.get_provider(name).capabilities(args.model).to_dict()],
                })
            except Exception as exc:  # noqa: BLE001
                get_logger().warning("could not describe %s for provider %s: %s", args.model, name, exc)
                providers.append({"provider": name, "models": []})
        return {"ok": True, "schema_version": SCHEMA_VERSION, "providers": providers}

    providers = [{"provider": name, "models": _describe(name, args)} for name in names]
    return {"ok": True, "schema_version": SCHEMA_VERSION, "providers": providers}


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
