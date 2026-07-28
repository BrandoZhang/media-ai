"""``media-ai uninstall`` — the other half of the install lifecycle.

The invariants worth protecting: uninstalling leaves nothing behind unless a
``--keep-*`` flag says otherwise, only recognisable skill directories are ever
deleted, and a cancelled run changes nothing. Answers come from a ScriptedPrompter,
so the flow is exercised without a terminal.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import pytest

from media_ai.cli import uninstall as uninstall_mod
from media_ai.cli._prompt import Cancelled, GoBack, ScriptedPrompter
from media_ai.cli._skillstore import copy_skill, load_receipt, receipt_path, record_install, remove_skill
from media_ai.core.errors import ErrorCategory, MediaError


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated config paths and a cwd inside the sandbox.

    Both matter here: uninstall scans ``~`` *and* ``./`` for the conventional agent
    directories, so a leaky fixture would have it deleting the checkout's own skills.
    """
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(tmp_path / "cfg" / "credentials.toml"))
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "cfg" / "config.toml"))
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    (tmp_path / "fakehome").mkdir()
    (tmp_path / "cfg").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_args(**over):
    base = dict(skills_dest=None, keep_skills=False, keep_config=False, keep_credentials=False,
                yes=True, dry_run=False, pretty=False, log_level=None, metadata_out=None)
    base.update(over)
    return argparse.Namespace(**base)


def run(args, answers=()):
    prompter = ScriptedPrompter(list(answers))
    return uninstall_mod._uninstall(args, prompter), prompter


def install(dest, *skills, record=True):
    """Put skills on disk the way `init` would, receipt included."""
    for skill in skills or ("media-ai-shared", "media-ai-image"):
        copy_skill(skill, dest)
    if record:
        record_install([dest])
    return dest


def write_config(home, *, credentials=True, config=True, backup=False):
    cfg = home / "cfg"
    if config:
        (cfg / "config.toml").write_text('[providers.volc]\nimage_model = "x"\n', encoding="utf-8")
    if credentials:
        path = cfg / "credentials.toml"
        path.write_text('[openai]\napi_key = "not-a-real-key"\n', encoding="utf-8")
        path.chmod(0o600)
        if backup:
            (cfg / "credentials.toml.bak").write_text(path.read_text(), encoding="utf-8")
    return cfg


# ---------------------------------------------------------------------- skills


def test_removes_the_recorded_skills(home):
    dest = install(home / "sk")
    summary, _ = run(make_args())
    assert summary["skills"][0]["dest"] == str(dest)
    assert not (dest / "media-ai-image").exists()


def test_finds_conventional_locations_without_a_receipt(home):
    """A hand-copied install, or one predating the receipt, is still removable."""
    dest = install(home / "fakehome" / ".claude" / "skills", record=False)
    run(make_args())
    assert not (dest / "media-ai-shared").exists()


def test_project_level_directories_are_found_too(home):
    dest = install(home / ".codex" / "skills", record=False)
    run(make_args())
    assert not (dest / "media-ai-shared").exists()


def test_an_emptied_skills_directory_is_cleaned_up(home):
    dest = install(home / "fakehome" / ".claude" / "skills")
    run(make_args())
    assert not dest.exists()


def test_a_shared_skills_directory_is_left_in_place(home):
    """Somebody else's skill in the same folder means the folder is not ours."""
    dest = install(home / "sk")
    (dest / "other-skill").mkdir()
    run(make_args())
    assert dest.is_dir() and (dest / "other-skill").is_dir()


def test_unrelated_directories_are_never_touched(home):
    dest = install(home / "sk")
    (dest / "media-ai-lookalike").mkdir()  # no SKILL.md: not ours
    run(make_args())
    assert (dest / "media-ai-lookalike").is_dir()


def test_removing_a_directory_that_is_not_a_skill_is_refused(home):
    (home / "sk" / "media-ai-bogus").mkdir(parents=True)
    with pytest.raises(MediaError, match="no SKILL.md"):
        remove_skill(home / "sk", "media-ai-bogus")


def test_a_path_traversing_name_is_refused(home):
    with pytest.raises(MediaError, match="not a media-ai skill"):
        remove_skill(home / "sk", "../../etc")


def test_a_symlinked_skill_is_unlinked_not_followed(home):
    """`skills/README.md` documents installing by symlink; deleting through one would
    take the user's checkout with it."""
    source = install(home / "checkout", "media-ai-image", record=False)
    dest = home / "sk"
    dest.mkdir()
    (dest / "media-ai-image").symlink_to(source / "media-ai-image")
    run(make_args(skills_dest=[str(dest)]))
    assert not (dest / "media-ai-image").exists()
    assert (source / "media-ai-image" / "SKILL.md").is_file()


def test_keep_skills_leaves_them_alone(home):
    dest = install(home / "sk")
    summary, _ = run(make_args(keep_skills=True))
    assert (dest / "media-ai-image").is_dir()
    assert str(dest) in summary["kept"]


def test_explicit_destination_limits_the_search(home):
    kept = install(home / "other")
    gone = install(home / "sk")
    run(make_args(skills_dest=[str(gone)]))
    assert not (gone / "media-ai-image").exists()
    assert (kept / "media-ai-image").is_dir()


def test_nothing_installed_is_not_an_error(home):
    summary, prompter = run(make_args())
    assert summary["ok"] is True and summary["skills"] == []
    assert any("No installed Agent Skills" in note for note in prompter.notes)


# --------------------------------------------------------------- configuration


def test_configuration_goes_by_default(home):
    """Keeping it would commit the project to migrating a file written by any past
    version, from the first release onward — a reinstall would always find one."""
    install(home / "sk")
    cfg = write_config(home)
    summary, _ = run(make_args())
    assert not (cfg / "credentials.toml").exists() and not (cfg / "config.toml").exists()
    assert str(cfg / "credentials.toml") in summary["removed"]


def test_credential_backups_go_with_the_credentials(home):
    """`init` copies the file aside before rewriting it; the copy holds the same key."""
    cfg = write_config(home, backup=True)
    run(make_args())
    assert not (cfg / "credentials.toml.bak").exists()


def test_keep_config_holds_back_only_the_config(home):
    cfg = write_config(home)
    run(make_args(keep_config=True))
    assert (cfg / "config.toml").is_file()
    assert not (cfg / "credentials.toml").exists()


def test_keep_credentials_holds_back_only_the_keys(home):
    cfg = write_config(home)
    run(make_args(keep_credentials=True))
    assert (cfg / "credentials.toml").is_file()
    assert not (cfg / "config.toml").exists()


def test_keeping_a_file_reports_it_as_kept(home):
    cfg = write_config(home)
    summary, _ = run(make_args(keep_credentials=True))
    assert str(cfg / "credentials.toml") in summary["kept"]
    assert str(cfg / "credentials.toml") not in summary["removed"]


def test_an_emptied_config_directory_is_cleaned_up(home):
    cfg = write_config(home)
    run(make_args())
    assert not cfg.exists()


def test_a_kept_file_keeps_its_directory(home):
    cfg = write_config(home)
    run(make_args(keep_credentials=True))
    assert cfg.is_dir()


# ---------------------------------------------------------------- interactive


def test_interactive_still_asks_before_removing_configuration(home):
    """The default is yes, but a credentials file is often the only copy of a key —
    so the path and what is in it go in front of the user before it does."""
    install(home / "sk")
    cfg = write_config(home)
    # skills: keep the one destination selected; then decline both files.
    summary, prompter = run(make_args(yes=False), [[0], False, False])
    assert (cfg / "credentials.toml").is_file() and (cfg / "config.toml").is_file()
    assert len(summary["kept"]) == 2
    assert any("credentials.toml" in q for q in prompter.asked)


def test_interactive_can_hold_back_one_file(home):
    cfg = write_config(home)
    run(make_args(yes=False), [True, False])  # no skills found, so the menu is skipped
    assert not (cfg / "config.toml").exists()
    assert (cfg / "credentials.toml").is_file()


def test_a_deselected_destination_survives(home):
    keep = install(home / "a")
    drop = install(home / "b")
    order = [entry["dest"] for entry in run(make_args(dry_run=True))[0]["skills"]]
    summary, _ = run(make_args(yes=False), [[order.index(str(drop))], False, False])
    assert (keep / "media-ai-image").is_dir()
    assert not (drop / "media-ai-image").exists()
    assert str(keep) in summary["kept"]


def test_going_back_re_asks_the_previous_question(home):
    """Same driver as `init`: the questions come first, so any of them can be re-run."""
    write_config(home)
    # config? -> credentials? (go back) -> config? -> credentials?
    _summary, prompter = run(make_args(yes=False), [False, GoBack, True, False])
    assert sum("config.toml" in q for q in prompter.asked) == 2
    assert not (home / "cfg" / "config.toml").exists(), "the second answer is the one that counts"
    assert (home / "cfg" / "credentials.toml").is_file()


def test_cancelling_removes_nothing(home, monkeypatch):
    """Every question is answered before the first deletion, so this is provable."""
    dest = install(home / "sk")
    write_config(home)
    monkeypatch.setattr(uninstall_mod, "get_prompter", lambda **kw: ScriptedPrompter([[0], Cancelled]))
    with pytest.raises(MediaError) as ei:
        uninstall_mod._do(make_args(yes=False))
    assert ei.value.category == ErrorCategory.CLI
    assert (dest / "media-ai-image").is_dir()
    assert (home / "cfg" / "credentials.toml").is_file()


# ------------------------------------------------------------------- dry run


def test_dry_run_changes_nothing(home):
    dest = install(home / "sk")
    cfg = write_config(home)
    summary, _ = run(make_args(dry_run=True))
    assert (dest / "media-ai-image").is_dir()
    assert (cfg / "credentials.toml").is_file()
    assert summary["dry_run"] is True


def test_dry_run_still_reports_what_would_go(home):
    install(home / "sk")
    write_config(home)
    summary, _ = run(make_args(dry_run=True))
    assert summary["skills"][0]["removed"]
    assert any("credentials.toml" in path for path in summary["removed"])


# ------------------------------------------------------------------- receipt


def test_the_receipt_goes_when_the_last_skill_does(home):
    install(home / "sk")
    run(make_args())
    assert not receipt_path().exists()


def test_a_partial_removal_leaves_an_accurate_receipt(home):
    kept = install(home / "a")
    install(home / "b")
    run(make_args(skills_dest=[str(home / "b")]))
    assert set(load_receipt()) == {str(kept)}


# ------------------------------------------------------- machine contract


def test_stdout_is_exactly_one_json_object(home):
    install(home / "sk")
    res = subprocess.run(
        [sys.executable, "-m", "media_ai", "uninstall", "--yes"],
        capture_output=True, text=True, timeout=60, cwd=str(home),
        env={**dict(__import__("os").environ), "HOME": str(home / "fakehome"),
             "MEDIA_CREDENTIALS_FILE": str(home / "cfg" / "credentials.toml"),
             "MEDIA_CONFIG_FILE": str(home / "cfg" / "config.toml")},
    )
    assert res.returncode == 0, res.stderr
    parsed = json.loads(res.stdout)
    assert parsed["ok"] is True and parsed["command"] == "uninstall"
    assert len(res.stdout.strip().splitlines()) == 1


def test_it_says_how_to_remove_the_cli_itself(home):
    """The command cannot delete the interpreter running it, so it has to say so."""
    summary, prompter = run(make_args())
    assert "uninstall media-ai" in summary["remove_cli"]
    assert any("still installed" in note for note in prompter.notes)


def test_uninstall_is_a_registered_group():
    from media_ai.__main__ import _GROUPS

    assert "uninstall" in _GROUPS


def test_the_same_discovery_as_doctor(home):
    """`doctor` blessing an install `uninstall` cannot find is what a second copy of
    this scan drifts into, so both go through one helper."""
    import inspect

    from media_ai.cli import doctor as doctor_mod

    assert "install_roots" in inspect.getsource(doctor_mod._check_skills)
    assert "install_roots" in inspect.getsource(uninstall_mod._candidates)


def test_a_step_clears_its_answer_before_it_can_fail(home, monkeypatch):
    """The step that decides what gets deleted must not keep a previous run's answer
    when the discovery under it raises."""
    dest = install(home / "sk")
    choices = uninstall_mod._Choices()
    choices.skills = [(dest, ["media-ai-image"])]
    monkeypatch.setattr(uninstall_mod, "_candidates", lambda _e: (_ for _ in ()).throw(OSError("unreadable")))
    with pytest.raises(OSError):
        uninstall_mod._ask_skills(make_args(), ScriptedPrompter([]), choices)
    assert choices.skills == []
