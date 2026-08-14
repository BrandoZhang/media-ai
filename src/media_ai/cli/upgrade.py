"""``<cli> upgrade`` — hand the upgrade to the package manager that owns this install.

``version check`` says a newer release exists and prints the command; this runs it. The
convenience is real — the command is long and names a URL and a tag — but so is the
restraint, and the restraint is most of what is written down here.

**It substitutes nothing.** The steps come from :mod:`media_ai.cli._install`, which
knows only how this build was installed, and they are executed as argv with no shell.
A failure is the package manager's own message, so an upgrade that goes wrong leaves
something diagnosable rather than an installation nobody can describe.

**It refuses more than it runs.** An editable checkout is told to ``git pull`` and left
alone — someone's work tree and git state are theirs, and a stash conflict caused by a
CLI helpfully pulling is a bad afternoon. An install it cannot identify gets no command
at all. And with no terminal it refuses without ``--yes``: replacing the tool is not
something to do because an unattended process happened to run this.

**It needs a target it can name.** The version comes from the feed, or from
``--version``; with neither, it refuses. Installing the default branch instead would
quietly move somebody from a release onto whatever main happens to be, which is a
different thing from upgrading.

The child's stdin is closed (``media/ffmpeg.py`` documents why: a spawned process
otherwise inherits the caller's and may eat the rest of a ``while read … done <
list.txt``), and its output is captured rather than inherited, because stdout here
belongs to the one JSON object this command prints like every other.
"""

from __future__ import annotations

import argparse
import subprocess

from .. import __version__
from ..brand import cli_name, cmd
from ..core import update
from ..core.errors import ErrorCategory, MediaError
from ..core.result import SCHEMA_VERSION
from . import common
from ._install import detect
from ._prompt import Cancelled, _nobody_is_watching, get_prompter

__all__ = ["main"]

#: Package managers are slow, and a network install of a git dependency is slower. Long
#: enough that a real upgrade never trips it, bounded so a wedged child is not forever.
_TIMEOUT_SECONDS = 600


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=f"{cli_name()} upgrade",
        description="Upgrade this installation to the latest published release.",
        epilog=(
            "For AI agents: this replaces the tool you are running. Do NOT run it on "
            "your own initiative — surface the notice and let the user decide."
        ),
    )
    ap.add_argument("--version", dest="target", default=None,
                    help="install this release instead of the latest published one")
    common.add_toggle(ap, "--yes", "-y", dest="yes", default=False,
                      help="do not ask before replacing the installed CLI")
    common.add_toggle(ap, "--dry-run", dest="dry_run", default=False,
                      help="report what would run, and run nothing")
    common.add_global_args(ap)
    return ap


def _target(args) -> str:
    """Which release to install. Refuses rather than falling back to the branch.

    ``--version`` wins, then whatever the feed says is published — fetched here because
    this command is an explicit request and waiting for it is the deal. With neither,
    there is no target: installing the default branch would move somebody from a
    release onto whatever main happens to be, and call it an upgrade.
    """
    if args.target:
        return args.target
    feed = update.refresh(__version__, force=True) or update.cached()
    latest = update.latest_version(feed)
    if not latest:
        raise MediaError(
            "cannot tell which release to install: the published feed was unreachable "
            "and no --version was given",
            category=ErrorCategory.CLI, code="no_upgrade_target",
            hint=cmd("upgrade", "--version", "<release>"),
        )
    return latest


def _run_steps(steps: list[list[str]], summary: dict) -> None:
    """Run each step, stopping at the first failure.

    ``capture_output`` because stdout belongs to this command's JSON object, and
    ``stdin=DEVNULL`` for the reason ``media/ffmpeg.py`` spells out: a child otherwise
    inherits the caller's stdin and may eat input meant for something else.
    """
    for step in steps:
        try:
            done = subprocess.run(  # noqa: S603 - argv from _install, never a shell
                step, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=_TIMEOUT_SECONDS, check=False,
            )
        except FileNotFoundError as exc:
            raise MediaError(
                f"{step[0]} is not installed; run the upgrade yourself: {' '.join(steps[0])}",
                category=ErrorCategory.IO, code="upgrade_tool_missing",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaError(
                f"{step[0]} did not finish within {_TIMEOUT_SECONDS}s",
                category=ErrorCategory.TIMEOUT, code="upgrade_timed_out",
            ) from exc
        summary["steps"].append({"argv": step, "returncode": done.returncode})
        if done.returncode != 0:
            # The tail, not the whole log: a pip resolver failure is hundreds of lines
            # and the last few say what happened. The same shape `ffmpeg._run` uses.
            tail = "\n".join((done.stderr or done.stdout or "").strip().splitlines()[-10:])
            raise MediaError(
                f"{step[0]} exited {done.returncode}\n{tail}",
                category=ErrorCategory.IO, code="upgrade_failed",
                details={"argv": step, "returncode": done.returncode},
            )


def _upgrade(args) -> dict:
    install = detect()
    current = __version__
    summary = {
        "ok": True, "schema_version": SCHEMA_VERSION, "command": "upgrade",
        "from": current, "install": install.method, "steps": [],
        "dry_run": bool(args.dry_run),
    }

    target = _target(args)
    summary["to"] = target
    if not update.is_newer(target, current) and not args.target:
        # Not a failure: being asked to upgrade something already current is a
        # perfectly good outcome, and reporting it as an error would make a scheduled
        # `upgrade` noisy on every machine that is up to date.
        summary["upgraded"] = False
        summary["reason"] = "already_current"
        return summary

    steps = install.upgrade_steps(update.SOURCE_REPO, target)
    if steps is None:
        # Two different refusals wearing one code. Editable is "there is a command and
        # it is not mine to run"; unknown is "there is no command I can vouch for".
        why = (
            "this is a source checkout, and its git state is yours to move"
            if install.method == "editable"
            else f"cannot tell how this {cli_name()} was installed, so there is no upgrade to run"
        )
        raise MediaError(
            why, category=ErrorCategory.CLI, code="upgrade_not_supported",
            details={"install": install.method, "prefix": install.prefix},
            hint=install.upgrade_command(update.SOURCE_REPO, target)
            or f"reinstall {cli_name()} however you installed it",
        )

    summary["would_run"] = [" ".join(step) for step in steps]
    if args.dry_run:
        summary["upgraded"] = False
        summary["reason"] = "dry_run"
        return summary

    if not args.yes:
        # `_nobody_is_watching` is the one place that answers "should anything try to be
        # interactive", and it answers it about the environment as well as the terminal —
        # `docker run -t` has a tty with nobody in front of it. Asking a question no one
        # will answer, and then replacing the CLI when the read fails, is the outcome to
        # avoid; refusing is recoverable with one flag.
        if _nobody_is_watching():
            raise MediaError(
                f"refusing to replace the installed {cli_name()} without confirmation",
                category=ErrorCategory.CLI, code="confirmation_required",
                hint=cmd("upgrade", "--yes"),
            )
        try:
            confirmed = get_prompter().confirm(f"Upgrade {cli_name()} {current} → {target}?", default=True)
        except Cancelled:
            confirmed = False
        if not confirmed:
            summary["upgraded"] = False
            summary["reason"] = "declined"
            return summary

    _run_steps(steps, summary)
    summary["upgraded"] = True
    # Deliberately not re-reading `__version__`: this process imported the old code and
    # still holds it, so anything it reports about itself is the version it started as.
    # The number that matters is what was installed, which is `to`.
    summary["note"] = f"{cli_name()} {target} installed; the next invocation runs it"
    return summary


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_upgrade, args)


if __name__ == "__main__":
    raise SystemExit(main())
