"""The standalone build: the three files that have to agree, and the fourth answer to
"how was this installed".

A bundle is the default way to get this CLI onto a machine, and it is assembled by
parts that cannot see each other. ``packaging/build.sh`` names a tarball;
``install/install.sh`` asks a release for one by name; ``packaging/standalone.spec``
decides what goes inside it; and :mod:`media_ai.cli._install`, running *from* inside it,
decides how to upgrade and remove it. Nothing at build time checks any of that — a
mismatch surfaces as a 404 during somebody's install, or as an ``upgrade`` that does
nothing, both a long way from the edit that caused them.

So the seams are pinned here:

1. The naming block is duplicated verbatim between the builder and the installer,
   because they cannot share a file — one is fetched alone by curl. Compared byte for
   byte.
2. The spec collects the installer under the name ``_install`` looks for, and names the
   executable from :data:`media_ai.brand.CLI_NAME` rather than spelling it out.
3. ``detect()`` recognises a frozen build, and the commands it derives are runnable
   (``bash``, not ``sh``: the installer is a bash script, and ``sh`` is dash on Debian).

What is deliberately *not* here is a build. Freezing takes minutes and a hundred
megabytes; ``packaging/build.sh`` ends with a smoke test of the bundle it just made, CI
runs it on Linux for every pull request, and the release workflow runs it on all four
platforms before a tag exists. This file is the part that can be answered in
milliseconds, offline, on every commit.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from media_ai import brand
from media_ai.cli import _install
from media_ai.core import packaging

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install" / "install.sh"
BUILDER = ROOT / "packaging" / "build.sh"
SPEC = ROOT / "packaging" / "standalone.spec"
ENTRY = ROOT / "packaging" / "entry.py"

needs_checkout = pytest.mark.skipif(
    not INSTALLER.is_file(), reason="running against an installed package, not a checkout"
)

SHARED_BLOCK = re.compile(r"^# >>> asset naming >>>$.*?^# <<< asset naming <<<$", re.M | re.S)


def shared_block(path: Path) -> str:
    match = SHARED_BLOCK.search(path.read_text(encoding="utf-8"))
    assert match, f"{path.name} no longer delimits the shared naming block"
    return match.group(0)


# ------------------------------------------------- 1. the builder and the installer


@needs_checkout
def test_the_asset_naming_block_is_identical_in_both_scripts():
    """One decision, written twice because the two files cannot share anything.

    The installer is fetched on its own by ``curl`` and the builder runs from a
    checkout, so there is no third file either could source. A drift here is a release
    whose assets are named one way and requested another — which looks, from the
    outside, exactly like a release that published nothing for your platform.
    """
    assert shared_block(BUILDER) == shared_block(INSTALLER)


@needs_checkout
def test_both_scripts_use_the_shared_block_rather_than_repeating_it():
    """A second copy of the name outside the block would satisfy the check above and
    still be wrong. Neither script may spell the tarball's shape out again.

    ``version_from_asset`` is the one exemption, and it is the *inverse* rather than a
    second copy: it reads a version back out of a name ``asset_name`` wrote, for
    ``--from-file``. Pinning it by text would say nothing about whether the two agree,
    so ``install/test_installer.sh`` round-trips it against ``asset_name`` instead —
    the property that actually matters. It stays out of the shared block because the
    builder has no use for it, and a function nothing calls is one shellcheck flags.
    """
    inverse = re.compile(r"^version_from_asset\(\) \{.*?^\}$", re.M | re.S)
    for path in (BUILDER, INSTALLER):
        text = path.read_text(encoding="utf-8")
        scanned = inverse.sub("", text.replace(shared_block(path), ""))
        outside = [
            line for line in scanned.splitlines()
            # Comments and the usage text may describe the name; only code may not build it.
            if ".tar.gz" in line and not line.lstrip().startswith("#") and "asset_name" not in line
        ]
        assert not outside, f"{path.name} builds an asset name outside the shared block: {outside}"


#: A bare ``$name`` immediately followed by a byte outside ASCII. This is the shape of
#: the bug that broke the first real release: ``say "… for $triple…"`` works everywhere
#: it was tested and fails on macOS, where ``/bin/bash`` is 3.2 and scans a bare
#: ``$name`` byte by byte — taking the ellipsis's high bytes to be part of the
#: identifier, expanding an unset variable, and dying under ``set -u``.
_UNBRACED_BEFORE_NON_ASCII = re.compile(rb"\$[A-Za-z_][A-Za-z0-9_]*[\x80-\xff]")

SHELL_SCRIPTS = ("install/install.sh", "install/test_installer.sh", "packaging/build.sh")


@needs_checkout
def test_no_shell_variable_runs_into_a_non_ascii_character():
    """Brace it: ``${name}…``, never ``$name…``.

    Read as bytes and not as text, because the hazard *is* the bytes: what bash 3.2
    does with them is invisible in a decoded string. Comment lines are exempt — they do
    not execute, and the note explaining this rule quotes the broken form on purpose.

    This is not a style rule. It is the one class of defect in these files that every
    check the repo has will pass: shellcheck (any version) is happy, the offline suite
    never runs a shell script under macOS bash, and CI's own bundle job builds on Linux.
    The first thing that would have noticed was a release runner — which is exactly what
    did, after the tag it was meant to guard had already been merged toward.
    """
    offenders = []
    for name in SHELL_SCRIPTS:
        for number, line in enumerate((ROOT / name).read_bytes().splitlines(), 1):
            if line.lstrip().startswith(b"#"):
                continue
            if _UNBRACED_BEFORE_NON_ASCII.search(line):
                offenders.append(f"{name}:{number}: {line.decode('utf-8', 'replace').strip()}")
    assert not offenders, "brace these — macOS bash 3.2 reads the following bytes as part of the name:\n" + "\n".join(offenders)


#: ``source <(…)`` / ``. <(…)`` — reading a *script* out of a process substitution.
_SOURCED_PROCESS_SUBSTITUTION = re.compile(r"^\s*(?:source|\.)\s+<\(")


@needs_checkout
def test_no_script_is_sourced_out_of_a_process_substitution():
    """Write it to a file first.

    The second member of the same family as the rule above, and it cost the same way:
    ``source <(sed '$ d' install.sh)`` loaded the installer's functions for its unit
    tests, and bash 3.2 — macOS's — can reach the end of a ``/dev/fd`` stream before the
    writer has finished a script this long. The functions defined near the end are then
    absent, so the harness dies while loading its own subject, and every check it exists
    to run silently never happens. A regular file is complete before it is read.

    A ``while read … done < <(cmd)`` loop is *not* this shape and stays allowed: it reads
    to EOF and cares about the bytes rather than about parsing a complete program.
    """
    offenders = [
        f"{name}:{number}: {line.strip()}"
        for name in SHELL_SCRIPTS
        for number, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("#") and _SOURCED_PROCESS_SUBSTITUTION.search(line)
    ]
    assert not offenders, (
        "write the text to a temporary file and source that — bash 3.2 may read only "
        "part of a process substitution:\n" + "\n".join(offenders)
    )


@needs_checkout
def test_the_installer_is_sourced_at_its_entry_point_marker():
    """And the cut is made *at* ``main "$@"``, not at the last physical line.

    ``sed '$ d'`` is right only while nothing follows the entry point. Add a trailing
    comment — the most harmless edit there is — and running the unit tests performs a
    real install into the developer's home directory instead. So the marker is matched
    literally, and the test file refuses to load an installer that no longer carries it.
    """
    harness = (ROOT / "install" / "test_installer.sh").read_text(encoding="utf-8")
    # Comments are exempt for the reason they are exempt above: the note explaining this
    # rule quotes the broken form on purpose.
    code = "\n".join(l for l in harness.splitlines() if not l.lstrip().startswith("#"))
    assert "sed '$ d'" not in code, "the harness cuts at the last line again"
    assert 'ENTRY_POINT=\'main "$@"\'' in harness, "the harness no longer names the entry point it cuts at"
    assert 'grep -qxF "$ENTRY_POINT"' in harness, "a missing entry point must refuse, not fall through"
    assert INSTALLER.read_text(encoding="utf-8").rstrip().endswith('main "$@"'), (
        "install.sh no longer ends at the marker its unit tests cut on"
    )
    # The same line, for a second reason: under `curl … | bash` a transfer that dies
    # mid-file leaves a truncated *definition* that is never invoked, because the only
    # call is the last thing in the file.


@needs_checkout
def test_the_installer_still_offers_the_source_path():
    """The bundle cannot serve musl, an unlisted architecture, an extra it was not
    frozen with, or a third-party binding plugin. ``--from-source`` is the documented
    answer to all four, and every refusal in the installer points at it, so it has to
    keep existing."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "--from-source" in text
    assert "uv tool install --force" in text, "the source path no longer installs anything"


# ------------------------------------------------------------------ 2. the spec


@needs_checkout
def test_the_spec_names_the_executable_from_the_brand():
    """`pyproject.toml` has to repeat the name because it is static; the spec does not,
    because PyInstaller executes it as Python. So it imports the constant, and the
    binary inside the tarball is named by the same declaration as the console script."""
    text = SPEC.read_text(encoding="utf-8")
    assert "from media_ai.brand import CLI_NAME" in text
    assert "name=CLI_NAME" in text, "the frozen executable must be named from the constant"
    assert f'"{brand.CLI_NAME}"' not in text, "the spec spells the brand out somewhere"


@needs_checkout
def test_the_spec_bundles_the_installer_where_detect_looks_for_it():
    """The upgrade path is "run the script that put this here". If the spec stops
    collecting it, ``upgrade`` degrades to ``upgrade_not_supported`` on every standalone
    install — reported as a refusal, so nothing else would notice."""
    text = SPEC.read_text(encoding="utf-8")
    assert f'"install" / "{packaging.BUNDLED_INSTALLER}"' in text, (
        f"the spec must collect install/{packaging.BUNDLED_INSTALLER} into the bundle root"
    )


@needs_checkout
def test_the_spec_collects_the_package_data_and_the_lazily_imported_adapters():
    """Two things static analysis cannot find on its own, and each fails quietly.

    Without the data files a bundle knows about no models at all (the manifests *are*
    the capability declaration). Without ``collect_submodules`` every adapter named as
    a string in a manifest is missing, so ``capabilities`` lists bindings that cannot
    be called — and the two local ones still work, which is what makes it quiet.
    """
    text = SPEC.read_text(encoding="utf-8")
    assert 'collect_data_files("media_ai")' in text
    assert 'collect_submodules("media_ai")' in text


@needs_checkout
def test_the_frozen_entry_point_is_the_console_script():
    """A bundle has no console scripts, so `entry.py` is that shim written out. If it
    ever diverges from `[project.scripts]`, the two install methods run different code
    for the same command line."""
    assert "from media_ai.__main__ import main" in ENTRY.read_text(encoding="utf-8")


# ------------------------------------------------------- 3. detection, from inside


@pytest.fixture()
def frozen(monkeypatch, tmp_path):
    """Pretend to be a PyInstaller bundle laid out the way the installer lays one out."""
    version_dir = tmp_path / "versions" / "1.2.3"
    internal = version_dir / "_internal"
    internal.mkdir(parents=True)
    (version_dir / brand.CLI_NAME).write_text("", encoding="utf-8")
    (internal / packaging.BUNDLED_INSTALLER).write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(version_dir / brand.CLI_NAME))
    return version_dir


def test_a_frozen_build_is_not_reported_as_a_pip_install(frozen):
    """The failure this replaces: ``sys.prefix`` points *into* the bundle, so the old
    detector matched its ``pip`` branch and every command offered
    ``python -m pip install --upgrade`` — naming an interpreter that is not on the
    machine, for a package no index has heard of under a name nothing installed."""
    install = _install.detect()
    assert install.method == "standalone"
    assert install.prefix == str(frozen)
    assert install.installer == str(frozen / "_internal" / packaging.BUNDLED_INSTALLER)


def test_the_upgrade_runs_the_bundled_installer(frozen):
    steps = _install.detect().upgrade_steps("owner/repo", "1.3.0")
    assert steps == [["bash", str(frozen / "_internal" / packaging.BUNDLED_INSTALLER),
                      "--no-init", "--version", "v1.3.0"]]


def test_the_upgrade_names_no_version_when_there_is_none(frozen):
    """With no published version to aim at, the installer resolves the latest release
    itself — which is the same answer, reached one step later. Passing an empty
    ``--version`` would install a git ref literally named nothing."""
    steps = _install.detect().upgrade_steps("owner/repo", None)
    assert steps == [["bash", str(frozen / "_internal" / packaging.BUNDLED_INSTALLER), "--no-init"]]


def test_the_installer_is_run_with_bash_not_sh(frozen):
    """`sh` is dash on Debian and Ubuntu, and install.sh dies on `set -o pipefail`
    before it prints anything. The upgrade would report the shell's syntax error as a
    package-manager failure."""
    command = _install.detect().upgrade_command("owner/repo", "1.3.0")
    assert command is not None and command.startswith("bash ")


def test_removal_points_at_the_same_installer(frozen):
    assert _install.detect().remove_command().startswith("bash ")
    assert "--uninstall" in _install.detect().remove_command()


def test_a_bundle_without_its_installer_says_where_the_files_are(monkeypatch, tmp_path):
    """A hint is documented as usually runnable, so an unbundled installer must not
    produce ``bash None --uninstall``. There is no command to give, so it gives paths."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / brand.CLI_NAME))
    install = _install.detect()
    assert install.installer is None
    assert install.upgrade_steps("owner/repo", "1.3.0") is None
    assert "None" not in install.remove_command()


def test_an_unfrozen_run_is_unaffected():
    """The reference case: this test suite is not frozen, and must still be detected as
    whatever installed it rather than falling into the new branch."""
    assert _install.detect().method in {"pip", "editable", "uv-tool"}


# ----------------------------------------- 4. the extras: what is in, and what to say

# A bundle carries whichever extras it was frozen with and can never gain another, so
# `BUNDLE_EXTRAS` is a decision rather than a default — and the two places that describe
# it to a user (the builder's own comment block, and LIMITATIONS.md) are the ones that
# go stale. `otel` is checked by name because taking it back out is a real regression
# and a silent one: telemetry degrades politely to a no-op, so the CLI keeps working and
# only an operator who enables it ever finds out.


@needs_checkout
def test_the_bundle_ships_the_telemetry_sdk():
    declared = re.search(r'^BUNDLE_EXTRAS="\$\{MEDIA_AI_BUNDLE_EXTRAS:-([^}]*)\}"', BUILDER.read_text(), re.M)
    assert declared, "packaging/build.sh no longer declares BUNDLE_EXTRAS the way this test reads it"
    # Comma-separated, the syntax a requirement takes extras in. The gate on the
    # telemetry check matches commas too; splitting the two ways apart is how that check
    # would silently stop running the day a second extra is added.
    assert declared.group(1).split(",") == ["otel"], "the bundle no longer freezes exactly the otel extra"


@needs_checkout
def test_the_build_applies_the_extras_to_what_it_installs():
    """Declaring them is not installing them: what ends up frozen is whatever is in the
    build environment, since that is what the hooks and `collect_submodules` read."""
    assert '"${ROOT}[${BUNDLE_EXTRAS}]"' in BUILDER.read_text(), (
        "BUNDLE_EXTRAS is declared but not applied to the project requirement"
    )


@needs_checkout
def test_the_build_proves_telemetry_actually_exports():
    """The check that cannot be replaced by a smaller one. A bundle that stopped
    collecting OpenTelemetry starts, generates, and passes every other step — the SDK is
    imported lazily and its absence is a `notices[]` entry, not a failure. Only turning
    telemetry on and looking for a span catches it."""
    text = BUILDER.read_text()
    assert "telemetry_test" in text and "MEDIA_AI_TELEMETRY=1" in text
    assert "telemetry_unavailable" in text, "the build does not check for the degraded-to-no-op notice"
    assert '*,otel,*' in text, "the gate must match BUNDLE_EXTRAS' comma separator, not spaces"


# The hint is about the extras a bundle does *not* have — `keychain` today. Its wording
# is the load-bearing part: `pip install '<dist>[keychain]'` is correct for a wheel in a
# virtualenv and misleading for a bundle, where it would install the package into some
# other Python and leave the reference that raised still raising. Hints here are
# documented as usually runnable and an agent runs whatever appears in one.


def test_the_extras_hint_names_this_interpreter_and_a_source_that_resolves():
    """Two things a bare ``pip install '<dist>[keychain]'`` gets wrong.

    It runs whichever pip is first on ``PATH`` — which on a machine with more than one
    interpreter installs the extra somewhere else, *successfully*, leaving the reference
    that raised still raising. ``cli/_install.py`` states that rule for the upgrade
    steps; it is the same rule here. And the distribution is deliberately not on PyPI,
    so the bare form resolves to nothing at all.
    """
    hint = packaging.extra_hint("keychain")
    assert hint.startswith(f"{sys.executable} -m pip install "), hint
    assert not hint.startswith("pip "), "a bare pip installs into whichever env is first on PATH"
    assert f"{brand.dist_name()}[keychain] @ git+" in hint, "the dist is not on an index; name the source"


def test_the_extras_hint_for_a_bundle_is_a_source_install(frozen):
    hint = packaging.extra_hint("keychain")
    assert hint.startswith("bash ") and hint.endswith("--from-source"), hint
    assert "pip install" not in hint


def test_the_extras_hint_never_names_a_missing_installer(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    hint = packaging.extra_hint("keychain")
    assert "None" not in hint and "pip install" not in hint


def test_every_extras_hint_site_goes_through_it(frozen):
    """Three callers, on both sides of the CLI boundary, and each one used to build the
    pip line itself. Checked by running them rather than by grepping: what matters is
    the string that reaches the user, and two of these are reached only on an import
    failure that has to be simulated anyway."""
    from media_ai.core import notices
    from media_ai.core.errors import MediaError
    from media_ai.core.telemetry import runtime

    # telemetry: the notice a command carries when the SDK is absent.
    notices.clear()
    try:
        runtime._unavailable(ImportError("no opentelemetry"))
        action = next(n for n in notices.pending() if n["kind"] == "telemetry_unavailable")["action"]
    finally:
        notices.clear()
    assert "pip install" not in action, action

    # doctor: the same fact, asked for directly and offline. Only reachable when the
    # extra really is absent, which is the matrix job rather than the `--extra otel` one.
    from media_ai.cli import doctor

    line = next(c for c in doctor._check_telemetry() if c["check"] == "telemetry")
    if "not installed" in line["detail"]:
        assert "pip install" not in line["detail"], line

    # credentials: a `keychain://` reference with the extra not installed.
    from media_ai.credentials.reference import resolve_reference

    if _keyring_absent():
        with pytest.raises(MediaError) as excinfo:
            resolve_reference("keychain://svc/acct", provider="mock")
        assert "pip install" not in str(excinfo.value), str(excinfo.value)


def _keyring_absent() -> bool:
    try:
        import keyring  # noqa: F401
    except ModuleNotFoundError:
        return True
    return False


def test_doctor_reports_the_install_method(frozen):
    """`doctor`'s first line is what makes the rest of it legible — a bundle carries its
    own interpreter and its own ffmpeg, so "which python" is a question about a
    different installation than the one being diagnosed."""
    from media_ai.cli import doctor

    version_line = next(c for c in doctor._check_cli() if c["check"] == "version")
    assert "standalone" in version_line["detail"]
    path_line = next(c for c in doctor._check_cli() if c["check"] == "path")
    if path_line["status"] == "warn":
        assert "uv run" not in path_line["detail"], "a bundle has no environment to run inside"


# ------------------------------------------- 5. the version a user actually types

# A release is *tagged* `v0.8.0` and its version *is* `0.8.0`. Somebody upgrading reads
# the tag off the releases page and passes it back, and every install method then added
# its own `v` — producing `git+…@vv0.8.0` and `--version vv0.8.0`, refs no repository
# has. `_target` normalises once, because the value is used three ways: to build that
# ref, to compare against the running version, and as `to` in the result.


def _args(target):
    from types import SimpleNamespace

    return SimpleNamespace(target=target)


@pytest.mark.parametrize("typed", ["0.8.0", "v0.8.0"])
def test_a_tag_and_a_version_name_the_same_release(typed):
    from media_ai.cli.upgrade import _target

    assert _target(_args(typed)) == "0.8.0"


def test_the_normalised_target_builds_a_ref_that_exists():
    from media_ai.cli.upgrade import _target

    for method in ("uv-tool", "pip", "standalone"):
        install = _install.Install(method, "/x", installer="/x/install.sh")
        rendered = " ".join(install.upgrade_steps("owner/repo", _target(_args("v0.8.0")))[0])
        assert "vv" not in rendered, rendered
        assert "v0.8.0" in rendered, rendered


def test_a_v_prefixed_target_no_longer_reads_as_not_newer():
    """The quiet half of the same bug: the version regex does not admit the prefix, so
    `is_newer("v9.9.9", "0.8.0")` was False and a newer release compared as older."""
    from media_ai.cli.upgrade import _target
    from media_ai.core import update

    assert update.is_newer(_target(_args("v9.9.9")), "0.8.0")


def test_something_that_is_not_a_version_is_left_alone():
    """`--version main` is not a release and must not be silently turned into `ain`."""
    from media_ai.cli.upgrade import _target

    assert _target(_args("main")) == "main"
