"""Agent Skills as they exist **on disk**: where they go, how they get there, and
how they come back out.

:mod:`media_ai.cli._discovery` covers what ships *inside* the package. This is the
other half — the copies ``init`` writes into an agent's skills directory and
``uninstall`` removes.

Uninstall needs to find those copies, and the destinations are open-ended (several
agent conventions × user or project level, plus any custom path the wizard was
handed). Guessing from the conventions alone would miss a custom path, and walking
the filesystem for one would be both slow and alarming. So installs are **recorded**:
a small non-secret receipt beside ``config.toml`` naming every directory written to.
The conventional locations are still scanned as well, so a hand-copied skill — or one
installed before the receipt existed — is still found.

Removal is deliberately narrow. It touches only ``<brand>-*`` directories that hold
a ``SKILL.md``, refuses anything else, and unlinks rather than deletes through a
symlink — installing by symlink is no longer supported (the packaged tree is a
template), but links left over from when it was must still come out, and following one
would delete the user's checkout.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .. import __version__
from ..brand import cli_name, skill_prefix
from ..core.config import config_path
from ..core.errors import ErrorCategory, MediaError
from ..core.logging import get_logger
from ..credentials.tomlwrite import dumps, write_public
from ._discovery import skill_root
from ._render import render

__all__ = [
    "SKILL_DESTS",
    "receipt_header",
    "copy_skill",
    "installed_skills",
    "known_dests",
    "load_receipt",
    "receipt_path",
    "install_roots",
    "record_install",
    "remove_skill",
    "skill_is_current",
]


@dataclass(frozen=True)
class AgentDir:
    """One agent convention that reads ``SKILL.md`` directories.

    Carries who reads it, not just where: a menu row saying only ``~/.trae/skills``
    asks the user to already know what Trae is and which of these paths is the one
    their agent looks at.
    """

    key: str
    segment: str
    name: str  #: short enough for a menu row
    who: str  #: a sentence fragment — "read by <who>"


# Adding a convention is one row; the layouts are assumed identical
# (<root>/skills/<name>/SKILL.md) until shown otherwise.
SKILL_DESTS = (
    AgentDir("claude", ".claude/skills", "Claude Code", "Claude Code"),
    AgentDir("agents", ".agents/skills", "AGENTS.md", "any agent following the AGENTS.md convention"),
    AgentDir("codex", ".codex/skills", "Codex", "Codex"),
    AgentDir("trae", ".trae/skills", "Trae", "Trae"),
    AgentDir("openclaw", ".openclaw/skills", "OpenClaw", "OpenClaw"),
)


def receipt_header() -> str:
    """The comment block at the top of the install receipt.

    A function rather than a constant because it names the CLI, and a module-level
    constant would freeze the brand at import time — invisible in the default build and
    wrong in exactly the case this indirection exists for.
    """
    cli = cli_name()
    return (
        f"{cli} skill install receipt — written by `{cli} init`.\n"
        f"NON-SECRET. Records where Agent Skills were copied so `{cli} uninstall`\n"
        "can find them again, including custom paths. Safe to delete; uninstall then\n"
        "falls back to scanning the conventional locations."
    )


def known_dests() -> list[Path]:
    """Every conventional skills directory, at user and project level.

    Returned whether or not they exist — callers filter on what they actually find,
    and an empty answer here would make an uninstall silently no-op.
    """
    seen: dict[Path, None] = {}
    for agent in SKILL_DESTS:
        for base in (Path.home(), Path.cwd()):
            seen.setdefault(base / agent.segment)
    return list(seen)


def install_roots() -> list[Path]:
    """Every directory that might hold installed skills, deduplicated, receipt first.

    One implementation because two commands have to agree on it: `doctor` blessing an
    install `uninstall` cannot find is exactly what a second copy of this loop drifts
    into. The receipt leads because it knows about custom paths; the conventional
    locations follow so a hand-copied install is still found.
    """
    roots = [Path(p).expanduser() for p in load_receipt()] + known_dests()
    seen: dict[str, Path] = {}
    for root in roots:
        seen.setdefault(str(root.resolve()), root)
    return list(seen.values())


# ------------------------------------------------------------------------ install


def copy_skill(name: str, dest_root: Path) -> list[Path]:
    """Install one packaged skill into ``dest_root/<name>``. Returns the files written.

    A *sync*, not a copy: files the packaged skill no longer ships are removed. Only
    adding would mean a reference file dropped in a release lingers forever — and
    since "is this copy current?" compares whole trees, the leftover would make the
    skill permanently look modified, so every later run would re-ask about a
    collision that updating cannot resolve.
    """
    written: list[Path] = []
    target_root = dest_root / name
    # An existing symlink (the manual install in skills/README.md) is replaced rather
    # than written through — and a *dangling* one has to go before mkdir, which does
    # not treat a broken link as an existing directory and would raise FileExistsError
    # halfway through the apply phase.
    if target_root.is_symlink() or (target_root.exists() and not target_root.is_dir()):
        target_root.unlink()

    def walk(src, out: Path):
        # A plain file where a packaged directory belongs: mkdir(exist_ok=True) raises
        # on it, mid-apply, after earlier skills were already written. The file branch
        # below handles the mirror case, so this is the same rule in both directions.
        if out.is_symlink() or (out.exists() and not out.is_dir()):
            out.unlink()
        out.mkdir(parents=True, exist_ok=True)
        packaged = set()
        for entry in src.iterdir():
            target = out / entry.name
            packaged.add(entry.name)
            if entry.is_dir():
                walk(entry, target)
            else:
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                elif target.is_symlink():
                    target.unlink()
                target.write_text(render(entry.read_text(encoding="utf-8")), encoding="utf-8")
                written.append(target)
        for stale in out.iterdir():
            if stale.name not in packaged:
                stale.unlink() if stale.is_symlink() or stale.is_file() else shutil.rmtree(stale)

    walk(skill_root(name), target_root)
    return written


@cache
def _packaged_source(name: str) -> dict[str, str]:
    """The packaged skill's files **as templates**, read once per process.

    Every (destination, skill) pair compares against the same packaged tree, and it
    may live inside a zip — re-reading it per pair turns a ten-skill install into
    twenty reads of the same files. Cached unrendered so the cache cannot outlive a
    change of brand; rendering it again is a string substitution over a handful of
    small files, which is not what this cache was protecting.
    """
    return _tree(skill_root(name))


def _packaged_tree(name: str) -> dict[str, str]:
    """What an up-to-date install of ``name`` looks like on disk.

    Rendered, because that is what :func:`copy_skill` writes. Comparing the raw
    templates instead would report every installed skill as modified forever, and
    ``copy_skill`` is a sync — so the installer would re-ask, on every run, about a
    collision that updating cannot resolve.
    """
    return {path: render(text) for path, text in _packaged_source(name).items()}


def _tree(root) -> dict[str, str]:
    """``{relative path: text}`` for every file under ``root``, recursively.

    Untyped on purpose: the two sides being compared are different things — the
    packaged skill is an ``importlib.resources`` Traversable (it may live inside a
    zip), the installed one is a real ``Path`` — and both answer the same four calls.
    """
    out: dict[str, str] = {}

    def walk(node, prefix: str) -> None:
        for entry in node.iterdir():
            name = f"{prefix}{entry.name}"
            if entry.is_dir():
                walk(entry, f"{name}/")
            else:
                out[name] = entry.read_text(encoding="utf-8")

    walk(root, "")
    return out


def skill_is_current(dest: Path, name: str) -> bool:
    """Whether the copy in ``dest`` is byte-for-byte the packaged skill.

    This is what makes re-running the installer quiet: a skill that already matches
    is neither written nor asked about, so a second run with nothing to change asks
    nothing and touches nothing.

    A symlink used to be current by construction — it *was* the packaged directory.
    That stopped being true when the packaged tree became a template: a link to it puts
    a literal ``{{cli}}`` in front of the agent, so it is compared like anything else
    and comes back not-current, which is the honest answer. A dangling link fails the
    read below and lands in the same place, rather than `doctor` blessing a skill the
    agent cannot read.
    """
    target = dest / name
    if not target.is_dir():
        return False
    try:
        return _packaged_tree(name) == _tree(target)
    except (OSError, UnicodeDecodeError):
        # Unreadable or not text: treat as drifted, so the caller offers to refresh it.
        return False


def installed_skills(dest: Path) -> list[str]:
    """The ``<brand>-*`` skills present in ``dest``, sorted.

    A dangling symlink counts: it was installed by hand and should still be
    removable. Anything else must carry a ``SKILL.md`` to be recognised, so an
    unrelated directory that happens to match the prefix is never claimed.
    """
    try:
        entries = sorted(dest.iterdir())
    except (OSError, NotADirectoryError):
        return []
    return [
        p.name
        for p in entries
        if p.name.startswith(skill_prefix()) and (p.is_symlink() or (p.is_dir() and (p / "SKILL.md").is_file()))
    ]


# ----------------------------------------------------------------------- uninstall


def remove_skill(dest: Path, name: str) -> bool:
    """Delete one installed skill; returns whether anything was there to delete.

    Refuses a name that is not a ``<brand>-*`` leaf, and refuses a directory with no
    ``SKILL.md`` in it — a wrong ``--skills-dest`` should fail loudly, not recursively
    delete whatever it was pointed at.

    Because the prefix is branded, this is also what scopes an uninstall to **its own**
    build: a ``foo`` install will not remove the ``media-ai-*`` skills sitting beside it,
    which is what lets two brands share an agent's skills directory. The other build's
    copies are removed by running *its* ``uninstall`` — nothing here can safely delete a
    directory it has no evidence it wrote.
    """
    prefix = skill_prefix()
    if not name.startswith(prefix) or "/" in name or name in ("", ".", ".."):
        raise MediaError(
            f"refusing to remove {name!r}: not a {prefix}* skill directory", category=ErrorCategory.CLI
        )
    target = dest / name
    if target.is_symlink():
        target.unlink()  # never rmtree through a link: the target may be a git checkout
        return True
    if not target.is_dir():
        return False
    if not (target / "SKILL.md").is_file():
        raise MediaError(
            f"refusing to remove {target}: no SKILL.md, so it is not an installed skill",
            category=ErrorCategory.CLI,
        )
    shutil.rmtree(target)
    return True


def prune_empty(path: Path) -> bool:
    """Remove ``path`` if it is an empty directory. Never recurses, never forces."""
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            return True
    except OSError as exc:  # a directory we cannot clean up is not a failed uninstall
        get_logger().debug("could not remove empty directory %s: %s", path, exc)
    return False


# ------------------------------------------------------------------------- receipt


def receipt_path() -> Path:
    """Beside ``config.toml``, so ``$MEDIA_AI_CONFIG_FILE`` relocates the whole set."""
    return config_path().parent / "installed-skills.toml"


def load_receipt() -> dict[str, dict]:
    """``{dest: {"skills": [...], "version": "..."}}``, or ``{}``.

    A corrupt receipt is a warning, not an error: it is a convenience index, and
    refusing to uninstall because of it would be the wrong trade — the conventional
    locations are still scanned.
    """
    path = receipt_path()
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        get_logger().warning("ignoring unreadable install receipt %s: %s", path, exc)
        return {}
    dests = data.get("dests")
    return {k: v for k, v in dests.items() if isinstance(v, dict)} if isinstance(dests, dict) else {}


def record_install(dests: list[Path]) -> Path | None:
    """Record what each destination now holds. Returns the receipt path, or ``None``.

    Stores what is *actually on disk* rather than what this run copied, so a second
    ``init`` adding one skill to a directory does not shrink the receipt to that one
    skill — and so a skill removed by hand drops out of it.
    """
    entries = load_receipt()
    for dest in dests:
        skills = installed_skills(dest)
        if skills:
            entries[str(dest)] = {"skills": skills, "version": __version__}
        else:
            entries.pop(str(dest), None)
    return _write_receipt(entries)


def _write_receipt(entries: dict[str, dict]) -> Path | None:
    path = receipt_path()
    if not entries:
        # An empty receipt is indistinguishable from no receipt, and leaving a stub
        # behind after a full uninstall is exactly the litter uninstall is for.
        path.unlink(missing_ok=True)
        return None
    try:
        write_public(path, dumps({"dests": entries}, header=receipt_header()))
    except OSError as exc:  # an unwritable receipt must not fail the install
        get_logger().warning("could not write install receipt %s: %s", path, exc)
        return None
    return path
