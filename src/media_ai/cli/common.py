"""Shared CLI plumbing: global flags, argument parsing helpers, and the machine
contract (one redacted JSON object on stdout; category-specific exit codes).
"""

from __future__ import annotations

import argparse
import json

from ..core.validate import UnsupportedPolicy
from ..core.errors import ErrorCategory, MediaError
from ..core.logging import configure, get_logger
from ..core.types import GeometrySpec, MediaRef
from ..credentials.redaction import redact_obj

_TRUE = {"1", "true", "yes", "y", "on"}


def bool_arg(s) -> bool:
    return str(s).strip().lower() in _TRUE


def add_toggle(ap: argparse.ArgumentParser, *names: str, dest: str | None = None,
               default: bool, help: str) -> None:  # noqa: A002 - argparse's own keyword
    """A boolean flag that accepts both ``--flag`` and ``--flag false``.

    Two callers write these commands and they write them differently: a person types
    ``--transparent``, while an agent filling a schema emits ``--transparent true``.
    Either spelling alone makes the other an argparse error, and "unrecognized
    arguments" is a poor answer to a request that was perfectly clear.

    Used for flags whose off-switch is worth having. A tri-state option — where *unset*
    has to stay distinguishable from ``false``, as with Volc's ``camera_fixed``, which is
    omitted from the wire entirely unless asked for — is still ``type=bool_arg,
    default=None``: a ``const`` would collapse exactly the distinction that matters.
    """
    ap.add_argument(*names, dest=dest, type=bool_arg, nargs="?", const=True, default=default, help=help)


def add_global_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--binding", default=None,
                    help="<provider>/<model>, e.g. volc-ark/seedance-2.0 (see: media-ai bindings list)")
    ap.add_argument("--provider", default=None, help="provider name; with --model it names a binding")
    ap.add_argument("--model", default=None, help="model name; used alone only when one binding serves it")
    ap.add_argument("--pretty", action="store_true", help="pretty-print the JSON result")
    ap.add_argument("--log-level", default=None, help="debug|info|warning|error")
    ap.add_argument("--verbose", action="store_true",
                    help="print redacted binding and HTTP request diagnostics to stderr")
    ap.add_argument("--metadata-out", default=None, help="also write the result JSON to this path (secret-free)")
    ap.add_argument("--on-unsupported", default="error", choices=[p.value for p in UnsupportedPolicy],
                    help="what to do with unsupported options (default: error)")


def provider_name(args) -> str | None:
    return getattr(args, "provider", None)


def bind(args, req):
    """Resolve the binding for ``req``, check it serves the scene, and build its adapter.

    Every generation command goes through here, so the addressing rules, the scene
    check and the ``model_id`` that reaches the wire are decided in exactly one place.
    Returns ``(adapter, resolved_binding, scene)``.
    """
    from ..core.config import load_config
    from ..core.registry import build_adapter, catalog
    from ..core.resolve import available_bindings, resolve
    from ..core.scene import derive_scene

    scene = derive_scene(req)
    cat, config = catalog(), load_config()
    available = available_bindings(cat, config)
    rb = resolve(
        binding=getattr(args, "binding", None),
        provider=getattr(args, "provider", None),
        model=getattr(args, "model", None),
        scene=scene, catalog=cat, config=config,
    )
    rb.check_scene(scene, available)
    req.model = rb.model_id
    get_logger().debug(
        "binding resolved: binding=%s scene=%s provider=%s base_url=%s wire_id=%s",
        rb.id, scene.value, rb.provider.name, rb.base_url, rb.model_id,
    )
    return build_adapter(rb), rb, scene


def stamp(result, rb, scene=None):
    """Record which binding ran and what it was asked for, in the result's ``meta``.

    An agent that keeps the JSON can answer "what actually produced this?" from the
    artifact alone — which matters most exactly when the CLI chose the binding
    itself, from the scene default.

    ``scene`` is ``None`` for ``job query``, which finalizes work submitted by an
    earlier process: the binding is named on the command line, but the inputs that
    implied the scene are gone. The key is then left absent rather than filled with a
    guess.
    """
    meta = getattr(result, "meta", None)
    if isinstance(meta, dict):
        meta.setdefault("binding", rb.id)
        if scene is not None:
            meta.setdefault("scene", scene.value)
    if hasattr(result, "binding") and getattr(result, "binding", None) is None:
        result.binding = rb.id  # a JobHandle's poll command has to name it
    return result


def add_geometry_args(ap: argparse.ArgumentParser, *, resolution_help: str) -> None:
    ap.add_argument("--size", default=None, help="pixel size WIDTHxHEIGHT (e.g. 1024x768)")
    ap.add_argument("--aspect-ratio", "--ratio", dest="aspect_ratio", default=None, help="e.g. 16:9")
    ap.add_argument("--resolution", default=None, help=resolution_help)


def parse_geometry(args) -> GeometrySpec | None:
    from ..core.geometry import parse_size

    if getattr(args, "size", None):
        w, h = parse_size(args.size)
        return GeometrySpec(width=w, height=h)
    if getattr(args, "aspect_ratio", None) or getattr(args, "resolution", None):
        return GeometrySpec(aspect_ratio=getattr(args, "aspect_ratio", None), resolution=getattr(args, "resolution", None))
    return None


def parse_refs(values, role: str | None = None) -> list[MediaRef]:
    return [MediaRef(str(v), role=role) for v in _listify(values or [])]


def _listify(raw: list[str]) -> list[str]:
    """Accept a single JSON-array string (how agent tool layers pass lists) or plain paths."""
    if len(raw) == 1 and raw[0].lstrip().startswith("["):
        try:
            v = json.loads(raw[0])
            if isinstance(v, list):
                return [str(x) for x in v]
        except json.JSONDecodeError:
            pass
    return list(raw)


def parse_options(pairs) -> dict:
    out: dict = {}
    for p in pairs or []:
        if "=" not in p:
            raise MediaError(f"--option must be key=value, got {p!r}", category=ErrorCategory.CLI)
        k, v = p.split("=", 1)
        out[k.strip()] = _coerce(v.strip())
    return out


def _coerce(v: str):
    low = v.lower()
    if low in _TRUE:
        return True
    if low in {"false", "no", "off"}:
        return False
    if v.lstrip("-").isdigit():
        return int(v)
    if "." in v and v.replace(".", "", 1).lstrip("-").isdigit():
        return float(v)  # e.g. guidance_scale=7.5
    return v


def policy(args) -> UnsupportedPolicy:
    return UnsupportedPolicy(getattr(args, "on_unsupported", "error"))


# --------------------------------------------------------------------------
# output contract
# --------------------------------------------------------------------------


def _dump(obj: dict, pretty: bool) -> str:
    safe = redact_obj(obj)
    return json.dumps(safe, ensure_ascii=False, indent=2 if pretty else None)


def emit(obj: dict, args) -> int:
    text = _dump(obj, getattr(args, "pretty", False))
    print(text)
    mo = getattr(args, "metadata_out", None)
    if mo:
        try:
            from pathlib import Path

            Path(mo).parent.mkdir(parents=True, exist_ok=True)
            Path(mo).write_text(_dump(obj, True) + "\n", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            get_logger().warning("could not write --metadata-out %s: %s", mo, exc)
    return 0


def emit_result(result, args) -> int:
    obj = result.to_dict() if hasattr(result, "to_dict") else result
    return emit(obj, args)


def emit_error(err: MediaError, args) -> int:
    emit({"ok": False, "error": err.to_dict()}, args)
    return err.exit_code


def parse_args(parser: argparse.ArgumentParser, argv=None):
    """Parse argv while keeping the machine contract intact on *parse* failure.

    ``--help``/``--version`` write to stdout and exit 0 — standard CLI behavior, left
    untouched. A genuine parse error (bad/unknown flag, missing subcommand) makes
    argparse print the specifics to stderr and exit 2 with **nothing** on stdout; we
    additionally emit the one-JSON-object failure contract on stdout so a machine
    consumer still gets a structured ``{"ok": false, ...}`` (category ``cli``, exit 2)
    rather than an empty stream. Human-readable detail stays on stderr.
    """
    try:
        return parser.parse_args(argv)
    except SystemExit as e:
        if e.code in (0, None):  # --help / --version: leave stdout behavior as-is
            raise
        err = MediaError("invalid command-line arguments (see stderr for details)", category=ErrorCategory.CLI)
        print(_dump({"ok": False, "error": err.to_dict()}, False))
        raise SystemExit(err.exit_code) from None


def run(build_and_call, args) -> int:
    """Configure logging, run the command, and turn any failure into the JSON
    error contract + a category-specific exit code."""
    configure("debug" if getattr(args, "verbose", False) else getattr(args, "log_level", None))
    try:
        result = build_and_call(args)
        return emit_result(result, args)
    except MediaError as e:
        return emit_error(e, args)
    except KeyboardInterrupt:
        return emit_error(MediaError("interrupted", category=ErrorCategory.TIMEOUT), args)
    except Exception as e:  # noqa: BLE001 - last-resort: never leak a raw traceback to stdout
        get_logger().exception("unexpected error")
        return emit_error(MediaError(str(e) or e.__class__.__name__, category=ErrorCategory.UNKNOWN), args)
