"""``media-ai bindings list|available|add`` — see and configure what this machine can call.

Every refusal from :mod:`media_ai.core.resolve` names one of these as its fix, so they
exist for the same reason the errors carry hints: nothing falls back on the caller's
behalf, which makes "what *can* I call, and how do I add the one I want?" a question
the CLI has to answer directly.
"""

from __future__ import annotations

import argparse

from ..brand import cli_name, cmd
from ..core.config import UserBinding, config_path, load_config, save_config
from ..core.errors import ErrorCategory, MediaError
from ..core.registry import catalog
from ..core.resolve import available_bindings
from ..core.result import SCHEMA_VERSION
from ..credentials.reference import is_reference
from . import common

def config_header() -> str:
    """The comment block written at the top of ``config.toml``.

    A function, like every other branded string: a module constant would bake the name
    in at import time, which is invisible until someone renames the build.
    """
    return (
        f"{cli_name()} config — bindings and scene defaults.\n"
        "NON-SECRET: safe to share. `credential` is a reference, never a key."
    )


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{cli_name()} bindings", description="List or configure bindings.")
    sub = ap.add_subparsers(dest="op", required=True)
    for op, help_text in (("list", "list configured and built-in bindings"),
                          ("available", "list declared bindings not configured yet")):
        common.add_global_args(sub.add_parser(op, help=help_text))
    add = sub.add_parser("add", help="write a binding into the config")
    add.add_argument("id", help="<provider>/<model>, or any name when using --extends")
    add.add_argument("--credential", default=None, help="env://VAR | cred://<account> | keychain://<name>")
    add.add_argument("--extends", default=None, help="inherit the capabilities of another binding")
    add.add_argument("--model-id", dest="model_id", default=None, help="override the id sent on the wire")
    add.add_argument("--endpoint-id", dest="endpoint_id", default=None,
                     help="Volcengine Ark endpoint ID sent as model (e.g. ep-xxx-xxx)")
    add.add_argument("--base-url", dest="base_url", default=None,
                     help="override the provider's HTTP base URL")
    common.add_global_args(add)
    return ap


def _entry(rb) -> dict:
    return {
        "binding": rb.id,
        "provider": rb.provider.name,
        "model": rb.spec.model,
        "model_id": rb.model_id,
        "scenes": sorted(s.value for s in rb.spec.scenes),
        "configured": rb.configured,
        "needs_credential": rb.provider.auth.needs_credential,
        "credential": rb.credential,
        "base_url": rb.base_url,
    }


def _list(args) -> dict:
    cat, config = catalog(), load_config()
    return {
        "ok": True, "schema_version": SCHEMA_VERSION,
        "bindings": [_entry(b) for b in available_bindings(cat, config)],
        "defaults": dict(sorted(config.defaults.items())),
        "config": str(config_path()),
    }


def _available(args) -> dict:
    """Declared but not yet configured — what you could add, and what it would need."""
    cat, config = catalog(), load_config()
    reachable = {b.id for b in available_bindings(cat, config)}
    out = []
    for spec in cat.all():
        if spec.id in reachable:
            continue
        provider = cat.providers[spec.provider]
        out.append({
            "binding": spec.id,
            "provider": spec.provider,
            "title": spec.title,
            "scenes": sorted(s.value for s in spec.scenes),
            "env": list(provider.auth.env),
            "setup_hint": provider.setup_hint,
            "add": f"{cmd('bindings', 'add', spec.id)} --credential env://"
                   f"{provider.auth.env[0] if provider.auth.env else 'API_KEY'}",
        })
    return {"ok": True, "schema_version": SCHEMA_VERSION, "bindings": out}


def _add(args) -> dict:
    cat, config = catalog(), load_config()
    spec = cat.get(args.extends or args.id)
    if spec is None:
        raise MediaError(
            f"nothing declares {args.extends or args.id!r}",
            category=ErrorCategory.NOT_FOUND, code="unknown_binding",
            details={"declared": cat.ids()},
            hint=cmd("bindings", "available"),
        )
    provider = cat.providers[spec.provider]
    if provider.auth.needs_credential and not args.credential:
        env = provider.auth.env[0] if provider.auth.env else f"{provider.name.upper()}_API_KEY"
        raise MediaError(
            f"binding {args.id!r} needs a credential",
            category=ErrorCategory.AUTH, code="credential_missing",
            details={"setup_hint": provider.setup_hint},
            hint=f"{cmd('bindings', 'add', args.id)} --credential env://{env}",
        )
    if args.credential and not is_reference(args.credential):
        raise MediaError(
            "--credential must be a reference (env://VAR, cred://<account>, keychain://<name>), never a raw key",
            category=ErrorCategory.AUTH, code="credential_is_raw_key",
            hint="put the key in credentials.toml and refer to it as cred://<account>",
        )
    if args.model_id and args.endpoint_id:
        raise MediaError(
            "use either --model-id or --endpoint-id, not both", category=ErrorCategory.CLI,
            code="wire_id_ambiguous",
        )
    if args.endpoint_id:
        if provider.name != "volc-ark":
            raise MediaError(
                "--endpoint-id is only valid for Volcengine Ark bindings", category=ErrorCategory.CLI,
                code="endpoint_id_unsupported",
            )
        if provider.wire_id_pattern:
            import re

            if not re.fullmatch(provider.wire_id_pattern, args.endpoint_id):
                raise MediaError(
                    f"--endpoint-id must match {provider.wire_id_hint or provider.wire_id_pattern}",
                    category=ErrorCategory.CLI, code="endpoint_id_invalid",
                )

    # Merge, never rebuild: `add` on a binding that already exists is how a key gets
    # rotated, and it must not take the endpoint id, base URL or per-binding options
    # down with it. Only the flags actually passed change anything.
    existing = config.bindings.get(args.id) or UserBinding(id=args.id)
    bindings = dict(config.bindings)
    bindings[args.id] = existing.merged_with(
        extends=args.extends,
        model_id="" if args.endpoint_id else args.model_id,
        endpoint_id="" if args.model_id else args.endpoint_id,
        base_url=args.base_url, credential=args.credential,
    )
    updated = type(config)(bindings=bindings, defaults=dict(config.defaults), path=config.path, exists=True)
    saved = save_config(updated, header=config_header())
    return {
        "ok": True, "schema_version": SCHEMA_VERSION,
        "binding": args.id, "config": str(config_path()),
        "backup": str(saved) if saved else None,
        "scenes": sorted(s.value for s in spec.scenes),
    }


def _do(args) -> dict:
    return {"list": _list, "available": _available, "add": _add}[args.op](args)


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
