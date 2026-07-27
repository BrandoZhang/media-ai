"""``media-ai uninstall`` — the other half of the install lifecycle.

The invariants worth protecting are about *restraint*: configuration survives unless
it was asked for by name, only recognisable skill directories are deleted, and a
cancelled run changes nothing. Answers come from a ScriptedPrompter, so the flow is
exercised without a terminal.
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
    base = dict(skills_dest=None, keep_skills=False, config=False, credentials=False,
                purge=False, yes=True, dry_run=False, pretty=False, log_level=None, metadata_out=None)
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


def test_configuration_is_kept_by_default(home):
    install(home / "sk")
    cfg = write_config(home)
    summary, _ = run(make_args())
    assert (cfg / "credentials.toml").is_file() and (cfg / "config.toml").is_file()
    assert str(cfg / "credentials.toml") in summary["kept"]


def test_purge_removes_both_files(home):
    install(home / "sk")
    cfg = write_config(home)
    run(make_args(purge=True))
    assert not (cfg / "credentials.toml").exists() and not (cfg / "config.toml").exists()


def test_purge_also_removes_credential_backups(home):
    """`init` copies the file aside before rewriting it; the copy holds the same key."""
    cfg = write_config(home, backup=True)
    run(make_args(purge=True))
    assert not (cfg / "credentials.toml.bak").exists()


def test_config_flag_does_not_take_the_keys_with_it(home):
    cfg = write_config(home)
    run(make_args(config=True))
    assert not (cfg / "config.toml").exists()
    assert (cfg / "credentials.toml").is_file()


def test_credentials_flag_does_not_take_the_config_with_it(home):
    cfg = write_config(home)
    run(make_args(credentials=True))
    assert not (cfg / "credentials.toml").exists()
    assert (cfg / "config.toml").is_file()


def test_an_emptied_config_directory_is_cleaned_up(home):
    cfg = write_config(home)
    run(make_args(purge=True))
    assert not cfg.exists()


# ---------------------------------------------------------------- interactive


def test_interactive_asks_before_removing_configuration(home):
    install(home / "sk")
    cfg = write_config(home)
    # skills: keep the one destination selected; then decline both files.
    summary, prompter = run(make_args(yes=False), [[0], False, False])
    assert (cfg / "credentials.toml").is_file()
    assert len(summary["kept"]) == 2
    assert any("credentials.toml" in q for q in prompter.asked)


def test_interactive_can_say_yes_to_the_config_file(home):
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
    summary, _ = run(make_args(purge=True, dry_run=True))
    assert (dest / "media-ai-image").is_dir()
    assert (cfg / "credentials.toml").is_file()
    assert summary["dry_run"] is True


def test_dry_run_still_reports_what_would_go(home):
    install(home / "sk")
    write_config(home)
    summary, _ = run(make_args(purge=True, dry_run=True))
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
    assert parsed["ok"] is True and parsed["operation"] == "uninstall"
    assert len(res.stdout.strip().splitlines()) == 1


def test_it_says_how_to_remove_the_cli_itself(home):
    """The command cannot delete the interpreter running it, so it has to say so."""
    summary, prompter = run(make_args())
    assert "uninstall media-ai" in summary["remove_cli"]
    assert any("still installed" in note for note in prompter.notes)


def test_uninstall_is_a_registered_group():
    from media_ai.__main__ import _GROUPS

    assert "uninstall" in _GROUPS
