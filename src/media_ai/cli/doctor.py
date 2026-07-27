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
import tomllib
from pathlib import Path

from .. import __version__
from ..core import registry
from ..core.errors import MediaError
from ..core.result import SCHEMA_VERSION
from ..credentials.profile import config_path
from ..credentials.resolver import default_chain
from ..credentials.stores import credentials_path
from . import common
from ._discovery import available_skills
from ._prompt import UNICODE, glyphs_for
from ._skillstore import install_roots, installed_skills, skill_is_current

__all__ = ["main"]

_RANK = {"ok": 0, "warn": 1, "fail": 2}


def _check(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


# --------------------------------------------------------------------- checks


def _check_cli() -> list[dict]:
    out = [_check("version", "ok", f"media-ai {__version__} (python {sys.version.split()[0]})")]
    on_path = shutil.which("media-ai")
    out.append(
        _check("path", "ok", on_path)
        if on_path
        else _check("path", "warn", "media-ai is not on PATH; add ~/.local/bin to it, or call it via `uv run`")
    )
    return out


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
    if not config.is_file():
        return _check("config", "ok", f"{config} (absent — built-in defaults apply)")
    if bad := _unparseable(config):
        return _check("config", "fail", bad)
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


def _check_credentials() -> list[dict]:
    """Which providers resolve a credential — by *source*, never by value.

    A provider with no key is a ``warn``, not a failure: nobody configures all four.
    """
    chain = default_chain()
    out = []
    for name in registry.provider_names():
        try:
            provider = registry.get_provider(name)
        except Exception as exc:  # noqa: BLE001 - a broken adapter is its own finding
            out.append(_check(f"credential:{name}", "warn", f"provider could not be loaded: {exc}"))
            continue
        if not getattr(provider, "requires_credentials", True):
            continue
        try:
            cred = chain.resolve(name)
        except MediaError:
            out.append(_check(f"credential:{name}", "warn", "no credential found (unconfigured)"))
            continue
        except Exception as exc:  # noqa: BLE001 - a broken source is a finding, not a crash
            # A malformed credentials.toml reaches here as a TOMLDecodeError. Letting
            # it out would abandon the whole diagnosis — including the file-mode check
            # that is trying to explain the very problem being diagnosed.
            out.append(_check(f"credential:{name}", "fail", f"{type(exc).__name__}: {exc}"))
            continue
        # `source` is a descriptor like "env:OPENAI_API_KEY" — never the key itself.
        out.append(_check(f"credential:{name}", "ok", f"resolved from {cred.source}"))
    return out


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
            detail += f"; differs from media-ai {__version__}: {', '.join(stale)} — re-run `media-ai init`"
        # A skill this version does not ship is as much a problem as a stale one: the
        # agent is reading instructions for a CLI that is no longer installed. Both
        # have to reach the verdict, or `doctor` says "everything checks out" over it.
        out.append(_check("skills", "warn" if stale or unknown else "ok", detail))
    if not out:
        out.append(_check("skills", "warn", "no Agent Skills installed; run `media-ai init --skills-only`"))
    return out


# -------------------------------------------------------------------- report


def _diagnose(args) -> dict:
    checks = _check_cli() + _check_media() + _check_files() + _check_credentials() + _check_skills()
    status = max((c["status"] for c in checks), key=lambda s: _RANK[s], default="ok")
    _print(checks, status)
    return {
        "ok": True, "schema_version": SCHEMA_VERSION, "operation": "doctor",
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
        prog="media-ai doctor",
        description="Check the installation offline: PATH, ffmpeg, file modes, credential sources, installed skills.",
    )
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--log-level", default=None)
    ap.add_argument("--metadata-out", default=None)
    return ap


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_diagnose, args)


if __name__ == "__main__":
    raise SystemExit(main())
