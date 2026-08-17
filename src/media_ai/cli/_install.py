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

A **standalone** build is the fourth, and it is the one where the question is not about
a package manager at all: there is none. The bundle carries its own interpreter, so
``sys.executable`` is the CLI rather than a python, ``sys.prefix`` is a directory that
only exists while the process runs, and nothing on the machine has a record of a
``media-ai`` package. The installer that put it there is the only thing that knows the
layout — so it ships *inside* the bundle and both commands hand the work back to it,
which is the same restraint the other three methods show towards pip and uv.

Whether this *is* a bundle is asked of :mod:`media_ai.core.packaging` rather than
answered here, because two other places need the same answer for a different purpose —
the extras hints in ``credentials/`` and ``core/telemetry/``, both of which sit below
the CLI. This module keeps the half with a policy attached: which manager owns the
install, and what to run.
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from ..brand import dist_name
from ..core.packaging import bundled_installer, is_standalone

__all__ = ["Install", "detect"]


@dataclass(frozen=True)
class Install:
    """Where this build lives and what manages it.

    ``method`` is a closed set — ``uv-tool``, ``pip``, ``editable``, ``standalone``,
    ``unknown`` — so a caller branches on it rather than on the prose.

    ``installer`` is the path to the bundled installer, and is set for ``standalone``
    alone. It is a *field* rather than something the methods go and look for, so that
    the promise in this module's header — nothing here runs anything, and nothing here
    touches the filesystem twice for one question — keeps holding: :func:`detect` does
    the one probe, and every command derived from it is pure.
    """

    method: str
    prefix: str
    installer: str | None = None

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
        ``PATH`` answers wrongly. A standalone bundle has no interpreter to offer and no
        package manager that has heard of it, so it runs the installer it shipped with —
        ``bash <bundle>/install.sh``, argv like every other step, no curl piped into a
        shell from inside a program. The interpreter is named: the script is bash (it
        uses ``pipefail`` and arrays), and ``sh`` is dash on Debian and Ubuntu, where it
        dies on line one. That installer unpacks the new release into a
        directory of its own and moves a symlink, which is what makes replacing a
        *running* build safe: nothing under this process's feet is overwritten.

        ``repo`` is unused for a standalone install and that is deliberate rather than
        an oversight — the bundled installer names the repository it came from, and a
        build that fetched its releases from somewhere other than where its installer
        points would be the more surprising of the two.

        Nothing here passes ``--bin-dir`` or a home directory either, and that is the
        same kind of deliberate. A bundled installer sits at
        ``<home>/versions/<version>/_internal/install.sh``, so it works out which
        installation it belongs to from its own path and reads the bin directory back
        from the receipt the first install wrote. Passing them from here would be a
        second opinion about a layout this side does not own — and the fields it would
        have to guess from (``prefix``) are exactly the ones a non-default
        ``--bin-dir`` or ``MEDIA_AI_HOME`` makes wrong.
        """
        source = f"git+https://github.com/{repo}{f'@v{version}' if version else ''}"
        if self.method == "uv-tool":
            return [["uv", "tool", "install", "--force", source]]
        if self.method == "pip":
            return [[sys.executable, "-m", "pip", "install", "--upgrade", f"{dist_name()} @ {source}"]]
        if self.method == "standalone" and self.installer:
            # No `--version` when there is none to name: the installer then resolves the
            # latest release itself, which is the same answer this would have reached.
            return [["bash", self.installer, "--no-init", *(["--version", f"v{version}"] if version else [])]]
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

        A standalone bundle is a directory and a symlink rather than a package, so the
        answer is its installer again — the one thing that knows both paths. Without a
        bundled installer to name there is nothing runnable to print, and the fallback
        says where the files are instead of inventing an ``rm -rf``.
        """
        if self.method == "uv-tool":
            return f"uv tool uninstall {dist_name()}"
        if self.method == "standalone":
            if self.installer:
                return f"bash {shlex.quote(self.installer)} --uninstall"
            return f"remove {self.prefix} and the {dist_name()} symlink on your PATH"
        return f"pip uninstall {dist_name()}"


def detect() -> Install:
    """Which manager owns this installation.

    A **standalone** bundle answers first and answers cheaply: PyInstaller sets
    ``sys.frozen``, and once it is set the other three tests are all meaningless —
    ``sys.prefix`` points into the bundle, so the ``pip`` branch below would match and
    report a package manager that has never heard of this install. The prefix reported
    is the directory the executable lives in (``…/versions/<version>``), which is the
    thing a user can look at, rather than the data directory beside it.

    ``uv tool`` gives each tool its own environment under ``…/uv/tools/<name>``, which
    is what makes it recognisable. An editable install leaves the package importable
    from a work tree rather than from ``site-packages``, so the giveaway is this
    module's own location: if ``…/src/media_ai`` is not inside ``sys.prefix``, the code
    being run is somebody's checkout.
    """
    if is_standalone():
        # Both facts come from `core.packaging`, which is the one place that decides
        # whether this is a bundle — the extras hints in `credentials/` and
        # `core/telemetry/` need the same answer and sit below this module.
        script = bundled_installer()
        return Install(
            "standalone",
            str(Path(sys.executable).resolve().parent),
            installer=str(script) if script else None,
        )
    prefix = Path(sys.prefix).resolve()
    here = Path(__file__).resolve()
    if "uv" in prefix.parts and "tools" in prefix.parts:
        return Install("uv-tool", str(prefix))
    if not here.is_relative_to(prefix):
        return Install("editable", str(prefix))
    return Install("pip", str(prefix))
