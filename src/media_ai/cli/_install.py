"""How this CLI got here, and therefore how to change it.

One question — *which package manager owns this installation* — with two callers that
want opposite things from the answer: ``uninstall`` prints how to remove the CLI, and
``version`` prints how to upgrade it. They were going to detect it separately, and a
pair of detectors that disagree would tell a user to remove it one way and upgrade it
another.

**Nothing here runs anything.** Detection is a read of ``sys.prefix``; producing a
command is assembling argv. Replacing the package that is currently executing is the
package manager's job — ``<cli> upgrade`` hands these steps to it and does no
substitution of its own, so a failed upgrade is a package manager's error message
rather than an installation in a state nobody can describe.

An editable checkout is a *third* answer, not a failure to detect one of the first two:
somebody working on the tool should be told to pull, not to install it over the top of
their own work tree.
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from ..brand import dist_name

__all__ = ["Install", "detect"]


@dataclass(frozen=True)
class Install:
    """Where this build lives and what manages it.

    ``method`` is a closed set — ``uv-tool``, ``pip``, ``editable``, ``unknown`` — so a
    caller branches on it rather than on the prose.
    """

    method: str
    prefix: str

    def upgrade_steps(self, repo: str, version: str | None = None) -> list[list[str]] | None:
        """The commands to run, as argv, or ``None`` when running them is not ours to do.

        Argv rather than a shell string, and this is the half that executes: a string
        needs quoting, and hand-written quoting is the kind of mistake that only shows
        up on the machine where a path had a space in it. :meth:`upgrade_command`
        renders these back for display, so what is printed is what would run.

        ``None`` covers two different situations, and the caller reports them
        differently. An **editable** checkout has a perfectly good upgrade — ``git
        pull`` — that this tool must not run: somebody's work tree and git state are
        theirs, and a stash conflict caused by a CLI helpfully pulling is a bad
        afternoon. An **unknown** install has no command at all.

        pip is invoked as ``<this interpreter> -m pip`` rather than as ``pip``, because
        which environment gets upgraded is exactly the question a bare ``pip`` on
        ``PATH`` answers wrongly.
        """
        source = f"git+https://github.com/{repo}{f'@v{version}' if version else ''}"
        if self.method == "uv-tool":
            return [["uv", "tool", "install", "--force", source]]
        if self.method == "pip":
            return [[sys.executable, "-m", "pip", "install", "--upgrade", f"{dist_name()} @ {source}"]]
        return None

    def upgrade_command(self, repo: str, version: str | None = None) -> str | None:
        """How to move this installation to ``version``, or ``None`` if we cannot say.

        ``None`` rather than a guess: a command that does not work is worse than no
        command, because this project's hints are documented as usually runnable and an
        agent will run whatever appears in one.
        """
        if self.method == "editable":
            return "git pull && uv sync"
        steps = self.upgrade_steps(repo, version)
        return " && ".join(shlex.join(step) for step in steps) if steps else None

    def remove_command(self) -> str:
        """How to remove the CLI itself, which no command here does on the user's behalf.

        ``editable`` deliberately gets the same answer as ``pip``, unlike
        :meth:`upgrade_command`. Removing an editable install *is* ``pip uninstall`` —
        it drops the link and leaves the work tree alone — while upgrading one over the
        top would install a release on top of the code somebody is editing. The two
        questions only look like the same question.
        """
        if self.method == "uv-tool":
            return f"uv tool uninstall {dist_name()}"
        return f"pip uninstall {dist_name()}"


def detect() -> Install:
    """Which manager owns this installation.

    ``uv tool`` gives each tool its own environment under ``…/uv/tools/<name>``, which
    is what makes it recognisable. An editable install leaves the package importable
    from a work tree rather than from ``site-packages``, so the giveaway is this
    module's own location: if ``…/src/media_ai`` is not inside ``sys.prefix``, the code
    being run is somebody's checkout.
    """
    prefix = Path(sys.prefix).resolve()
    here = Path(__file__).resolve()
    if "uv" in prefix.parts and "tools" in prefix.parts:
        return Install("uv-tool", str(prefix))
    if not here.is_relative_to(prefix):
        return Install("editable", str(prefix))
    return Install("pip", str(prefix))
