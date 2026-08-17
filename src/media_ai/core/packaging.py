"""Facts about how this build was *packaged*, for the code that has to phrase a hint.

There are two shapes an installation of this CLI can have, and they differ in one way
that reaches the user: a **standalone bundle** carries its own interpreter and cannot
be added to. ``pip install '<dist>[otel]'`` is a perfectly good instruction for a wheel
in a virtualenv and a misleading one for a bundle — it would install the package into
some other Python, leave the bundle exactly as it was, and the collector would stay
empty. This project documents ``error.hint`` and ``notices[].action`` as *usually
runnable*, and an agent runs whatever appears in one, so the extras hint has to know
which installation it is talking about.

This module is where that question lives, rather than in each of the three places that
asks it (``credentials/reference.py`` for ``keychain``, ``cli/doctor.py`` and
``core/telemetry/runtime.py`` for ``otel``). It sits in ``core/`` because those callers
sit on both sides of the CLI boundary — ``credentials/`` and ``core/telemetry/`` are
below it — and because it imports nothing but the standard library and
:mod:`media_ai.brand`.

:mod:`media_ai.cli._install` is the *other* half of the same subject and deliberately
stays separate: it answers "which package manager owns this, and what do I run to
upgrade or remove it", which is a question with four answers and a policy attached. It
resolves the frozen one through :func:`is_standalone` here, so there is still exactly
one place that decides whether this is a bundle.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from ..brand import dist_name

__all__ = ["BUNDLED_INSTALLER", "bundle_root", "bundled_installer", "extra_hint", "is_standalone"]

#: The installer, as ``packaging/standalone.spec`` collects it into a bundle. A relative
#: name, resolved against :func:`bundle_root`, so nothing here has to know where
#: PyInstaller decided to put the data directory.
BUNDLED_INSTALLER = "install.sh"


def is_standalone() -> bool:
    """Whether this process is a frozen bundle rather than an installed package.

    ``sys.frozen`` is what PyInstaller's bootloader sets, and it is the only honest
    signal: inside a bundle ``sys.prefix``, ``sys.executable`` and ``__file__`` all
    point at paths that exist because the bundle exists, so every other test answers a
    question about the freeze rather than about the installation.
    """
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path | None:
    """The bundle's data directory, or ``None`` when this is not a bundle.

    ``sys._MEIPASS`` is ``_internal/`` beside the executable for a one-directory build
    and an unpacked temporary directory for a one-file one. Both are where the spec's
    ``datas`` were collected to.
    """
    root = getattr(sys, "_MEIPASS", None) if is_standalone() else None
    return Path(root) if root else None


def bundled_installer() -> Path | None:
    """The installer that produced this bundle, if it came with one.

    Absent for a bundle built some other way — an older release, or somebody's
    ``pyinstaller`` run — which is why every caller has to have an answer for ``None``
    rather than formatting the path into a string and hoping.
    """
    root = bundle_root()
    if root is None:
        return None
    script = root / BUNDLED_INSTALLER
    return script if script.is_file() else None


def extra_hint(extra: str) -> str:
    """A runnable command for getting one optional extra onto *this* installation.

    For a package, that is pip. For a bundle it is not: there is no environment to add
    a dependency to, and the extras are deliberately not frozen in (see
    ``docs/LIMITATIONS.md`` — an install that never exports should not carry an
    OpenTelemetry tree several times its own size, and a bundle would make that choice
    for everybody permanently). So the answer is to swap the bundle for a source
    install, which is a thing the installer it shipped with does in one flag.

    The command is returned bare, with no explanation attached: every caller already
    says what the extra is for, and a ``#`` comment inside an ``error.hint`` is one more
    thing for a consumer to strip.
    """
    if not is_standalone():
        return f"pip install '{dist_name()}[{extra}]'"
    script = bundled_installer()
    if script is None:
        # No installer to name, so no command to give. Saying what has to happen beats
        # printing a `pip` line that would install the package somewhere else entirely
        # and change nothing about the build that printed it.
        return f"reinstall {dist_name()} from source; a standalone build cannot add extras"
    return f"bash {shlex.quote(str(script))} --from-source"
