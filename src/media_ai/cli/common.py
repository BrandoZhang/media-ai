"""Shared CLI plumbing: global flags, argument parsing helpers, and the machine
contract (one redacted JSON object on stdout; category-specific exit codes).
"""

from __future__ import annotations

import argparse
import json
import time

from .. import __version__
from ..brand import cli_name, cmd
from ..core import notices, telemetry
from ..core.errors import ErrorCategory, MediaError
from ..core.logging import FORMATS, configure, get_logger
from ..core.result import error_payload
from ..core.types import GeometrySpec, MediaRef
from ..core.validate import UnsupportedPolicy
from ..credentials.redaction import redact_obj

_TRUE = {"1", "true", "yes", "y", "on"}


@notices.register_source
def _skills_from_another_build():
    """Installed Agent Skills whose recorded version is not this one.

    Skills are *copied* into an agent's directory, so upgrading the CLI leaves
    yesterday's instructions where the agent reads them — describing flags this build
    may have renamed or dropped. Nothing else says so during an ordinary call, and the
    agent following those instructions is the party that can fix it.

    The install receipt records which version wrote each destination, so this is one
    small file read and a string comparison. ``doctor`` still does the thorough thing
    (comparing the installed tree against the packaged one byte for byte, which also
    catches a hand-edit at the same version); this is the cheap proxy that can afford
    to run on every command.
    """
    from ._skillstore import load_receipt

    stale = sorted(dest for dest, entry in load_receipt().items() if entry.get("version") != __version__)
    if not stale:
        return
    yield notices.Notice(
        kind="skills_stale",
        severity="warn",
        message=(
            f"Agent Skills in {', '.join(stale)} were installed by a different "
            f"{cli_name()} build; this one is {__version__}."
        ),
        action=cmd("init", "--skills-only"),
    )


@notices.register_source
def _a_newer_release_is_published():
    """A newer version, from the cached feed and never from the network.

    ``version check`` answers this on request; this is the same fact arriving unasked,
    on whatever command the caller was already running — which is the only way an agent
    that never thinks to ask ever finds out. Reading the cache is what makes that
    affordable: the fetch happened in ``init``, in an explicit check, or in the detached
    child a previous command left behind (:func:`_refresh_feed_afterwards`), and a
    generation command still touches nothing but a file.

    Stateless on purpose. A "shown once" flag would suppress it for every later session,
    and the agent in *that* session is the one who would have acted — the notice
    describes a condition that is still true, not an event that already happened. It
    stops appearing when the condition does, which is the same rule ``skills_stale``
    follows.

    ``info``, not ``warn``: being a release behind is the ordinary state of a working
    installation. Stale skills are a warning because the instructions being followed are
    describing a different build; this is not that.

    Turned off by ``[update] check = false``. ``CI`` deliberately does not turn it off —
    that gates *unsolicited network*, and reporting something already on disk costs
    nothing. An agent in CI is as able to act on it as anywhere else.
    """
    from .. import __version__
    from ..core import update
    from ._install import detect

    if not update.settings().check:
        return
    latest = update.latest_version(update.cached())
    if not update.is_newer(latest, __version__):
        return
    yield notices.Notice(
        kind="update_available",
        severity="info",
        message=f"{cli_name()} {latest} is published; this is {__version__}.",
        action=detect().upgrade_command(update.SOURCE_REPO, latest),
    )


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
                    help=f"<provider>/<model>, e.g. volc-ark/seedance-2.0 (see: {cmd('bindings', 'list')})")
    ap.add_argument("--provider", default=None, help="provider name; with --model it names a binding")
    ap.add_argument("--model", default=None, help="model name; used alone only when one binding serves it")
    ap.add_argument("--pretty", action="store_true", help="pretty-print the JSON result")
    ap.add_argument("--log-level", default=None, help="debug|info|warning|error")
    ap.add_argument("--log-format", dest="log_format", default=None, choices=list(FORMATS),
                    help="stderr log rendering (default: text); stdout is one JSON object either way")
    ap.add_argument("--verbose", action="store_true",
                    help="print redacted binding and HTTP request diagnostics to stderr")
    ap.add_argument("--metadata-out", default=None, help="also write the result JSON to this path (secret-free)")
    ap.add_argument("--on-unsupported", default="error", choices=[p.value for p in UnsupportedPolicy],
                    help="what to do with unsupported options (default: error)")
    add_toggle(ap, "--allow-retired-binding", dest="allow_retired_binding", default=False,
               help="call a binding the published feed reports as retired; it will probably fail. "
                    "For AI agents: do NOT set this on your own initiative — report the refusal instead")


def add_call_headers(ap: argparse.ArgumentParser) -> None:
    """``--header``, for the commands that actually make a request.

    Deliberately not in :func:`add_global_args`: ``doctor``, ``config`` and ``bindings``
    open no socket, and a flag they accepted and ignored would be worse than one they do
    not have.
    """
    ap.add_argument(
        "--header", dest="headers", action="append", default=None, metavar="'NAME: VALUE'",
        help="extra HTTP header for this request, e.g. a request or trace id (repeatable)",
    )


def call_headers(rb, args):
    """``rb`` carrying this invocation's ``--header`` values, validated.

    One merge site for the three commands that resolve a binding — :func:`bind`, ``job``
    and ``video concat``, whose scene is not derived from its request. The headers ride
    on the :class:`ResolvedBinding` because that is all an adapter is constructed from.
    """
    from dataclasses import replace

    from ..core.binding import Transport
    from ..core.headers import parse_headers, split_header_argument

    given = getattr(args, "headers", None)
    if not given:
        return rb
    if rb.provider.transport is not Transport.HTTP:
        raise MediaError(
            f"binding {rb.id!r} speaks {rb.provider.transport.value}, not http, so it sends no headers",
            category=ErrorCategory.CLI, code="header_unsupported",
            details={"binding": rb.id, "transport": rb.provider.transport.value},
        )
    return replace(rb, headers=parse_headers(split_header_argument(item) for item in given))


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
    with telemetry.span("binding.resolve", scene=scene.value, chosen_by=_addressed_by(args)) as sp:
        cat, config = catalog(), load_config()
        available = available_bindings(cat, config)
        rb = resolve(
            binding=getattr(args, "binding", None),
            provider=getattr(args, "provider", None),
            model=getattr(args, "model", None),
            scene=scene, catalog=cat, config=config,
        )
        rb.check_scene(scene, available)
        rb = call_headers(rb, args)
        sp.set(binding=rb.id, provider=rb.provider.name, wire_id=rb.model_id)
        telemetry.event(telemetry.BINDING_RESOLVED, binding=rb.id, scene=scene.value,
                        provider=rb.provider.name,
                        # Which of the three addressing routes was used, because the
                        # interesting case is the fourth: nobody named anything and the
                        # scene default chose. That is the call whose binding is a
                        # surprise when the result looks different from last week.
                        chosen_by=_addressed_by(args))
        # Two sources say a binding is on its way out, and only one of them speaks per
        # call: the feed knows about withdrawals that happened after this build shipped,
        # so when it has an opinion the manifest's older one adds nothing but a second
        # line.
        if not _enforce_published_policy(args, rb, available):
            _note_declared_deprecation(rb, available)
        req.model = rb.model_id
        get_logger().debug(
            "binding resolved: binding=%s scene=%s provider=%s base_url=%s wire_id=%s",
            rb.id, scene.value, rb.provider.name, rb.base_url, rb.model_id,
        )
        return build_adapter(rb), rb, scene


def _addressed_by(args) -> str:
    """How this call named its binding: ``binding``, ``provider+model``, or ``default``.

    A bounded set of four values, so it can label a metric — and the one worth counting
    is ``default``, where the CLI chose from ``[defaults]`` and no argv names the model
    that ran.
    """
    if getattr(args, "binding", None):
        return "binding"
    provider, model = getattr(args, "provider", None), getattr(args, "model", None)
    if provider and model:
        return "provider+model"
    if provider or model:
        return "partial"
    return "default"


def _enforce_published_policy(args, rb, available) -> bool:
    """The two things the published feed is allowed to stop, checked from the cache.

    Here, and only here, because ``bind`` is what every command that reaches a provider
    goes through — which makes it exactly the set of commands worth stopping. ``doctor``,
    ``version``, ``uninstall`` and ``upgrade`` never call it, so they keep working on a
    build the feed has disowned: a floor that locks a user out of the tools for finding
    out *why* is a floor they will answer by deleting the config directory.

    The cache, never the network. A blocked call must not also be a slow one, and the
    fact was fetched by ``init``, by an explicit check, or by the background refresh a
    previous command started, like everything else here.

    One consequence worth stating out loud, because it looks like a hole and is not: a
    machine whose cache has never been written is not stopped by anything. A first run
    that cannot reach the feed would otherwise be a first run that refuses to work, and
    the answer to a tool that bricks itself on install is not "upgrade it", it is `rm
    -rf` on the config directory. With the refresh now running after every command, the
    practical effect is that a floor or a retirement published today reaches an active
    machine by its *second* invocation.

    Returns whether the feed had anything to say about *this binding*, so the manifest's
    own declaration can stay quiet rather than repeating it.
    """
    from ..core import notices, update

    feed = update.cached()
    if floor := update.below_floor(__version__, feed):
        # No override flag, deliberately. A binding retirement is "this will probably
        # fail", which is a risk somebody may choose to take; a floor is only ever set
        # for a compliance or safety reason, and a switch that waves it away would be
        # asked for by exactly the person who should not have it.
        raise MediaError(
            f"{cli_name()} {__version__} is below the minimum supported version ({floor})",
            category=ErrorCategory.VALIDATION, code="version_unsupported",
            details={"current": __version__, "min_supported": floor},
            hint=cmd("upgrade"),
        )

    retirement = update.retirement_for(rb.id, feed)
    if not retirement:
        return False

    alternatives = [a for a in retirement.get("alternatives", []) or [] if isinstance(a, str)]
    configured = [a for a in alternatives if a in {b.id for b in available}]
    # Prefer an alternative this machine can already call: a hint naming something
    # unconfigured is a second problem handed over as the answer to the first.
    hint = f"re-run with --binding {configured[0]}" if configured else cmd("bindings", "available")
    detail = {
        "binding": rb.id, "since": retirement.get("since"), "source": "feed",
        "alternatives": alternatives, "fixed_in": retirement.get("fixed_in"),
    }
    reason = retirement.get("reason") or "no longer available"

    if retirement.get("severity") == "warn" or getattr(args, "allow_retired_binding", False):
        notices.add(notices.Notice(
            kind="binding_deprecated", severity="warn",
            message=f"{rb.id} is retired: {reason}"
                    + (f" Use {configured[0]} instead." if configured else ""),
            # Not the error's hint. A hint is instructional and "re-run with --binding X"
            # is the best thing to say there; a notice's `action` is documented as
            # runnable verbatim, and the skills tell agents so.
            action=_how_to_switch(alternatives, available),
        ))
        return True
    raise MediaError(
        f"{rb.id} is retired: {reason}",
        category=ErrorCategory.VALIDATION, code="binding_retired",
        details=detail, hint=hint,
    )


def _note_declared_deprecation(rb, available) -> None:
    """A deprecation the manifest already knew about, said out loud at the call.

    ``capabilities`` has reported ``lifecycle`` all along and ``init`` labels the row,
    but neither reaches the caller who passed no binding flags and got the scene
    default — which is the ordinary case, and precisely the one where nobody chose this
    binding at all. The manifest knew; nothing said so.

    Same ``kind`` as the feed's version, because a consumer branches on the kind and
    "which document told us" is not its problem. ``info``, not ``warn``: a deprecated
    binding still works, and the feed's retirement — which means it probably does not —
    is the one that earns the stronger severity.
    """
    from ..core import notices
    from ..core.binding import Lifecycle

    if rb.spec.lifecycle is not Lifecycle.DEPRECATED:
        return
    # A manifest cannot declare `deprecated` without naming a replacement (the parser
    # refuses it), so this always has somewhere to point.
    replacement = rb.spec.replacement
    notices.add(notices.Notice(
        kind="binding_deprecated", severity="info",
        message=f"{rb.id} is deprecated; {replacement} replaces it.",
        action=_how_to_switch([replacement] if replacement else [], available),
    ))


def _how_to_switch(alternatives: list[str], available) -> str:
    """A runnable command for "move off this binding", preferring one already here.

    Two different situations and two different next steps: an alternative this machine
    can already call needs checking against the request (limits and scenes differ
    between models, which is the whole reason a binding is the unit), while one it
    cannot needs adding, and ``bindings available`` prints the exact command for that.
    """
    configured = [a for a in alternatives if a in {b.id for b in available}]
    if configured:
        return cmd("capabilities", "--binding", configured[0])
    return cmd("bindings", "available")


def check(req, args, rb, scene):
    """Validate a request against its binding's manifest, before any network call.

    One copy of what every generation command used to spell out for itself: run
    :func:`~media_ai.core.validate.validate_request`, log whatever it lets through, and
    say so as an event. Consolidated when the event was added, because eight copies of
    a three-line loop are eight places for the ninth command to be instrumented
    differently — and the count of tolerated-but-unsupported options is the number that
    explains an odd-looking result later.
    """
    from ..core.validate import validate_request

    unsupported = policy(args)
    warnings = list(validate_request(req, rb.spec.constraints, unsupported, binding=rb.id, scene=scene))
    for w in warnings:
        get_logger().warning("unsupported (proceeding): %s", w)
    telemetry.event(telemetry.REQUEST_VALIDATED, binding=rb.id, scene=scene.value,
                    policy=unsupported.value, unsupported=len(warnings))
    return warnings


def produce(operation, req, rb, scene, *, name: str | None = None):
    """Run one adapter operation, stamp its result, and count what it made.

    Every generation command ends in this call, which is why the provider span, the
    latency histogram and the artifact counters live here rather than in each of the
    eight command modules. ``operation`` is the bound method
    (``adapter.generate_image``) — its ``__name__`` becomes the span's, so nothing has
    to restate it.

    ``req`` may be ``None``, for the one operation that does not take a request object:
    ``video concat`` passes its inputs as several arguments, so it hands over a bound
    callable and a ``name``. Worth the extra parameter rather than a second
    instrumentation site — concat is the scene most likely to be run in a batch, and a
    dashboard missing it would be missing exactly the calls somebody ran a hundred of.

    The failure path is deliberately *not* handled: a :class:`MediaError` propagates to
    ``run``, which owns the JSON contract and the exit code. This only records that the
    call happened and how it went.
    """
    # An explicit name wins: a lambda has a ``__name__`` too, and it is ``<lambda>``.
    name = name or getattr(operation, "__name__", None) or "call"
    started = time.monotonic()
    labels = {"binding": rb.id, "provider": rb.provider.name, "scene": scene.value if scene else None}
    with telemetry.span(f"provider.{name}", **labels, wire_id=rb.model_id) as sp:
        try:
            result = operation(req) if req is not None else operation()
        except BaseException as exc:
            _record_call(name, labels, started, outcome="error", error=exc)
            raise
        _record_call(name, labels, started, outcome="ok")
        sp.set(**_artifact_totals(result))
        _record_artifacts(result, labels)
        return stamp(result, rb, scene)


def _record_call(name: str, labels: dict, started: float, *, outcome: str, error: BaseException | None = None) -> None:
    elapsed_ms = (time.monotonic() - started) * 1000
    category = getattr(getattr(error, "category", None), "value", None)
    telemetry.event(telemetry.PROVIDER_CALL, operation=name, outcome=outcome,
                    duration_ms=round(elapsed_ms, 1), **{"error.category": category}, **labels)
    telemetry.count("media_ai.provider.calls", outcome=outcome, **labels, **{"error.category": category})
    telemetry.observe("media_ai.provider.duration", elapsed_ms, outcome=outcome, **labels)


def _artifact_totals(result) -> dict:
    artifacts = list(getattr(result, "artifacts", None) or [])
    return {"artifacts": len(artifacts), "artifact_bytes": sum(int(a.bytes or 0) for a in artifacts)}


def _record_artifacts(result, labels: dict) -> None:
    """Count the files a call produced, grouped by kind.

    From ``artifacts[]`` and nowhere else — the machine contract says every produced
    file is an entry there, so a count taken from it cannot disagree with what the
    caller was told. A submit that returns a :class:`~media_ai.core.result.JobHandle`
    has no artifacts yet and is reported as the job it is instead.
    """
    if getattr(result, "id", None) and not getattr(result, "artifacts", None):
        telemetry.event(telemetry.JOB_SUBMITTED, job_id=result.id, status=getattr(result, "status", None), **labels)
        return
    by_kind: dict[str, list[int]] = {}
    for artifact in getattr(result, "artifacts", None) or []:
        by_kind.setdefault(artifact.kind, []).append(int(artifact.bytes or 0))
    for kind, sizes in by_kind.items():
        telemetry.count("media_ai.artifacts", len(sizes), kind=kind, **labels)
        telemetry.count("media_ai.artifact.bytes", sum(sizes), kind=kind, **labels)
    if by_kind:
        telemetry.event(telemetry.ARTIFACT_WRITTEN, count=sum(len(s) for s in by_kind.values()),
                        kinds=sorted(by_kind), **labels)


def stamp(result, rb, scene=None):
    """Record which binding ran, what it was asked for, and which build ran it.

    An agent that keeps the JSON can answer "what actually produced this?" from the
    artifact alone — which matters most exactly when the CLI chose the binding
    itself, from the scene default. ``tool_version`` completes that answer: a bug
    report, a regression bisect and a "why does this look different from last week"
    all start from which build wrote the file, and the JSON beside it is the only
    thing that outlives the shell it was produced in.

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
        # Assigned, not `setdefault`-ed, unlike the two above: those may legitimately
        # come from an adapter, while the running version is something only this
        # process knows. A value found here came from somewhere that cannot be right.
        meta["tool_version"] = __version__
    if hasattr(result, "binding") and getattr(result, "binding", None) is None:
        result.binding = rb.id  # a JobHandle's poll command has to name it
    return result


def add_geometry_args(ap: argparse.ArgumentParser, *, resolution_help: str) -> None:
    ap.add_argument("--size", default=None, help="pixel size WIDTHxHEIGHT (e.g. 1024x768)")
    ap.add_argument("--aspect-ratio", "--ratio", dest="aspect_ratio", default=None, help="e.g. 16:9")
    ap.add_argument("--resolution", default=None, help=resolution_help)


def parse_geometry(args) -> GeometrySpec | None:
    from ..core.geometry import parse_ratio, parse_size

    if getattr(args, "size", None):
        w, h = parse_size(args.size)
        return GeometrySpec(width=w, height=h)
    if getattr(args, "aspect_ratio", None) or getattr(args, "resolution", None):
        # Both values are form-checked here, the way `--size` is: a `--aspect-ratio` that
        # is not a ratio at all is refused with the field named, rather than reaching a
        # binding that declares no ratio list and going on the wire.
        return GeometrySpec(aspect_ratio=parse_ratio(getattr(args, "aspect_ratio", None)),
                            resolution=getattr(args, "resolution", None))
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
    safe = redact_obj(_with_notices(obj))
    return json.dumps(safe, ensure_ascii=False, indent=2 if pretty else None)


def _with_notices(obj):
    """Attach ``notices[]`` to an outgoing payload, if there is anything to say.

    Here rather than in each ``to_dict`` because this is the single funnel: success,
    failure, an argparse rejection and the dispatcher's unknown-group error all render
    through it. That last pair matters most — they are the paths where no command body
    runs, and "you passed a flag this build does not have" is exactly what stale skills
    look like from the outside.

    A copy, never a mutation: ``emit`` renders the same object twice when
    ``--metadata-out`` is given.
    """
    if not isinstance(obj, dict):
        return obj
    found = notices.pending()
    return {**obj, "notices": list(found)} if found else obj


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
    emit(error_payload(err), args)
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
        args = parser.parse_args(argv)
        # The command, from the parser rather than from ``sys.argv``: the umbrella
        # dispatcher rewrites argv[0], a direct ``python -m media_ai.cli.image`` does
        # not, and telemetry needs the same answer either way. Bounded by construction —
        # it is a group and a subcommand, never a prompt or a path — which is what makes
        # it usable as a span name and a metric label.
        args._command = _command_of(parser, args)
        return args
    except SystemExit as e:
        if e.code in (0, None):  # --help / --version: leave stdout behavior as-is
            raise
        err = MediaError("invalid command-line arguments (see stderr for details)", category=ErrorCategory.CLI)
        print(_dump(error_payload(err), False))
        raise SystemExit(err.exit_code) from None


def _command_of(parser: argparse.ArgumentParser, args) -> str:
    """``image.generate`` — the group from the parser's ``prog``, the op from the args."""
    group = (parser.prog or "").split()[-1] if parser.prog else ""
    op = getattr(args, "op", None)
    return f"{group}.{op}" if group and op else (group or "?")


def run(build_and_call, args, *, refresh_feed: bool = True) -> int:
    """Configure logging and telemetry, run the command, and turn any failure into the
    JSON error contract + a category-specific exit code.

    ``refresh_feed=False`` is for the one command that is not simply *using* this
    installation but taking it apart — see :func:`_refresh_feed_afterwards`.

    The whole of it runs inside ``config.snapshot()``, so an invocation has one view of
    the configuration — the command body and the notice sources that run on the way out
    included. The emit is inside the block deliberately: the notices are computed there,
    and one of them asks the config whether update checking is on.

    ``telemetry.invocation`` is *inside* the snapshot for the same reason: whether
    telemetry is on is a config question, and it must be the same answer the rest of the
    command reads. It is outside the ``try`` because it owns the flush — a command that
    fails is exactly the one whose spans have to survive, and a ``finally`` around the
    whole block is the only place that is true of every path out of here.
    """
    from ..core.config import snapshot

    configure("debug" if getattr(args, "verbose", False) else getattr(args, "log_level", None),
              fmt=getattr(args, "log_format", None))
    interrupted = False
    with snapshot(), telemetry.invocation(getattr(args, "_command", "?")) as inv:
        try:
            result = build_and_call(args)
            return inv.finish(emit_result(result, args))
        except MediaError as e:
            inv.failed(e)
            return inv.finish(emit_error(e, args))
        except KeyboardInterrupt:
            interrupted = True
            err = MediaError("interrupted", category=ErrorCategory.TIMEOUT)
            inv.failed(err)
            return inv.finish(emit_error(err, args))
        except Exception as e:  # noqa: BLE001 - last-resort: never leak a raw traceback to stdout
            get_logger().exception("unexpected error")
            err = MediaError(str(e) or e.__class__.__name__, category=ErrorCategory.UNKNOWN)
            inv.failed(err)
            return inv.finish(emit_error(err, args))
        finally:
            if refresh_feed and not interrupted:
                _refresh_feed_afterwards()


def _refresh_feed_afterwards() -> None:
    """Top the release-feed cache up, in the background, on the way out of a command.

    **After**, not before, and that is the whole design. Before means every command pays
    the latency of a request it did not ask for, on the one path this project has spent
    the most effort keeping clear; after means this run used whatever was already on
    disk and the *next* one gets the newer answer. For a document that says which
    versions are supported and which bindings have been withdrawn — decisions taken over
    days — being right one invocation later is not a compromise.

    It is what makes the check automatic. Until now a fetch happened only in ``init``
    and in ``version check``, so the two things the feed is allowed to *stop*
    (:func:`~media_ai.core.update.below_floor`,
    :func:`~media_ai.core.update.retirement_for`) were read from a cache that a machine
    might refresh once and never again. Whether a policy applies is still decided
    entirely by the feed's ``min_supported`` and ``retired_bindings``; this only makes
    sure the machine has heard of them.

    In the ``finally``, so a command that *failed* refreshes too — being out of date is
    a plausible reason for the failure, and the update notice riding the next
    invocation's ``notices[]`` is how anyone finds out. Not after an interrupt: Ctrl-C
    means stop, and a process that outlives the one the user just killed is the
    opposite of that.

    Inside ``snapshot()`` because the decision reads ``[update]``, and it must be the
    same configuration the rest of the command saw. Wrapped, because an update check is
    never a reason for a command to fail — least of all here, where the result has
    already been printed and the exit code already chosen.

    ``uninstall`` opts out (``run(..., refresh_feed=False)``), and it is the only
    command that needs to. This writes a stamp before it spawns anything, so on the way
    out of a command that has just deleted the cache — and the config directory holding
    it — it would put one of them straight back. "Uninstalling leaves nothing behind" is
    a promise this project makes in as many words, and a file recreated one millisecond
    after removal is the most confusing possible way to break it: the command reports
    the path as removed, and the path is there.
    """
    from ..core import update

    try:
        update.refresh_detached(__version__)
    except Exception as exc:  # noqa: BLE001 - a preference is never worth failing a command over
        get_logger().debug("could not start a background update check: %s", exc)
