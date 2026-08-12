"""``media-ai uninstall`` — undo what ``media-ai init`` wrote.

Installing is only half a lifecycle. ``init`` copies skill directories into several
agent conventions at once and writes two files under ``~/.config/media-ai``; without
this command the only way back is to remember all of that and delete it by hand.

Two rules shape it:

- **Uninstalling leaves nothing behind.** Skills, ``config.toml`` and
  ``credentials.toml`` all go unless a ``--keep-*`` flag says otherwise. The
  alternative — keeping configuration by default — quietly commits the project to
  *migration*: a file written by any past version would have to stay readable by every
  future one, from the first release onward, because a reinstall would always find one
  waiting. Removing it is what makes an install a fresh start. (Migration is still
  worth having, and is tracked separately; ``--keep-config`` is the escape hatch until
  then, and the confirmation is asked with the paths spelled out.)
- **Nothing is removed that was not recognisably installed.** Every deletion goes
  through :mod:`media_ai.cli._skillstore`, which touches only ``media-ai-*``
  directories carrying a ``SKILL.md``, and unlinks symlinks rather than following
  them. A wrong ``--skills-dest`` fails loudly instead of recursing.

The CLI itself is left in place: it is what is running. The removal command for it is
printed, and carried in the JSON as ``remove_cli`` so ``install.sh --uninstall`` can
finish the job.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from ..core.config import config_path
from ..core.result import SCHEMA_VERSION
from ..credentials.stores import credentials_path
from . import common
from ._prompt import Cancelled, Option, get_prompter, run_steps
from ._skillstore import (
    install_roots,
    installed_skills,
    prune_empty,
    receipt_path,
    record_install,
    remove_skill,
)

__all__ = ["main"]


# ------------------------------------------------------------------- discovery


def _candidates(explicit: list[str] | None) -> list[tuple[Path, list[str]]]:
    """``[(dest, [skill, …]), …]`` for every directory holding installed skills.

    Without ``--skills-dest`` this looks in the receipt (which knows about custom
    paths) *and* the conventional locations (which cover hand-copied installs and
    anything predating the receipt). Order is receipt-first so the paths a user chose
    lead the list.
    """
    roots = [Path(p).expanduser() for p in explicit] if explicit else install_roots()
    out: list[tuple[Path, list[str]]] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve())
        if key in seen:
            continue
        seen.add(key)
        if skills := installed_skills(root):
            out.append((root, skills))
    return out


def _dest_choices(found: list[tuple[Path, list[str]]]) -> list[Option]:
    return [
        Option(
            label=str(dest),
            hint=f"{len(skills)} skill{'s' if len(skills) != 1 else ''}",
            value=dest,
            detail=", ".join(skills),
        )
        for dest, skills in found
    ]


# --------------------------------------------------------------------- removal


def _remove_skills(found: list[tuple[Path, list[str]]], summary: dict, *, dry_run: bool) -> None:
    if not found:
        return
    for dest, skills in found:
        removed = [skill for skill in skills if dry_run or remove_skill(dest, skill)]
        summary["skills"].append({"dest": str(dest), "removed": removed})
    if dry_run:
        return
    for dest, _skills in found:
        # An emptied skills directory is litter; a directory with anything left in it
        # is somebody else's. rmdir tells the two apart without a heuristic.
        if prune_empty(dest):
            summary["removed"].append(str(dest))
    # Re-derives each entry from what is left on disk, so a partial removal keeps an
    # accurate receipt and a full one deletes it.
    record_install([dest for dest, _ in found])


def _remove_file(path: Path, summary: dict, *, dry_run: bool) -> None:
    if not dry_run:
        path.unlink(missing_ok=True)
    summary["removed"].append(str(path))


def _backups_of(path: Path) -> list[Path]:
    """``init`` copies a file aside before rewriting it. A backup of the credentials
    file holds the same keys, so removing one without the other would leave the secret
    on disk under a name the user is even less likely to remember."""
    return sorted(p for p in path.parent.glob(path.name + ".bak*") if p.is_file())


# ---------------------------------------------------------------- the command


# Asked about separately: someone rotating machines wants the model defaults gone but
# the keys kept, and a shared box is the other way round.
CONFIG_FILES = (
    ("config", config_path, "provider defaults, profiles, endpoint ids"),
    ("credentials", credentials_path, "API keys — this may be the only copy"),
)


@dataclass
class _Choices:
    """What the questions settle, before anything is deleted."""

    skills: list[tuple[Path, list[str]]] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    # Keyed by flag rather than accumulated into a list: a step that is re-run after
    # the user goes back has to *replace* its previous answer, not add to it.
    files: dict[str, bool] = field(default_factory=dict)


def _ask_skills(args, prompter, choices: _Choices) -> None:
    # Cleared before the discovery that can fail: a step that aborts part-way must not
    # leave the previous run's answer behind, and this is the one that decides what
    # gets deleted. Same rule every step in `init` follows.
    choices.skills, choices.kept = [], []
    found = _candidates(args.skills_dest)
    if args.keep_skills:
        choices.kept = [str(dest) for dest, _ in found]
        return
    if not found:
        prompter.note("No installed Agent Skills found.")
        return
    if args.yes:
        choices.skills = found
        return
    options = _dest_choices(found)
    picked = set(prompter.multiselect(
        "Remove the media-ai skills installed here?", options, preselected=list(range(len(options))),
    ))
    choices.kept = [str(dest) for i, (dest, _) in enumerate(found) if i not in picked]
    choices.skills = [entry for i, entry in enumerate(found) if i in picked]


def _ask_file(args, prompter, choices: _Choices, flag: str, path: Path, what: str) -> None:
    """Whether one configuration file goes. It does, unless told otherwise.

    Still *asked* rather than assumed on an interactive run: the default is yes, but a
    credentials file is often the only copy of a key, so the path and what is in it are
    put in front of the user before it goes.
    """
    choices.files[flag] = False
    if not path.is_file() or getattr(args, f"keep_{flag}"):
        return
    choices.files[flag] = True if args.yes else prompter.confirm(f"Remove {path}\n  ({what})?", default=True)


def _uninstall(args, prompter) -> dict:
    """Ask everything, then delete. Never the other way round.

    Ctrl-C at any prompt leaves the install exactly as it was, and Esc steps back to
    the previous question — both because the question half touches nothing. A
    half-answered uninstall would be the worst of the available states.
    """
    prompter.intro("media-ai uninstall")
    summary: dict = {
        "ok": True, "schema_version": SCHEMA_VERSION, "command": "uninstall",
        "skills": [], "removed": [], "kept": [], "dry_run": bool(args.dry_run),
    }
    choices = _Choices()
    run_steps(
        [
            lambda: _ask_skills(args, prompter, choices),
            *(
                partial(_ask_file, args, prompter, choices, flag, where(), what)
                for flag, where, what in CONFIG_FILES
            ),
        ],
        prompter,
    )

    # -- execute ---------------------------------------------------------
    summary["kept"] += choices.kept
    doomed: list[Path] = []
    for flag, where, _what in CONFIG_FILES:
        path = where()
        if choices.files.get(flag):
            doomed += [path, *_backups_of(path)]
        elif path.is_file():
            summary["kept"].append(str(path))

    _remove_skills(choices.skills, summary, dry_run=args.dry_run)
    for path in doomed:
        _remove_file(path, summary, dry_run=args.dry_run)
    if not args.dry_run and not receipt_path().is_file():
        prune_empty(config_path().parent)

    summary["remove_cli"] = _remove_cli_hint()
    _report(summary, prompter)
    return summary


def _remove_cli_hint() -> str:
    """How to remove the CLI itself — which this command deliberately does not do.

    Deleting the package out from under the interpreter that is running it works on
    POSIX but is not something to do on a user's behalf, and the installer is the
    piece that knows how it was installed. ``uv tool`` puts each tool in its own
    environment under ``…/uv/tools/<name>``, which is what makes it recognisable here.
    """
    parts = Path(sys.prefix).resolve().parts
    if "uv" in parts and "tools" in parts:
        return "uv tool uninstall media-ai"
    return "pip uninstall media-ai"


def _report(summary: dict, prompter) -> None:
    verb = "would remove" if summary["dry_run"] else "removed"
    for entry in summary["skills"]:
        if entry["removed"]:
            prompter.note(f"{verb} {len(entry['removed'])} skill(s) from {entry['dest']}")
    for path in summary["removed"]:
        prompter.note(f"{verb} {path}")
    for path in summary["kept"]:
        prompter.note(f"kept    {path}")
    prompter.note(f"\nThe media-ai CLI itself is still installed. To remove it:\n  {summary['remove_cli']}")
    prompter.outro("Dry run — nothing was changed." if summary["dry_run"] else "Done.")


# -------------------------------------------------------------------- entry


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="media-ai uninstall",
        description="Remove installed Agent Skills and the configuration files, so a later install starts fresh.",
    )
    ap.add_argument("--skills-dest", action="append", default=None,
                    help="only look here (repeatable); default: the install receipt + the usual agent directories")
    ap.add_argument("--keep-skills", action="store_true", help="leave installed Agent Skills in place")
    ap.add_argument("--keep-config", action="store_true", help="leave config.toml (model defaults, profiles)")
    ap.add_argument("--keep-credentials", action="store_true", help="leave credentials.toml (API keys)")
    ap.add_argument("--yes", "-y", action="store_true", help="don't ask; remove everything not kept by a flag")
    ap.add_argument("--dry-run", action="store_true", help="report what would be removed without removing it")
    ap.add_argument("--pretty", action="store_true", help="pretty-print the JSON result")
    ap.add_argument("--log-level", default=None, help="stderr log level: debug, info, warning, or error")
    ap.add_argument("--metadata-out", default=None, help="also write the secret-free result JSON to this path")
    return ap


def _do(args) -> dict:
    prompter = get_prompter(force_fallback=args.yes)
    try:
        return _uninstall(args, prompter)
    except Cancelled:
        from ..core.errors import ErrorCategory, MediaError

        # Truthful because every prompt is answered before the first deletion.
        raise MediaError("uninstall cancelled; nothing was removed", category=ErrorCategory.CLI) from None


def main() -> int:
    args = common.parse_args(_build_parser())
    return common.run(_do, args)


if __name__ == "__main__":
    raise SystemExit(main())
