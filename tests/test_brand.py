"""The CLI's name, and the four ways it could quietly disagree with itself.

A rename is only worth offering if it is *complete*. This project promises that
``error.hint`` is usually runnable and ships Agent Skills whose commands an agent
executes verbatim, so a build whose binary is ``foo`` while a hint or a skill still
says ``media-ai`` hands the caller a ``command not found`` — with the tool's own
output as the thing that lied. That is a worse failure than not supporting the rename,
which is why the name has exactly one declaration (:data:`media_ai.brand.CLI_NAME`)
and why the places that cannot import it are pinned here instead of trusted.

Four invariants, each corresponding to a way the name has leaked in the past:

1. The static files outside the package repeat the name — ``pyproject.toml`` and
   ``install/install.sh``. Pinned, exactly as ``tests/test_version.py`` pins the
   installer's fallback ref to ``__version__``.
2. No packaged skill file contains the name at all: skills carry ``{{cli}}`` and are
   rendered on install, so a new reference file that spells the command out fails here
   rather than shipping a skill that only works for the unrenamed build.
3. No string a user can see is a literal. Enforced over the syntax tree so that
   docstrings — developer prose, which may name the reference build — stay exempt
   while the strings that reach stdout, stderr and ``error.hint`` do not.
4. The whole visible surface actually follows the constant. Checked by renaming the
   build and looking, because the first three are all *absence* checks and absence is
   satisfiable by a string nobody derives from anything.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import media_ai
from media_ai import brand
from media_ai.core import update

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "media_ai"
PYPROJECT = ROOT / "pyproject.toml"
INSTALLER = ROOT / "install" / "install.sh"

DEFAULT = brand.CLI_NAME

needs_checkout = pytest.mark.skipif(
    not PYPROJECT.is_file(), reason="running against an installed package, not a checkout"
)


# ------------------------------------------------------------------ 0. the constant


def test_the_name_is_usable_as_all_three_things_it_becomes():
    """A filesystem name, a shell command, and a PyPI distribution name at once.

    Deliberately strict: a brand with a space in it produces a skill directory nobody
    can quote and a `[project.scripts]` key that fails at build time — much further
    from the edit that caused it.
    """
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", DEFAULT), DEFAULT


def test_the_derived_names_all_hang_off_it():
    assert brand.cli_name() == DEFAULT
    assert brand.dist_name() == DEFAULT
    assert brand.skill_prefix() == f"{DEFAULT}-"
    assert brand.skill_name("image") == f"{DEFAULT}-image"
    assert brand.config_dir().name == DEFAULT
    assert brand.cmd("bindings", "add") == f"{DEFAULT} bindings add"


# ------------------------------------------------- 1. the files that cannot import it


@needs_checkout
def test_pyproject_names_the_same_cli():
    """The executable's name is fixed at build time, which is the whole reason the
    constant is build-time too. If these drift, the binary and every hint disagree."""
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["name"] == DEFAULT
    assert project["scripts"] == {DEFAULT: "media_ai.__main__:main"}, (
        "one console script, named for the brand, pointing at the package's dispatcher"
    )


@needs_checkout
def test_the_installer_names_the_same_cli():
    text = INSTALLER.read_text(encoding="utf-8")
    assert f'CLI_NAME="{DEFAULT}"' in text, "install/install.sh must pin CLI_NAME to the brand"
    assert 'DIST_NAME="$CLI_NAME"' in text


@needs_checkout
def test_the_installer_calls_the_cli_through_the_variable():
    """A bare `media-ai …` line in the installer would work for this build and fail
    silently for a renamed one — the installer is the one place with no test coverage
    of its happy path, so the check is textual."""
    text = INSTALLER.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.splitlines()
        # The banner comment, the source repo and the CLI_NAME line itself may say it.
        if not line.lstrip().startswith("#") and "CLI_NAME=" not in line and "REPO=" not in line
    )
    assert DEFAULT not in body, "installer invokes the CLI by literal name; use \"$CLI_NAME\"/\"$DIST_NAME\""


# ---------------------------------------------------------- 2. the packaged skills


def _packaged_skill_files() -> list[Path]:
    return sorted(p for p in (SRC / "skills").rglob("*.md") if p.parent != SRC / "skills")


@needs_checkout
def test_no_packaged_skill_names_the_cli():
    """Skills are templates. A literal here reaches an agent as an instruction to run
    a command a renamed build does not answer to."""
    offenders = [str(p.relative_to(SRC)) for p in _packaged_skill_files() if DEFAULT in p.read_text(encoding="utf-8")]
    assert not offenders, f"use {{{{cli}}}} / {{{{skill}}}} instead of the literal name: {offenders}"


@needs_checkout
def test_no_packaged_skill_has_a_token_typo():
    """`render` leaves an unknown token verbatim rather than raising mid-install, so
    this is where a `{{clii}}` is caught — before it ships."""
    from media_ai.cli._render import unknown_tokens

    bad = {
        str(p.relative_to(SRC)): sorted(unknown_tokens(p.read_text(encoding="utf-8")))
        for p in _packaged_skill_files()
        if unknown_tokens(p.read_text(encoding="utf-8"))
    }
    assert not bad, f"unknown template tokens: {bad}"


@needs_checkout
def test_the_packaged_directories_are_bare_group_names():
    """`skills/image/`, not `skills/media-ai-image/` — the brand enters the tree only
    through `brand.skill_name`, so a rename reaches the directory names too."""
    from media_ai.cli._discovery import packaged_groups

    assert packaged_groups(), "no packaged skills found"
    assert all("-" not in g and g.islower() for g in packaged_groups()), packaged_groups()


# ------------------------------------------------------- 3. no user-visible literal


def _string_constants(tree: ast.AST) -> list[ast.Constant]:
    """Every string literal in the module except the docstrings.

    Docstrings are developer prose and may name the reference build; the point of the
    exemption is that everything *else* is a string somebody could read at a terminal
    or parse out of `error.hint`. Walking the tree is what makes that distinction —
    a grep cannot, and would either fail on the comments or miss the f-strings.
    """
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n not in docstrings
    ]


@needs_checkout
def test_no_user_visible_string_hardcodes_the_cli():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "brand.py":  # the one declaration
            continue
        for node in _string_constants(ast.parse(path.read_text(encoding="utf-8"))):
            # `core.update.SOURCE_REPO` names where the code comes from, which
            # `brand.py` says outright is not what the tool is called: a white-label
            # build renames the executable and still fetches from the same repository.
            # Exempted by value, not by file, so the rest of that module stays covered.
            if DEFAULT in node.value and node.value != update.SOURCE_REPO:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}: {node.value[:60]!r}")
    assert not offenders, "build these from media_ai.brand instead:\n" + "\n".join(offenders)


@needs_checkout
def test_the_installer_fetches_from_the_repository_the_package_names():
    """`install.sh` repeats the repository, the way it repeats the fallback version.

    It cannot import Python, so the copy is unavoidable — but a drift means the
    installer pulls from one repository while the feed is read from another, and the
    two would disagree about what the latest release is.
    """
    match = re.search(r'^REPO="\$\{MEDIA_AI_REPO:-([^}]+)\}"', INSTALLER.read_text(encoding="utf-8"), re.M)
    assert match, "install.sh no longer declares REPO the way this test reads it"
    assert match.group(1) == update.SOURCE_REPO


# --------------------------------------------------------- 4. the rename works


@pytest.fixture()
def renamed(monkeypatch):
    """Rebrand this process. Everything user-visible must follow within the process,
    which is the property the three absence checks above cannot establish."""
    monkeypatch.setattr(brand, "CLI_NAME", "zzbrand")
    from media_ai.cli import _discovery, _skillstore

    _discovery.skill_info.cache_clear()
    _skillstore._packaged_source.cache_clear()
    yield "zzbrand"
    _discovery.skill_info.cache_clear()
    _skillstore._packaged_source.cache_clear()


def test_the_usage_text_follows(renamed):
    import io

    from media_ai.__main__ import _usage

    buf = io.StringIO()
    _usage(buf)
    text = buf.getvalue()
    assert DEFAULT not in text
    assert f"usage: {renamed} <group> <op>" in text
    assert f"  {renamed} image generate" in text


def test_every_group_parser_follows(renamed):
    from media_ai.__main__ import _GROUPS, group_main

    for group in _GROUPS:
        module = sys.modules[group_main(group).__module__]
        prog = module._build_parser().prog
        assert prog == f"{renamed} {group}", prog


def test_error_hints_follow(renamed, monkeypatch, tmp_path):
    """The contractual one: a hint is documented as runnable, so it is the string a
    rename most has to reach."""
    from media_ai.core.errors import MediaError
    from media_ai.core.resolve import resolve
    from media_ai.core.result import error_payload
    from media_ai.core.scene import Scene

    monkeypatch.setenv("MEDIA_AI_CONFIG_FILE", str(tmp_path / "config.toml"))
    # Three shapes of failure, because the hint is built differently in each: an
    # unknown binding, a scene with nothing configured to serve it, and a request that
    # the manifest rejects. Asserting on `error_payload` rather than on `details` is
    # deliberate — that is the JSON a caller actually parses.
    cases = [
        dict(scene=Scene.IMAGE_TEXT_TO_IMAGE, binding="nope/nope"),
        dict(scene=Scene.IMAGE_TEXT_TO_IMAGE),
        dict(scene=Scene.VIDEO_TEXT_TO_VIDEO),
    ]
    seen = 0
    for kwargs in cases:
        with pytest.raises(MediaError) as excinfo:
            resolve(**kwargs)
        payload = json.dumps(error_payload(excinfo.value))
        assert DEFAULT not in payload, payload
        if renamed in payload:
            seen += 1
    assert seen, "no hint named the CLI at all; the assertion above proves nothing"


def test_the_job_poll_string_follows(renamed):
    """`meta.poll` is machine contract: an agent runs it verbatim to finish an async job."""
    from media_ai.core.result import JobHandle

    handle = JobHandle(provider="mock", model="mock", id="abc", output="/tmp/x.mp4", binding="mock/mock")
    poll = handle.to_dict()["poll"]
    assert poll.startswith(f"{renamed} job query"), poll


def test_the_config_namespace_follows(renamed, monkeypatch):
    """Two brands must not share a config: it names bindings the other may not ship."""
    monkeypatch.delenv("MEDIA_AI_CONFIG_FILE", raising=False)
    monkeypatch.delenv("MEDIA_AI_CREDENTIALS_FILE", raising=False)
    from media_ai.core.config import config_path
    from media_ai.credentials.stores import credentials_path

    assert config_path().parent.name == renamed
    assert credentials_path().parent.name == renamed
    assert config_path().parent == credentials_path().parent


def test_the_env_var_names_do_not_follow(renamed, monkeypatch, tmp_path):
    """Deliberately unbranded: `MEDIA_*` names a modality, and each is a per-invocation
    override rather than a namespace — what has to differ between two installs is the
    default path above. Renaming them would break every caller's CI for no isolation."""
    from media_ai.core.config import config_path

    monkeypatch.setenv("MEDIA_AI_CONFIG_FILE", str(tmp_path / "elsewhere.toml"))
    assert config_path() == tmp_path / "elsewhere.toml"


def test_installed_skill_names_and_text_follow(renamed, tmp_path):
    from media_ai.cli._discovery import available_skills
    from media_ai.cli._skillstore import copy_skill, skill_is_current

    assert available_skills()[0].startswith(f"{renamed}-")
    for skill in available_skills():
        copy_skill(skill, tmp_path)
        assert skill_is_current(tmp_path, skill), skill
    for path in tmp_path.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert DEFAULT not in text, f"{path} still names the reference build"
        assert "{{" not in text, f"{path} shipped an unrendered token"

    # The cross-references between skills have to land on the renamed directories, or
    # every skill's opening "read the shared contract first" is a broken path.
    shared = tmp_path / f"{renamed}-shared" / "SKILL.md"
    assert shared.is_file()
    assert f"../{renamed}-shared/SKILL.md" in (tmp_path / f"{renamed}-image" / "SKILL.md").read_text(encoding="utf-8")


def test_the_needs_graph_survives_the_rename(renamed):
    """`needs:` edges are written as `{{skill}}job` and matched against branded names —
    parsing them unrendered would silently drop every dependency."""
    from media_ai.cli._discovery import resolve_selection

    chosen, reasons = resolve_selection([f"{renamed}-video"])
    assert f"{renamed}-job" in chosen
    assert reasons[f"{renamed}-job"] == f"needed by {renamed}-video"


@needs_checkout
def test_a_renamed_build_reports_its_own_name_end_to_end(tmp_path):
    """The in-process checks all patch a constant. This one edits the source, runs the
    CLI in a fresh interpreter, and reads stdout — so it also covers anything that
    reads the brand at import time, which is the failure mode a monkeypatch hides."""
    pkg = tmp_path / "media_ai"
    subprocess.run(
        [sys.executable, "-c",
         "import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2])", str(SRC), str(pkg)],
        check=True,
    )
    text = (pkg / "brand.py").read_text(encoding="utf-8")
    (pkg / "brand.py").write_text(text.replace(f'CLI_NAME = "{DEFAULT}"', 'CLI_NAME = "zzbrand"'), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "media_ai", "--help"],
        cwd=tmp_path, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(tmp_path), "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stderr
    assert "zzbrand" in proc.stdout and DEFAULT not in proc.stdout, proc.stdout


def test_the_version_line_is_the_only_place_the_version_lives():
    """Guards the analogy this module is built on: if `__version__` ever grew a second
    home, copying its arrangement for the brand would be copying a broken one."""
    assert media_ai.__version__
