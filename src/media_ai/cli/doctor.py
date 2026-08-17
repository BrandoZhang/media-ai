"""``media-ai doctor`` — check an installation without calling a provider.

The failures worth catching after an install are boring and local: the CLI is not on
``PATH``, ffmpeg is missing, ``credentials.toml`` got a mode the resolver refuses to
read, a key is set for a provider the user never configured (or configured for one
they no longer have), the installed skills are from an older version than the CLI.
None of that needs the network, and all of it otherwise surfaces as a confusing error
in the middle of a real generation.

So every check here is **offline and read-only**. Whether a key actually *works* is a
different question — one that costs a request — and stays behind
``media-ai init --verify``.

The exit code is 0 whatever the checks say: the machine contract maps non-zero to an
error *category*, and a diagnosis that ran successfully is not a CLI failure however
grim its findings. The verdict is the ``status`` field (``ok`` / ``warn`` / ``fail``),
which is what a script should branch on.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import sys
import time
import tomllib
from pathlib import Path

from .. import __version__
from ..brand import cli_name, cmd
from ..core.config import config_path
from ..core.errors import MediaError
from ..core.logging import FORMATS
from ..core.packaging import extra_hint
from ..core.result import SCHEMA_VERSION
from ..credentials.stores import credentials_path
from . import common
from ._discovery import available_skills
from ._install import detect
from ._prompt import UNICODE, glyphs_for
from ._skillstore import install_roots, installed_skills, skill_is_current

__all__ = ["main"]

_RANK = {"ok": 0, "warn": 1, "fail": 2}


def _check(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


# --------------------------------------------------------------------- checks


def _check_cli() -> list[dict]:
    """Version, how it was installed, and whether the shell can find it.

    The install method is here because it is the first thing that makes the rest of a
    diagnosis legible: a standalone bundle carries its own interpreter and its own
    ffmpeg, so "which python is this" and "is uv on PATH" are questions about a
    different installation than the one the user has. It also decides the ``PATH``
    advice below — ``uv run`` is a real fallback for a package and a dead end for a
    bundle, which has no environment to run inside.
    """
    cli = cli_name()
    install = detect()
    out = [_check("version", "ok", f"{cli} {__version__} ({install.method}, python {sys.version.split()[0]})")]
    on_path = shutil.which(cli)
    fallback = "" if install.method == "standalone" else ", or call it via `uv run`"
    out.append(
        _check("path", "ok", on_path)
        if on_path
        else _check("path", "warn", f"{cli} is not on PATH; add ~/.local/bin to it{fallback}")
    )
    return out


def _check_update() -> list[dict]:
    """Whether a newer release is published, from the cache and never the network.

    ``doctor`` is defined as strictly offline and that does not bend for this: the
    cache is written by ``init`` (and, later, by an explicit check), so what is
    reported here is what this machine last learned, with its age said out loud. A
    diagnosis that quietly went to the network would be a different command.

    An absent cache is ``ok``, not a warning — a machine that has never fetched is not
    a broken machine, and the fix for it (run setup) is not something a diagnosis
    should nag about.
    """
    from ..core import update

    stamp = update.cached_at()
    if stamp is None:
        return [_check("update", "ok", f"no release feed cached yet (written by `{cmd('init')}`)")]
    age = max(0, int((time.time() - stamp) // 86400))
    when = "today" if age == 0 else f"{age} day(s) ago"
    latest = update.latest_version(update.cached())
    if update.is_newer(latest, __version__):
        return [_check("update", "warn", f"{latest} is available (running {__version__}; checked {when})")]
    return [_check("update", "ok", f"{__version__} is current as of {when}")]


def _check_telemetry() -> list[dict]:
    """Whether telemetry would run, answered without exporting anything.

    The failure worth catching is the quiet one: telemetry enabled, the SDK extra not
    installed, and every command exporting nothing. That reaches an operator at runtime
    as a ``notices[]`` entry on whatever they happened to run next — this is where
    somebody can ask the question directly, before wondering why a dashboard is empty.

    Offline like every other check here. Whether the *collector* is reachable is a
    different question, one that costs a request, and the answer to it is a span that
    arrived or did not.
    """
    from ..core.telemetry import settings
    from ..core.telemetry.runtime import EXTRA

    cfg = settings()
    if not cfg.enabled:
        return [_check("telemetry", "ok", "off (no traces or metrics are exported)")]
    try:
        import opentelemetry.sdk  # noqa: F401
    except ImportError:
        return [_check("telemetry", "warn",
                       f"enabled, but the OpenTelemetry SDK is not installed — nothing is exported; "
                       f"run: {extra_hint(EXTRA)}")]
    where = {
        "otlp": f"otlp -> {cfg.endpoint}",
        "console": "console -> stderr",
        "none": "recording only, nothing exported",
    }[cfg.exporter.value]
    return [_check("telemetry", "ok", f"on: {where} (service {cfg.service})")]


def _check_media() -> list[dict]:
    """ffmpeg and Pillow back the offline mock provider and every local media step."""
    out = []
    try:
        from ..media.ffmpeg import ffmpeg_exe

        out.append(_check("ffmpeg", "ok", ffmpeg_exe()))
    except MediaError as exc:
        out.append(_check("ffmpeg", "fail", str(exc)))
    try:
        from PIL import Image
        from PIL import __version__ as pillow_version

        Image.new("RGB", (1, 1))  # importable is not the same as working
        out.append(_check("pillow", "ok", f"Pillow {pillow_version}"))
    except Exception as exc:  # noqa: BLE001 - a broken imaging core reads like an ImportError
        out.append(_check("pillow", "fail", str(exc) or exc.__class__.__name__))
    return out


def _check_files() -> list[dict]:
    """One entry per file, never two.

    `status` is what a script is told to branch on, so a second entry under the same
    name — an `ok` line followed by a `fail` line for the same path — makes the
    obvious `{c["check"]: c for c in checks}` lookup contradict the report it came
    from. A file's worst finding is its finding.
    """
    config, creds = config_path(), credentials_path()
    out = [_config_check(config)]
    if not creds.is_file():
        out.append(_check("credentials-file", "ok", f"{creds} (absent — keys come from the environment)"))
    elif creds.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        # The resolver refuses this file rather than trusting it, so a loose mode is
        # not a style note: every key in it is already unreadable.
        out.append(_check("credentials-file", "fail", f"{creds} is group/world accessible; run: chmod 600 {creds}"))
    elif bad := _unparseable(creds):
        out.append(_check("credentials-file", "fail", bad))
    else:
        out.append(_check("credentials-file", "ok", f"{creds} (0600)"))
    return out


def _config_check(config: Path) -> dict:
    """The config file: readable, and written in a schema this build can work with.

    The schema verdict belongs to this entry rather than a second one, by the rule
    above — "a file's worst finding is its finding". A file this build refuses is not
    usefully described as an `ok` config plus a `fail` about its version.
    """
    from ..core.config import SCHEMA, declared_schema, load_config

    if not config.is_file():
        return _check("config", "ok", f"{config} (absent — built-in defaults apply)")
    if bad := _unparseable(config):
        return _check("config", "fail", bad)
    try:
        declared = declared_schema(config)
        load_config()
    except MediaError as exc:
        # Whatever went wrong, the message already names the file and, where one exists,
        # the command that settles it: a schema from a newer build, a layout with no
        # migration, a value this build cannot write back.
        return _check("config", "fail", exc.message)
    if declared != SCHEMA:
        # Not a warning: it loaded, so nothing here is broken — the file is simply older
        # than the build reading it, and says the same thing either way.
        return _check("config", "ok", f"{config} (schema {declared}, read as {SCHEMA}; "
                                      f"`{cmd('config', 'migrate')}` writes it back)")
    return _check("config", "ok", str(config))


def _unparseable(path: Path) -> str:
    """A description of why a TOML file cannot be read, or ``""``.

    Named separately because this is the failure a user is most likely to be holding
    when they run ``doctor``: a hand-edited config file with a typo in it, which every
    other command reports only as a confusing error mid-generation.
    """
    try:
        tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return f"{path} is not valid TOML: {exc}"
    except OSError as exc:
        return f"{path} cannot be read: {exc}"
    return ""


def _check_bindings() -> list[dict]:
    """Every configured binding: does its adapter import, and does its credential resolve?

    Reported by *source*, never by value — "resolved from env" is the whole answer.
    Unconfigured bindings are not listed at all: nobody configures everything, and a
    dozen warnings about models this machine was never meant to call is noise that
    hides the one that is actually broken.
    """
    from ..core.config import load_config
    from ..core.registry import catalog, load_adapter_class
    from ..core.resolve import available_bindings

    out: list[dict] = []
    try:
        cat, config = catalog(), load_config()
        bindings = available_bindings(cat, config)
    except MediaError as exc:
        return [_check("bindings", "fail", str(exc))]

    for rb in bindings:
        name = f"binding:{rb.id}"
        try:
            load_adapter_class(rb.provider.adapter)
        except MediaError as exc:
            out.append(_check(name, "fail", str(exc)))
            continue
        if not rb.provider.auth.needs_credential:
            out.append(_check(name, "ok", f"{rb.provider.transport.value}, no credential needed"))
            continue
        try:
            cred = rb.credentials().resolve()
        except MediaError as exc:
            out.append(_check(name, "fail", exc.message))
            continue
        except Exception as exc:  # noqa: BLE001 - a broken source is a finding, not a crash
            # A malformed credentials.toml reaches here as a TOMLDecodeError. Letting
            # it out would abandon the whole diagnosis — including the file-mode check
            # that is trying to explain the very problem being diagnosed.
            out.append(_check(name, "fail", f"{type(exc).__name__}: {exc}"))
            continue
        out.append(_check(name, "ok", f"credential resolved from {cred.source}"))

    if not out:
        out.append(_check("bindings", "warn", f"nothing configured; run `{cmd('init')}`"))
    return out


def _check_defaults() -> list[dict]:
    """A default pointing at a binding that is not configured fails every bare call."""
    from ..core.config import load_config
    from ..core.registry import catalog
    from ..core.resolve import available_bindings

    try:
        config = load_config()
        reachable = {b.id for b in available_bindings(catalog(), config)}
    except MediaError as exc:
        return [_check("defaults", "fail", str(exc))]
    if not config.defaults:
        return [_check("defaults", "warn", "no scene defaults; every call must name a binding")]
    missing = sorted({b for b in config.defaults.values() if b not in reachable})
    if missing:
        return [_check("defaults", "fail", f"default binding(s) not configured: {', '.join(missing)}")]
    return [_check("defaults", "ok", f"{len(config.defaults)} scene default(s)")]


def _check_skills() -> list[dict]:
    """Installed skill copies, and whether they still match the packaged originals.

    Skills are copied, not linked, so upgrading the CLI leaves yesterday's instructions
    in the agent's directory — telling it about flags this version may have changed.
    Nothing else reports that.
    """
    shipped = set(available_skills())
    out = []
    for root in install_roots():
        skills = installed_skills(root)
        if not skills:
            continue
        stale = [s for s in skills if s in shipped and not skill_is_current(root, s)]
        unknown = [s for s in skills if s not in shipped]
        detail = f"{len(skills)} skill(s) in {root}"
        if unknown:
            detail += f"; not shipped by this version: {', '.join(unknown)}"
        if stale:
            detail += f"; differs from {cli_name()} {__version__}: {', '.join(stale)} — re-run `{cmd('init')}`"
        # A skill this version does not ship is as much a problem as a stale one: the
        # agent is reading instructions for a CLI that is no longer installed. Both
        # have to reach the verdict, or `doctor` says "everything checks out" over it.
        out.append(_check("skills", "warn" if stale or unknown else "ok", detail))
    if not out:
        out.append(_check("skills", "warn", f"no Agent Skills installed; run `{cmd('init')} --skills-only`"))
    return out


# -------------------------------------------------------------------- report


def _diagnose(args) -> dict:
    checks = (
        _check_cli() + _check_update() + _check_media() + _check_files()
        + _check_bindings() + _check_defaults() + _check_skills() + _check_telemetry()
    )
    status = max((c["status"] for c in checks), key=lambda s: _RANK[s], default="ok")
    _print(checks, status)
    return {
        "ok": True, "schema_version": SCHEMA_VERSION, "command": "doctor",
        "status": status, "checks": checks,
    }


def _print(checks: list[dict], status: str) -> None:
    """Human rendering on stderr; stdout stays the one JSON object.

    The marks degrade with the terminal: ``doctor`` is most useful on a constrained
    box, which is exactly where stderr may not encode ``✓`` — and an
    ``UnicodeEncodeError`` here would take down the diagnosis it is reporting.
    """
    unicode_ok = glyphs_for(sys.stderr) is UNICODE
    mark = {"ok": "✓", "warn": "!", "fail": "✗"} if unicode_ok else {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}
    for c in checks:
        print(f"  {mark[c['status']]} {c['check']:<22} {c['detail']}", file=sys.stderr)
    # The verdict names the mark it just printed, and stays inside the same encoding:
    # a hard-coded ✗ here would both point at a glyph that was never drawn and raise
    # on the stderr that made us degrade — on exactly the runs that found a problem.
    verdict = {
        "ok": "\nEverything checks out.",
        "warn": "\nUsable, with the warnings above.",
        "fail": f"\nSomething is broken - see the {mark['fail'].strip()} lines above.",
    }[status]
    print(verdict, file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=f"{cli_name()} doctor",
        description="Check the installation offline: PATH, ffmpeg, file modes, credential sources, installed skills.",
    )
    ap.add_argument("--pretty", action="store_true", help="pretty-print the JSON result")
    ap.add_argument("--log-level", default=None, help="stderr log level: debug, info, warning, or error")
    ap.add_argument("--log-format", dest="log_format", default=None, choices=list(FORMATS),
                    help="stderr log rendering (default: text); stdout is one JSON object either way")
    ap.add_argument("--metadata-out", default=None, help="also write the secret-free result JSON to this path")
    return ap


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_diagnose, args)


if __name__ == "__main__":
    raise SystemExit(main())
