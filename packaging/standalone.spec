# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the standalone build — the CLI plus the interpreter it needs.

The install path this exists for is "no Python on the machine, and none wanted".
``uv tool install`` is a fine way to get a Python CLI onto a developer's laptop and a
poor way to get a tool onto a box whose owner does not think of themselves as a Python
user: it fetches an interpreter, resolves a dependency tree, and turns a network
problem three layers down into a failed install. A bundle has none of those failure
modes because it has none of those steps — one tarball, one directory, one symlink.

What has to be told to PyInstaller, and why each is not optional here:

``datas`` — the manifests and the skills
    Binding manifests *are* the capability declaration the CLI reads at runtime
    (:mod:`media_ai.core.binding`), so a bundle without them knows about no models at
    all; the packaged Agent Skills are what ``init`` copies out. Both are non-``.py``
    files inside the package, which is exactly what ``collect_data_files`` finds and
    exactly what a bytecode-only freeze would otherwise drop.

``datas`` — the installer
    A standalone build has no package manager to hand ``upgrade`` and ``uninstall``
    over to, so it carries the script that installed it and hands the work to that
    instead (:mod:`media_ai.cli._install`). Shipping it rather than fetching it means
    the upgrade path is not "a program pipes curl into a shell".

``hiddenimports`` — every adapter
    Adapters are named as strings in the manifests (``adapter = "media_ai.providers.…"``)
    and imported lazily, which is the whole reason a binding can live in a private
    package. Static analysis cannot see through that, so a bundle built without this
    lists every binding in ``capabilities`` and fails to import all but the two local
    ones. ``collect_submodules`` takes the package wholesale rather than naming the
    providers, so adding one needs no edit here — the same rule the rest of the repo
    follows about not keeping a second list of the models.

Which optional extras are inside is **not** decided here — it is decided by what
``packaging/build.sh`` installs (``BUNDLE_EXTRAS``), because that is what the hooks and
``collect_submodules`` read. Today that is ``otel`` and not ``keychain``; the reasoning
for each is written down beside the variable, and ``docs/LIMITATIONS.md`` says which
is which for users, since a difference between two install methods that nobody wrote
down is a bug report waiting to happen.

Nothing here names OpenTelemetry either, and that is deliberate rather than an
oversight: pyinstaller-hooks-contrib ships a hook that collects its entry-point groups
(the SDK resolves its context runtime and its exporters through
``importlib.metadata``, which a freeze does not carry unless told to). Listing them
again here would be a second copy that goes stale against the hook. What proves it
worked is ``build.sh``'s telemetry check, which exports a real span from the finished
bundle — the failure mode being that telemetry degrades *politely* to a no-op, so a
bundle that stopped collecting it would pass every other check.

Run it through ``packaging/build.sh``, which is what pins the toolchain, sets the
output paths and smoke-tests the result. Invoking ``pyinstaller`` on this file directly
works and skips all three.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

# The package is imported rather than parsed, so the executable inside the tarball is
# named by the same constant as the one inside the wheel. `SPECPATH` is PyInstaller's;
# `__file__` is not defined while a spec is being executed.
from media_ai.brand import CLI_NAME, dist_name

ROOT = Path(SPECPATH).parent  # noqa: F821 - injected by PyInstaller

a = Analysis(  # noqa: F821
    [str(Path(SPECPATH) / "entry.py")],  # noqa: F821
    pathex=[],
    binaries=[],
    datas=[
        # Non-`.py` files under `media_ai/`: `bindings/*.toml` and `skills/**/*.md`.
        # Collected as a package rather than as a glob so a new resource directory is
        # picked up without an edit, matching `[tool.setuptools.package-data]`.
        *collect_data_files("media_ai"),
        # `importlib.metadata` finds nothing in a bundle unless the dist-info comes
        # too. Nothing in the CLI needs it today; a `PackageNotFoundError` from a
        # dependency that does would be a puzzling way to learn that.
        *copy_metadata(dist_name()),
        # `.` is the bundle's data root — `sys._MEIPASS`, where `_install.detect()`
        # looks for it.
        (str(ROOT / "install" / "install.sh"), "."),
    ],
    hiddenimports=collect_submodules("media_ai"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Pillow pulls `tkinter` in through `ImageTk`, which is guarded and never reached
    # here; the rest are build-time tooling that a wheel install would not have either.
    # `unittest` is *not* excluded — it is small, and several stdlib modules import it
    # on paths that are hard to predict from here.
    excludes=["tkinter", "_tkinter", "test", "pydoc_data", "setuptools", "pkg_resources", "pip", "wheel", "pytest", "_pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=CLI_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    # Not stripped. It saves a few megabytes against a bundle whose bulk is a 78 MB
    # ffmpeg, and on macOS `strip` invalidates the ad-hoc signature that arm64
    # requires — a binary that refuses to start is a poor trade for 3% of a download.
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # native; the release builds one bundle per runner architecture
    codesign_identity=None,  # ad-hoc, which is all macOS arm64 requires to execute
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=CLI_NAME,
)
