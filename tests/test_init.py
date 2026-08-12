"""``media-ai init`` — the wizard flow.

Questions are answered by a ScriptedPrompter, so these exercise the flow and what it
writes without touching a terminal. The terminal layer itself is covered by
tests/test_prompt.py, which drives a real pty.
"""

from __future__ import annotations

import argparse
import json
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from conftest import CATALOG

from media_ai.cli import init as init_mod
from media_ai.cli._discovery import core_skills, selectable_skills
from media_ai.cli._prompt import Cancelled, GoBack, ScriptedPrompter
from media_ai.core.errors import ErrorCategory, MediaError

SECRET = "sk-sentinel-do-not-leak-9f3a"


def pick(*names: str) -> list[int]:
    """Answer the skill menu by name.

    The menu offers only the *optional* tier, so positions shift whenever a skill is
    added or promoted; naming what is picked keeps these tests about the flow rather
    than about the ordering of a list they do not own.
    """
    offered = selectable_skills()
    return [offered.index(name) for name in names]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated config paths, a clean environment, and cwd inside the sandbox."""
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "config.toml"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ARK_API_KEY",
                "VOLC_API_KEY", "ELEVENLABS_API_KEY", "ELEVEN_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_args(**over):
    base = dict(verify=False, advanced=False, skills_only=False, skills_dest=None,
                dry_run=False, non_interactive=False, pretty=False, log_level=None,
                metadata_out=None)
    base.update(over)
    return argparse.Namespace(**base)


def run(args, answers):
    """Run the wizard with a scripted prompter; returns (summary, prompter)."""
    prompter = ScriptedPrompter(answers)
    return init_mod._wizard(args, prompter), prompter


def creds(home) -> dict:
    return tomllib.loads((home / "credentials.toml").read_text())


def config(home) -> dict:
    return tomllib.loads((home / "config.toml").read_text())


# ------------------------------------------------------------- non-interactive


def test_skills_only_non_interactive_installs_everything(home):
    args = make_args(skills_only=True, non_interactive=True, skills_dest=str(home / "sk"))
    summary, _ = run(args, [])
    installed = summary["skills"][0]["installed"]
    assert "media-ai-image" in installed and "media-ai-shared" in installed
    assert (home / "sk" / "media-ai-image" / "SKILL.md").is_file()
    assert (home / "sk" / "media-ai-image" / "references" / "generate.md").is_file()


def test_non_interactive_without_dest_is_a_clear_error(home):
    with pytest.raises(MediaError) as ei:
        run(make_args(non_interactive=True), [])
    assert ei.value.category == ErrorCategory.CLI
    assert "--skills-dest" in str(ei.value)


def test_non_interactive_never_collects_credentials(home):
    """Nothing sensible to default a key to, so an unattended run must not try."""
    args = make_args(non_interactive=True, skills_dest=str(home / "sk"))
    summary, prompter = run(args, [])
    assert prompter.asked == []
    assert summary["bindings"] == []
    assert not (home / "credentials.toml").exists()


# -------------------------------------------------------------------- skills


def test_shared_skill_is_always_included(home):
    """media-ai-shared is the contract the others build on."""
    args = make_args(skills_dest=str(home / "sk"), skills_only=True)
    summary, _ = run(args, [pick("media-ai-image")])
    assert "media-ai-shared" in summary["skills"][0]["installed"]


def dests(*labels: str) -> list[int]:
    """Answer the destination menu by label, for the same reason as :func:`pick`."""
    choices = init_mod._dest_choices()
    return [next(i for i, o in enumerate(choices) if o.label == label) for label in labels]


def test_installs_to_several_destinations(home):
    args = make_args(skills_only=True)
    # skills=[image]; two distinct conventions; no custom path
    summary, _ = run(args, [pick("media-ai-image"), dests("~/.claude/skills", "~/.codex/skills")])
    assert len(summary["skills"]) == 2
    assert all(entry["installed"] for entry in summary["skills"])


class TestACustomPath:
    """A custom path is one more place to install to, so it is a row in the same list
    as the rest rather than a follow-up question — and unticked, it costs no question."""

    @staticmethod
    def custom_row() -> list[int]:
        choices = init_mod._dest_choices()
        return [next(i for i, o in enumerate(choices) if o.value == init_mod.CUSTOM_DEST)]

    def test_it_is_the_last_row_and_never_pre_ticked(self, home):
        choices = init_mod._dest_choices()
        assert choices[-1].value == init_mod.CUSTOM_DEST
        assert self.custom_row()[0] not in init_mod._preselected_dests(choices)

    def test_ticking_it_asks_for_the_path(self, home):
        args = make_args(skills_only=True)
        summary, _ = run(args, [pick("media-ai-image"), self.custom_row(), str(home / "custom")])
        assert summary["skills"][0]["dest"] == str(home / "custom")
        assert (home / "custom" / "media-ai-shared" / "SKILL.md").is_file()

    def test_it_installs_alongside_the_ticked_conventions(self, home):
        args = make_args(skills_only=True)
        picked = dests("~/.claude/skills") + self.custom_row()
        summary, _ = run(args, [pick("media-ai-image"), picked, str(home / "custom")])
        assert {e["dest"] for e in summary["skills"]} == {str(home / ".claude/skills"), str(home / "custom")}

    def test_leaving_it_unticked_asks_nothing(self, home):
        args = make_args(skills_only=True)
        _summary, prompter = run(args, [pick("media-ai-image"), dests("~/.claude/skills")])
        assert not any("path" in q.lower() for q in prompter.asked)

    def test_an_empty_path_adds_no_destination(self, home):
        args = make_args(skills_only=True)
        picked = dests("~/.claude/skills") + self.custom_row()
        summary, _ = run(args, [pick("media-ai-image"), picked, "   "])
        assert [e["dest"] for e in summary["skills"]] == [str(home / ".claude/skills")]

    def test_going_back_from_the_path_returns_to_the_list(self, home):
        """Its own step, so back lands on the destinations rather than past them."""
        args = make_args(skills_only=True)
        _summary, prompter = run(
            args,
            [pick("media-ai-image"), self.custom_row(), GoBack, dests("~/.claude/skills")],
        )
        assert prompter.asked.count("Where should they be installed?") == 2
        assert prompter.asked.count("Which skills should be installed?") == 1


class TestDestinationDefaults:
    """Pressing enter through the wizard has to install something.

    Pre-ticking nothing would end a first run with a success message and an empty
    disk — the one outcome the wizard must not produce.
    """

    def test_existing_agent_directories_are_preselected(self, home):
        (home / ".codex" / "skills").mkdir(parents=True)
        choices = init_mod._dest_choices()
        picked = init_mod._preselected_dests(choices)
        assert [choices[i].label for i in picked] == ["~/.codex/skills"]

    def test_the_same_directory_is_never_offered_twice(self, home):
        """Running from your home directory makes `~/…` and `./…` the same path."""
        labels = [o.label for o in init_mod._dest_choices()]
        paths = [str(o.value) for o in init_mod._dest_choices()]
        assert len(paths) == len(set(paths)) == len(labels)

    def test_with_nothing_installed_the_leading_guess_is_ticked(self, home):
        choices = init_mod._dest_choices()
        assert init_mod._preselected_dests(choices) == [0]
        assert choices[0].label == "~/.claude/skills"

    def test_labels_are_real_paths(self, home):
        for option in init_mod._dest_choices():
            if option.value == init_mod.CUSTOM_DEST:
                continue
            assert option.label.startswith(("~/", "./"))
            assert str(option.value).endswith(option.label.lstrip("~.").lstrip("/"))

    def test_pressing_enter_installs_something(self, home):
        args = make_args(skills_only=True)
        summary, _ = run(args, [pick("media-ai-image"), init_mod._preselected_dests(init_mod._dest_choices())])
        assert summary["skills"] and summary["skills"][0]["installed"]


def test_existing_skill_can_be_skipped(home):
    dest = home / "sk"
    (dest / "media-ai-shared").mkdir(parents=True)
    (dest / "media-ai-shared" / "SKILL.md").write_text("hand written", encoding="utf-8")
    args = make_args(skills_only=True, skills_dest=str(dest))
    summary, _ = run(args, [pick("media-ai-image"), 1])  # 1 = "skip"
    assert "media-ai-shared" in summary["skills"][0]["skipped"]
    assert (dest / "media-ai-shared" / "SKILL.md").read_text() == "hand written"


def test_existing_skill_can_be_overwritten(home):
    dest = home / "sk"
    (dest / "media-ai-shared").mkdir(parents=True)
    (dest / "media-ai-shared" / "SKILL.md").write_text("stale", encoding="utf-8")
    args = make_args(skills_only=True, skills_dest=str(dest))
    run(args, [pick("media-ai-image"), 0])  # 0 = "overwrite"
    assert "stale" not in (dest / "media-ai-shared" / "SKILL.md").read_text()


# ------------------------------------------------------- what the menu asks about


class TestTheSkillMenu:
    """The menu should contain choices, and only choices.

    Offering the shared contract, capability discovery, cost accounting, or the async
    job skill invites a selection that half-works: deselect ``media-ai-shared`` and
    every other skill loses its contract; deselect ``media-ai-job`` and an async video
    generation has no way to be collected.
    """

    @staticmethod
    def menu(home, answers):
        args = make_args(skills_only=True, skills_dest=str(home / "sk"))
        summary, prompter = run(args, answers)
        return summary, prompter

    def test_only_optional_skills_are_offered(self, home):
        _summary, prompter = self.menu(home, [pick("media-ai-image")])
        assert [o.value for o in prompter.offered[0]] == selectable_skills()

    def test_every_row_carries_a_description(self, home):
        _summary, prompter = self.menu(home, [pick("media-ai-image")])
        for option in prompter.offered[0]:
            assert option.detail, f"{option.label} offered with nothing describing it"
            assert option.hint, f"{option.label} offered without saying what it costs"

    def test_core_skills_install_without_being_picked(self, home):
        summary, _ = self.menu(home, [[]])
        assert set(summary["skills"][0]["installed"]) == set(core_skills())

    def test_video_brings_the_job_skill_with_it(self, home):
        summary, _ = self.menu(home, [pick("media-ai-video")])
        installed = summary["skills"][0]["installed"]
        assert "media-ai-job" in installed
        assert (home / "sk" / "media-ai-job" / "SKILL.md").is_file()

    def test_the_automatic_additions_are_reported(self, home):
        """Writing directories nobody asked for is fine; doing it quietly is not."""
        _summary, prompter = self.menu(home, [pick("media-ai-video")])
        notes = "\n".join(prompter.notes)
        assert "media-ai-job" in notes and "needed by media-ai-video" in notes

    def test_a_selection_that_needs_no_key_asks_for_none(self, home):
        """Deselecting everything still installs the core skills, which are offline.

        Folding concat into video removed the last *optional* skill that needed no
        credential, so this is now the only way to reach that state — worth keeping
        reachable, since it is what a first-time user does while looking around.
        """
        args = make_args(skills_dest=str(home / "sk"))
        summary, prompter = run(args, [[]])
        assert summary["bindings"] == []
        assert "no credentials needed" in "\n".join(prompter.notes)


class TestDestinationsExplainThemselves:
    """`(exists)` answered the wrong question: it left the user unable to tell whether
    the *directory* was there or whether media-ai's skills already were — and said
    nothing at all about why there are two `.claude/skills` to choose between."""

    @staticmethod
    def by_label(home):
        return {o.label: o for o in init_mod._dest_choices()}

    def test_each_row_names_the_agent_that_reads_it(self, home):
        for label, option in self.by_label(home).items():
            assert option.hint, f"{label} offered with nothing identifying it"
        assert "Claude Code" in self.by_label(home)["~/.claude/skills"].hint

    def test_user_and_project_scope_are_distinguished(self, home, monkeypatch):
        """The reason there are two `.claude/skills` rows, which the paths alone do
        not explain."""
        (home / "proj").mkdir()
        monkeypatch.chdir(home / "proj")  # otherwise ~ and . are the same directory
        rows = self.by_label(home)
        assert "all projects" in rows["~/.claude/skills"].hint
        # "current folder", not "this project": `./` is wherever the shell happens to be.
        assert "current folder" in rows["./.claude/skills"].hint
        assert str(home / "proj") in rows["./.claude/skills"].detail

    def test_a_directory_that_does_not_exist_says_so(self, home):
        detail = self.by_label(home)["~/.trae/skills"].detail
        assert "Does not exist yet" in detail

    def test_an_empty_directory_is_not_confused_with_an_installed_one(self, home):
        (home / ".codex" / "skills").mkdir(parents=True)
        option = self.by_label(home)["~/.codex/skills"]
        assert "installed" not in option.hint
        assert "no media-ai skills in it yet" in option.detail

    def test_skills_already_there_are_counted(self, home):
        from media_ai.cli._skillstore import copy_skill

        copy_skill("media-ai-shared", home / ".codex" / "skills")
        option = self.by_label(home)["~/.codex/skills"]
        assert "1 installed" in option.hint
        assert "up to date" in option.detail


# ---------------------------------------------------------------- announcement


def test_setup_opens_with_the_announcement(home):
    """Setup is the one moment a user is definitely reading the terminal, so it is
    where "do not build on this yet" has to be said."""
    args = make_args(skills_only=True, skills_dest=str(home / "sk"))
    _summary, prompter = run(args, [pick("media-ai-image")])
    shown = "\n".join(prompter.notes)
    assert "rapid development" in shown and "production" in shown


def test_announcements_are_display_only(home):
    """They will eventually come from a remote source; nothing about them may be
    load-bearing, and nothing may fail an install."""
    from media_ai.cli._announce import announcements

    for title, body in announcements():
        assert isinstance(title, str) and isinstance(body, str) and body


# ------------------------------------------------------------------ going back


class TestGoingBack:
    def test_escaping_the_destination_question_re_asks_the_skills(self, home):
        args = make_args(skills_only=True)
        # skills -> destinations (go back) -> skills -> destinations -> no custom path
        _summary, prompter = run(
            args, [pick("media-ai-image"), GoBack, pick("media-ai-video"), dests("~/.claude/skills")],
        )
        assert prompter.asked.count("Which skills should be installed?") == 2

    def test_the_second_answer_is_the_one_that_counts(self, home):
        args = make_args(skills_only=True)
        summary, _ = run(
            args, [pick("media-ai-image"), GoBack, pick("media-ai-video"), dests("~/.claude/skills")],
        )
        installed = summary["skills"][0]["installed"]
        assert "media-ai-video" in installed and "media-ai-image" not in installed

    def test_nothing_is_written_before_every_question_is_answered(self, home):
        """Going back has to be safe, which means the question half cannot write —
        the same property that makes Ctrl-C safe."""
        args = make_args(skills_dest=str(home / "sk"))
        with pytest.raises(Cancelled):
            run(args, [pick("media-ai-image"), [0], 0, None, Cancelled])
        assert not (home / "sk").exists(), "skills were installed before the last question"

    def test_back_inside_the_credentials_step_re_asks_one_provider(self, home):
        """`run_steps` rewinds a *step*, but this step asks one question per provider —
        unwinding all of them would throw away keys already typed."""
        args = make_args(skills_dest=str(home / "sk"))
        needed = sorted(init_mod.bindings_for_skills(["media-ai-image"]))
        two = [b for b in needed if not b.startswith("volc-ark/")][:2]
        picked = [needed.index(p) for p in two]
        # skills -> two bindings -> mode -> URL, key, URL, key; then back one binding.
        _summary, prompter = run(
            args,
            [pick("media-ai-image"), picked, 0, None, f"sk-{two[0]}", None, GoBack,
             None, f"sk-{two[0]}-again", None, "sk-second-try", 0],
        )
        assert prompter.asked.count("How should keys be stored?") == 1, "the whole step restarted"
        assert creds(home)[two[0]]["api_key"] == f"sk-{two[0]}-again"
        assert creds(home)[two[1]]["api_key"] == "sk-second-try"

    def test_back_at_the_first_provider_leaves_the_step(self, home):
        args = make_args(skills_dest=str(home / "sk"))
        _summary, prompter = run(args, [pick("media-ai-image"), [0], 0, GoBack, []])
        assert prompter.asked.count("These bindings can serve the skills you picked. Configure which?") == 2

    def test_a_deselected_provider_takes_its_key_with_it(self, home):
        """Go back and untick the provider: the key typed for it was already in hand,
        and writing it anyway would store a credential the user's final answer said
        not to configure."""
        args = make_args(skills_dest=str(home / "sk"))
        # skills -> providers -> mode -> (Base URL: go back) -> providers: none
        summary, _ = run(args, [pick("media-ai-image"), [0], 0, GoBack, []])
        assert summary["bindings"] == []
        assert not (home / "credentials.toml").exists()

    def test_going_back_to_a_local_only_skill_does_not_crash(self, home):
        """`providers` and `needed` are set by the same step; leaving one behind made
        the next step die on a KeyError, taking every answer with it."""
        args = make_args(skills_dest=str(home / "sk"))
        # image -> a binding -> mode -> back to bindings -> back to skills -> pick nothing
        summary, _ = run(args, [pick("media-ai-image"), [0], 0, GoBack, GoBack, []])
        assert summary["ok"] is True
        assert summary["bindings"] == []
        assert "media-ai-shared" in summary["skills"][0]["installed"]


def binding_index(name: str, skills=("media-ai-image",)) -> list[int]:
    """Answer the binding menu by name, for the same reason as :func:`pick`."""
    return [sorted(init_mod.bindings_for_skills(list(skills))).index(name)]


class TestVerifyProbesAfterTheWritesAndOnlyWhenAsked:
    """`--verify` used to ask a question *after* the apply phase, which made the cancel
    message ("nothing was written") false and let an Esc escape the driver. The question
    is gone now — every probe is a free GET, so the flag is the whole consent — but the
    ordering it forced is the part that has to hold.
    """

    def test_the_flag_probes_every_binding_that_got_a_credential(self, home, monkeypatch):
        import media_ai.cli._verify as verify_mod

        monkeypatch.setattr(verify_mod, "probe", lambda b: f"ok:{b}")
        args = make_args(skills_dest=str(home / "sk"), verify=True)
        summary, _ = run(args, [pick("media-ai-image"), binding_index("openai/gpt-image-2"), 0, None, SECRET])
        # Probed *by binding id*: the credential belongs to the binding, so verifying
        # one and reporting for its siblings would clear a key nothing touched.
        assert summary["verified"] == {"openai/gpt-image-2": "ok:openai/gpt-image-2"}
        assert (home / "credentials.toml").is_file()

    def test_without_the_flag_nothing_is_probed(self, home, monkeypatch):
        import media_ai.cli._verify as verify_mod

        monkeypatch.setattr(verify_mod, "probe", lambda b: pytest.fail(f"probed {b} without --verify"))
        args = make_args(skills_dest=str(home / "sk"))
        summary, _ = run(args, [pick("media-ai-image"), binding_index("openai/gpt-image-2"), 0, None, SECRET])
        assert "verified" not in summary

    def test_going_back_over_the_verify_step_is_not_an_error(self, home, monkeypatch):
        """The step asks nothing, so `run_steps` must skip back *over* it rather than
        stopping on a screen with no question."""
        import media_ai.cli._verify as verify_mod

        monkeypatch.setattr(verify_mod, "probe", lambda b: "ok")
        args = make_args(skills_dest=str(home / "sk"), verify=True)
        summary, _ = run(
            args,
            [pick("media-ai-image"), binding_index("openai/gpt-image-2"), 0, None, SECRET, GoBack, 0, None, SECRET],
        )
        assert summary["ok"] is True and summary["verified"] == {"openai/gpt-image-2": "ok"}

    def test_a_dry_run_never_probes(self, home, monkeypatch):
        """A probe makes a real network call, and a dry run is documented as reporting
        what *would* be written. It would also answer the wrong question: nothing was
        written, so it would report on whatever credentials happened to be there."""
        import media_ai.cli._verify as verify_mod

        monkeypatch.setattr(verify_mod, "probe", lambda b: pytest.fail(f"probed {b} on a dry run"))
        args = make_args(skills_dest=str(home / "sk"), verify=True, dry_run=True)
        summary, _ = run(args, [pick("media-ai-image"), binding_index("openai/gpt-image-2"), 0, None, SECRET])
        assert "verified" not in summary


# --------------------------------------------------------------------- receipt


def test_install_is_recorded_for_uninstall(home):
    from media_ai.cli._skillstore import load_receipt

    args = make_args(skills_only=True, skills_dest=str(home / "sk"))
    run(args, [pick("media-ai-image")])
    receipt = load_receipt()
    assert str(home / "sk") in receipt
    assert "media-ai-image" in receipt[str(home / "sk")]["skills"]


def test_dry_run_records_nothing(home):
    from media_ai.cli._skillstore import load_receipt, receipt_path

    args = make_args(skills_only=True, skills_dest=str(home / "sk"), dry_run=True)
    run(args, [pick("media-ai-image")])
    assert not receipt_path().exists() and load_receipt() == {}


# -------------------------------------------------------------- running it twice


def snapshot(root) -> dict[str, str]:
    """Every file under ``root``, by content — enough to prove a re-run changed nothing."""
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*")) if p.is_file()}


class TestRerunIsANoOp:
    """Installing twice is the normal case — it is how you upgrade — so the second
    run must not accumulate anything: no rewritten skills, no second backup of the
    same keys, no drifting receipt."""

    @staticmethod
    def flow(home):
        return make_args(skills_dest=str(home / "sk")), [pick("media-ai-image"), [0], 0, None, SECRET]

    def test_the_second_run_writes_no_skill_files(self, home):
        args, answers = self.flow(home)
        run(args, answers)
        summary, _ = run(*self.flow(home))
        entry = summary["skills"][0]
        assert entry["written"] == [], "re-wrote skills that were already current"
        assert "media-ai-image" in entry["installed"], "should still report them as installed"

    def test_the_second_run_asks_nothing_extra(self, home):
        """An unchanged copy is not a collision, so there is nothing to decide."""
        args, answers = self.flow(home)
        _summary, first = run(args, answers)
        _summary, second = run(*self.flow(home))
        assert second.asked == first.asked

    def test_the_second_run_leaves_the_tree_byte_identical(self, home):
        args, answers = self.flow(home)
        run(args, answers)
        before = snapshot(home)
        run(*self.flow(home))
        assert snapshot(home) == before

    def test_identical_answers_do_not_pile_up_backups(self, home):
        """A `.bak` per run would be a second copy of the same keys, under a name
        nobody remembers to delete."""
        args, answers = self.flow(home)
        run(args, answers)
        summary, _ = run(*self.flow(home))
        assert summary["backed_up"] == []
        assert not list(home.glob("credentials.toml.bak*"))

    def test_a_changed_answer_still_gets_backed_up(self, home):
        args, answers = self.flow(home)
        run(args, answers)
        args2, _ = self.flow(home)
        summary, _ = run(args2, [pick("media-ai-image"), [0], 0, None, "sk-a-different-key-4242"])
        assert summary["backed_up"], "overwriting a key must keep the old file"

    def test_a_rerun_repairs_a_loose_credentials_mode(self, home):
        """The resolver refuses a group/world-readable credentials.toml, and re-running
        the wizard is the obvious remedy — so the write cannot be skipped just because
        the content matches."""
        args, answers = self.flow(home)
        run(args, answers)
        path = home / "credentials.toml"
        path.chmod(0o644)
        run(*self.flow(home))
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_wrote_lists_only_files_that_were_written(self, home):
        """A caller diffing `wrote[]` across runs to spot config churn should not get
        a hit from a run that wrote the same bytes."""
        args, answers = self.flow(home)
        summary, _ = run(args, answers)
        assert str(home / "credentials.toml") in summary["wrote"]
        for path in summary["wrote"]:
            assert Path(path).is_file(), f"{path} reported as written but does not exist"

    def test_a_backup_is_never_world_readable_even_briefly(self, home):
        """It holds every key the original did."""
        args, answers = self.flow(home)
        run(args, answers)
        args2, _ = self.flow(home)
        summary, _ = run(args2, [pick("media-ai-image"), [0], 0, None, "sk-a-different-key-4242"])
        backup = Path(summary["backed_up"][0])
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600

    def test_an_unattended_upgrade_never_stops_on_a_prompt(self, home):
        """`--non-interactive` has to hold for *every* question, including the one an
        edited copy would otherwise raise."""
        dest = home / "sk"
        args = make_args(skills_only=True, non_interactive=True, skills_dest=str(dest))
        run(args, [])
        (dest / "media-ai-image" / "SKILL.md").write_text("edited", encoding="utf-8")
        summary, prompter = run(make_args(skills_only=True, non_interactive=True, skills_dest=str(dest)), [])
        assert prompter.asked == []
        assert "media-ai-image" in summary["skills"][0]["written"]
        assert "edited" not in (dest / "media-ai-image" / "SKILL.md").read_text()

    def test_a_locally_edited_skill_is_still_offered(self, home):
        """Unchanged is silent; *changed* is exactly what the user should be asked about."""
        args, answers = self.flow(home)
        run(args, answers)
        (home / "sk" / "media-ai-image" / "SKILL.md").write_text("my own version", encoding="utf-8")
        args2, _ = self.flow(home)
        summary, prompter = run(args2, [pick("media-ai-image"), 1, [0], 0, None, SECRET])  # 1 = keep mine
        assert any("differs from the packaged skill" in q for q in prompter.asked)
        assert "media-ai-image" in summary["skills"][0]["skipped"]
        assert (home / "sk" / "media-ai-image" / "SKILL.md").read_text() == "my own version"


# --------------------------------------------------------------- credentials


def image_only_flow(home, *, key=SECRET, mode=0):
    """skills=[image] -> dest -> providers=[first] -> storage mode -> Base URL -> key."""
    return make_args(skills_dest=str(home / "sk")), [pick("media-ai-image"), [0], mode, None, key]


def test_pasted_key_lands_in_credentials_toml(home):
    args, answers = image_only_flow(home)
    summary, _ = run(args, answers)
    written = creds(home)
    assert summary["bindings"], "no provider configured"
    provider = summary["bindings"][0]
    assert written[provider]["api_key"] == SECRET


def test_credentials_file_is_0600(home):
    args, answers = image_only_flow(home)
    run(args, answers)
    path = home / "credentials.toml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)


def test_env_reference_mode_keeps_the_key_off_disk(home):
    """"Off disk" now means the secret file is never created at all.

    Previously credentials.toml was written holding `env://VAR` — the key itself was
    absent, but the file was there. With the reference stored in the config there is
    nothing secret left to write, so the wizard writes nothing secret.
    """
    args = make_args(skills_dest=str(home / "sk"))
    # mode 1 = reference an env var; then accept the suggested variable name
    summary, _ = run(args, [pick("media-ai-image"), [0], 1, None, None])
    binding = summary["bindings"][0]
    assert not (home / "credentials.toml").exists()

    from media_ai.core.config import load_config

    assert load_config(home / "config.toml").bindings[binding].credential.startswith("env://")


def test_secret_never_reaches_the_summary(home):
    """The summary is printed to stdout; a key in it would be a leak."""
    args, answers = image_only_flow(home)
    summary, _ = run(args, answers)
    assert SECRET not in json.dumps(summary)


def test_blank_key_configures_nothing(home):
    args, answers = image_only_flow(home, key="   ")
    summary, _ = run(args, answers)
    assert summary["bindings"] == []
    assert not (home / "credentials.toml").exists()


def test_non_ark_always_prompts_for_base_url_with_the_manifest_default(home):
    binding = "openai/gpt-image-2"
    summary, prompter = run(
        make_args(skills_dest=str(home / "sk")),
        [pick("media-ai-image"), binding_index(binding), 0, None, SECRET],
    )
    base = next(i for i, q in enumerate(prompter.asked) if q == f"{binding} — Base URL")
    key = next(i for i, q in enumerate(prompter.asked) if q.endswith("API key"))
    assert base < key
    assert config(home)["bindings"][binding]["base_url"] == "https://api.openai.com/v1"
    assert summary["bindings"] == [binding]


def test_ark_is_configured_url_then_endpoint_then_key(home):
    """Ark's endpoint and credential are one configuration unit, in wire order."""
    binding = "volc-ark/seedream-5.0"
    endpoint = "ep-20260728-seedream50"
    args = make_args(skills_dest=str(home / "sk"))
    summary, prompter = run(
        args,
        [pick("media-ai-image"), binding_index(binding), 0, None, endpoint, SECRET],
    )
    questions = prompter.asked
    base = next(i for i, q in enumerate(questions) if q == f"{binding} — Base URL")
    wire = next(i for i, q in enumerate(questions) if "endpoint ID sent as model" in q)
    key = next(i for i, q in enumerate(questions) if q.endswith("API key"))
    assert base < wire < key
    entry = config(home)["bindings"][binding]
    assert entry["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert entry["endpoint_id"] == endpoint
    assert "model_id" not in entry
    assert creds(home)[binding]["api_key"] == SECRET
    assert summary["bindings"] == [binding]


def test_ark_endpoint_id_must_use_the_documented_shape(home):
    binding = "volc-ark/seedream-5.0"
    args = make_args(skills_dest=str(home / "sk"))
    with pytest.raises(MediaError) as ei:
        run(args, [pick("media-ai-image"), binding_index(binding), 0, None, "not-an-endpoint"])
    assert ei.value.code == "endpoint_id_invalid"
    assert not (home / "config.toml").exists()


def test_ark_init_replaces_a_legacy_model_id_with_endpoint_id(home):
    binding = "volc-ark/seedream-5.0"
    (home / "config.toml").write_text(
        f'schema = 2\n\n[bindings."{binding}"]\n'
        'model_id = "ep-20260728-legacy"\ncredential = "env://OLD_ARK_KEY"\n', encoding="utf-8",
    )
    endpoint = "ep-20260728-current"
    summary, _ = run(
        make_args(skills_dest=str(home / "sk")),
        [pick("media-ai-image"), binding_index(binding), 0, None, endpoint, SECRET],
    )
    entry = config(home)["bindings"][binding]
    assert entry["endpoint_id"] == endpoint and "model_id" not in entry
    assert summary["bindings"] == [binding]


def test_advanced_non_ark_configures_url_model_then_key_per_binding(home):
    binding = "openai/gpt-image-2"
    args = make_args(skills_dest=str(home / "sk"), advanced=True)
    summary, prompter = run(
        args,
        [pick("media-ai-image"), binding_index(binding), 0, None, None, SECRET],
    )
    questions = prompter.asked
    base = next(i for i, q in enumerate(questions) if q == f"{binding} — Base URL")
    model = next(i for i, q in enumerate(questions) if q == f"{binding} — model ID sent on the wire")
    key = next(i for i, q in enumerate(questions) if q.endswith("API key"))
    assert base < model < key
    entry = config(home)["bindings"][binding]
    assert entry["base_url"] == "https://api.openai.com/v1"
    assert "model_id" not in entry, "accepting the manifest default should not restate it"
    assert summary["bindings"] == [binding]


# --------------------------------------------------------------- scene defaults


def test_a_credential_free_binding_still_gets_a_scene_default(home):
    """A default is what makes a call that names no binding work, and needing no key has
    nothing to do with that.

    ``local/ffmpeg`` never appeared in ``creds``, so the wizard wrote no default for the
    scenes only it serves — and ``media-ai video concat`` on a completely fresh install
    answered ``no_default_binding`` while naming, in its own hint, the free offline
    binding it should have used.
    """
    args = make_args(non_interactive=True, skills_dest=str(home / "sk"))
    summary, _ = run(args, [])

    assert summary["bindings"] == []                     # nothing was configured at all
    assert summary["defaults"]["video.concat"] == "local/ffmpeg"
    assert summary["defaults"]["animation.from_video"] == "local/ffmpeg"
    assert summary["defaults"]["animation.from_frames"] == "local/ffmpeg"
    assert config(home)["defaults"]["video.concat"] == "local/ffmpeg"


def test_a_local_scene_keeps_its_default_when_the_group_goes_to_a_model(home):
    """The other half of the same bug: with two generators configured, the group *is*
    asked — and the answer is a model, which does not serve ``video.concat``. Writing
    defaults only for the scenes the chosen binding serves dropped that one on the floor.
    """
    picked = ["gemini/veo-3.1", "volc-ark/seedance-2.0"]
    offered = sorted(init_mod.bindings_for_skills(["media-ai-video"]))
    args = make_args(skills_dest=str(home / "sk"))
    summary, prompter = run(args, [
        pick("media-ai-video"),
        [offered.index(b) for b in picked],
        0,                                      # how keys are stored
        None, SECRET,                           # gemini: base URL, key
        None, "ep-20260101-abc", SECRET,        # ark: base URL, endpoint id, key
        0,                                      # the group question: the first generator
    ])

    assert any("Default for `media-ai video`" in q for q in prompter.asked), "the group was not contested"
    defaults = summary["defaults"]
    assert defaults["video.text_to_video"] == "gemini/veo-3.1"
    assert defaults["video.concat"] == "local/ffmpeg", "the local scene lost its default"


def test_a_scene_the_group_answer_cannot_serve_gets_its_own_question(home):
    """Third shape of the same bug, and the one no single-candidate rule can catch.

    ElevenLabs splits speech in two — ``eleven-multilingual-v2`` reads a single voice,
    ``eleven-v3`` performs a dialogue — while ``gemini-tts`` does both. Configure all
    three and answer the group question with either ElevenLabs model, and the *other*
    scene has two candidates, neither of them the answer: it used to be written nowhere,
    so ``media-ai speech dialogue`` refused on a freshly finished install. The leftover
    scene is a second decision, so it is asked rather than inferred from the provider.
    """
    picked = ["elevenlabs/eleven-multilingual-v2", "elevenlabs/eleven-v3", "gemini/gemini-tts"]
    offered = sorted(init_mod.bindings_for_skills(["media-ai-speech"]))
    args = make_args(skills_dest=str(home / "sk"))
    summary, prompter = run(args, [
        pick("media-ai-speech"),
        [offered.index(b) for b in picked],
        0,                                                  # how keys are stored
        None, SECRET, None, SECRET, None, SECRET,           # three bindings: base URL, key
        0,                                                  # the group: eleven-multilingual-v2
        0,                                                  # dialogue, which it cannot serve
    ])

    assert any("`speech.dialogue`" in q for q in prompter.asked), "the leftover scene was not asked about"
    defaults = summary["defaults"]
    assert defaults["speech.text_to_speech"] == "elevenlabs/eleven-multilingual-v2"
    assert defaults["speech.dialogue"] == "elevenlabs/eleven-v3"
    # Written, not just summarized: the default is what makes a call naming no binding work.
    assert config(home)["defaults"]["speech.dialogue"] == "elevenlabs/eleven-v3"


def test_every_scene_a_configured_binding_serves_ends_up_with_a_default(home):
    """The invariant behind all three: finish the wizard and no scene you configured a
    binding for is left answering ``no_default_binding``."""
    from media_ai.core.scene import scenes_for_group

    picked = ["elevenlabs/eleven-multilingual-v2", "elevenlabs/eleven-v3", "gemini/gemini-tts"]
    offered = sorted(init_mod.bindings_for_skills(["media-ai-speech"]))
    for group_answer in range(len(picked)):
        summary, _ = run(make_args(skills_dest=str(home / "sk")), [
            pick("media-ai-speech"),
            [offered.index(b) for b in picked],
            0, None, SECRET, None, SECRET, None, SECRET,
            group_answer, 0,
        ])
        served = {s.value for s in scenes_for_group("speech")
                  if any(s in CATALOG.get(b).scenes for b in picked)}
        assert served <= set(summary["defaults"]), f"group answer {group_answer} left {served - set(summary['defaults'])}"


def test_the_offline_placeholder_is_never_proposed_as_a_default(home):
    """`mock/mock` needs no credential either, and a default is the strongest possible
    recommendation — it is what a call with no `--binding` silently gets."""
    args = make_args(non_interactive=True, skills_dest=str(home / "sk"))
    summary, _ = run(args, [])
    assert "mock/mock" not in summary["defaults"].values()
    assert "mock/mock" not in init_mod._configuration_free_bindings()


def test_skills_only_writes_no_defaults(home):
    """`--skills-only` is "without changing credentials or bindings", and a default is
    configuration like any other."""
    args = make_args(skills_only=True, non_interactive=True, skills_dest=str(home / "sk"))
    summary, _ = run(args, [])
    assert summary["defaults"] == {}
    assert not (home / "config.toml").exists()


# --------------------------------------------------------------- merge/backup


def test_existing_accounts_are_preserved(home):
    path = home / "credentials.toml"
    path.write_text('[other_account]\napi_key = "keep-me"\n', encoding="utf-8")
    path.chmod(0o600)
    args, answers = image_only_flow(home)
    run(args, answers)
    assert creds(home)["other_account"]["api_key"] == "keep-me"


def test_existing_file_is_backed_up(home):
    path = home / "credentials.toml"
    path.write_text('[other]\napi_key = "old"\n', encoding="utf-8")
    path.chmod(0o600)
    args, answers = image_only_flow(home)
    summary, _ = run(args, answers)
    assert summary["backed_up"], "no backup recorded"
    backup = home / summary["backed_up"][0].rsplit("/", 1)[-1]
    assert "old" in backup.read_text()


def test_backup_of_a_secret_file_stays_private(home):
    path = home / "credentials.toml"
    path.write_text('[other]\napi_key = "old"\n', encoding="utf-8")
    path.chmod(0o600)
    args, answers = image_only_flow(home)
    summary, _ = run(args, answers)
    backup = home / summary["backed_up"][0].rsplit("/", 1)[-1]
    assert not backup.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)


def test_unreadable_existing_config_is_an_error_not_an_overwrite(home):
    path = home / "credentials.toml"
    path.write_text("[broken\nnot toml", encoding="utf-8")
    path.chmod(0o600)
    args, answers = image_only_flow(home)
    with pytest.raises(MediaError, match="could not read"):
        run(args, answers)
    assert "not toml" in path.read_text(), "a broken file must not be clobbered"


# ------------------------------------------------------------------ dry run


def test_dry_run_writes_nothing(home):
    args = make_args(skills_dest=str(home / "sk"), dry_run=True)
    summary, _ = run(args, [pick("media-ai-image"), [0], 0, None, SECRET])
    assert summary["dry_run"] is True
    assert not (home / "credentials.toml").exists()
    assert not (home / "sk").exists()


def test_dry_run_still_reports_what_it_would_write(home):
    args = make_args(skills_dest=str(home / "sk"), dry_run=True)
    summary, _ = run(args, [pick("media-ai-image"), [0], 0, None, SECRET])
    assert summary["wrote"], "dry run should still name the files"
    assert summary["skills"][0]["installed"]


# ------------------------------------------------------------------- cancel


def test_cancel_maps_to_the_cli_exit_code(home, monkeypatch):
    """Cancelling is a CLI outcome (exit 2), not the timeout run() gives KeyboardInterrupt."""
    monkeypatch.setattr(init_mod, "get_prompter", lambda **kw: ScriptedPrompter([Cancelled]))
    with pytest.raises(MediaError) as ei:
        init_mod._do(make_args(skills_dest=str(home / "sk")))
    assert ei.value.category == ErrorCategory.CLI
    assert ei.value.exit_code == 2


def test_cancel_midway_writes_nothing(home, monkeypatch):
    """Cancelling after answering some questions must not leave a partial config."""
    # skills, destinations, providers and the Base URL answered; then abort at the key prompt.
    monkeypatch.setattr(
        init_mod, "get_prompter",
        lambda **kw: ScriptedPrompter([pick("media-ai-image"), [0], 0, None, Cancelled]),
    )
    with pytest.raises(MediaError):
        init_mod._do(make_args(skills_dest=str(home / "sk")))
    assert not (home / "credentials.toml").exists()
    assert not (home / "config.toml").exists()


# ------------------------------------------------------------- machine contract


def test_stdout_is_exactly_one_json_object(home):
    res = subprocess.run(
        [sys.executable, "-m", "media_ai", "init", "--skills-only", "--non-interactive",
         "--skills-dest", str(home / "sk")],
        capture_output=True, text=True, timeout=60,
        env={**dict(__import__("os").environ), "HOME": str(home),
             "MEDIA_CREDENTIALS_FILE": str(home / "credentials.toml"),
             "MEDIA_CONFIG_FILE": str(home / "config.toml")},
    )
    assert res.returncode == 0, res.stderr
    parsed = json.loads(res.stdout)
    assert parsed["ok"] is True and parsed["command"] == "init"
    assert len(res.stdout.strip().splitlines()) == 1


def test_failure_is_also_one_json_object(home):
    res = subprocess.run(
        [sys.executable, "-m", "media_ai", "init", "--non-interactive"],
        capture_output=True, text=True, timeout=60,
        env={**dict(__import__("os").environ), "HOME": str(home),
             "MEDIA_CREDENTIALS_FILE": str(home / "credentials.toml"),
             "MEDIA_CONFIG_FILE": str(home / "config.toml")},
    )
    assert res.returncode == 2
    assert json.loads(res.stdout)["ok"] is False


def test_init_is_a_registered_group():
    from media_ai.__main__ import _GROUPS

    assert "init" in _GROUPS


# ------------------------------------------------------------------- verify


class TestProbeClassification:
    """A bad key does not reliably arrive as an auth error, so classification
    cannot key off the category alone — Google answers an invalid API key with
    400 INVALID_ARGUMENT, which the adapter maps to `validation` (exit 3)."""

    @staticmethod
    def err(message="boom", category=ErrorCategory.PROVIDER, code=""):
        return MediaError(message, category=category, code=code or None)

    def test_no_error_is_ok(self):
        from media_ai.cli._verify import classify

        assert classify(None) == "ok"

    def test_gemini_invalid_key_arrives_as_validation(self):
        from media_ai.cli._verify import classify

        exc = self.err("API key not valid. Please pass a valid API key.", ErrorCategory.VALIDATION)
        assert classify(exc) == "invalid"

    def test_auth_category_is_invalid(self):
        from media_ai.cli._verify import classify

        assert classify(self.err("401", ErrorCategory.AUTH)) == "invalid"

    def test_absent_credential_is_missing_not_invalid(self):
        from media_ai.cli._verify import classify

        exc = self.err("no credential found for provider 'openai'. Set an env var", ErrorCategory.AUTH)
        assert classify(exc) == "missing"

    def test_rate_limit_means_the_key_worked(self):
        from media_ai.cli._verify import classify

        assert classify(self.err("insufficient_quota", ErrorCategory.RATE_LIMIT)) == "no-quota"

    def test_not_found_means_authentication_succeeded(self):
        from media_ai.cli._verify import classify

        # The read-only probes query a job id that cannot exist; auth happens first.
        assert classify(self.err("task not found", ErrorCategory.NOT_FOUND)) == "ok"

    def test_network_fault_says_nothing_about_the_key(self):
        from media_ai.cli._verify import classify

        assert classify(self.err("connection reset", ErrorCategory.PROVIDER)) == "unreachable"

    def test_unknown_provider_is_reported_not_raised(self):
        from media_ai.cli._verify import probe

        assert probe("nonexistent-provider") == "unsupported"

    def test_probe_never_raises(self, monkeypatch):
        import media_ai.cli._verify as verify_mod

        monkeypatch.setitem(verify_mod._PROBES, "boom", lambda: (_ for _ in ()).throw(RuntimeError("kaboom")))
        assert verify_mod.probe("boom") == "unreachable"


# ------------------------------------------------------- model lifecycle in the UI


class TestBindingChoicesSurfaceLifecycle:
    """Discovery lists preview and deprecated bindings — withholding them would be
    worse — so anything offering one to a human must label it. Somebody picking a
    superseded model on setup day should not find that out months later."""

    @staticmethod
    def hints(skills=("media-ai-image", "media-ai-video", "media-ai-speech")):
        from media_ai.cli.init import _binding_choice

        offered = init_mod.bindings_for_skills(list(skills))
        return {bid: _binding_choice(bid, sk).hint for bid, sk in offered.items()}

    def test_preview_bindings_are_labelled(self):
        assert "preview" in self.hints()["gemini/veo-3.1"]

    def test_an_unverified_binding_says_so(self):
        """Derived, not hardcoded: `verified` records a real live run, so which binding
        carries one changes whenever somebody does one. A test naming a specific binding
        turns "we tested it today" into a build failure."""
        hints = self.hints()
        unverified = [bid for bid in hints if CATALOG.get(bid).verified is None]
        if not unverified:
            pytest.skip("every offered binding has been live-verified")
        for bid in unverified:
            assert "never live-tested" in hints[bid], bid

    def test_a_verified_binding_shows_its_date(self):
        hints = self.hints()
        verified = [bid for bid in hints if CATALOG.get(bid).verified]
        if not verified:
            pytest.skip("no offered binding has been live-verified yet")
        for bid in verified:
            assert CATALOG.get(bid).verified in hints[bid], bid

    def test_every_candidate_carries_a_hint(self):
        """A blank hint would render as an unqualified recommendation."""
        for bid, hint in self.hints().items():
            assert hint, f"{bid} offered with no lifecycle hint"

    def test_the_hint_names_what_the_binding_unlocks(self):
        assert "image" in self.hints()["gemini/nano-banana-2"]

    def test_options_carry_the_binding_id_as_their_value(self):
        """The wizard writes Option.value into config; a mismatch would write a label."""
        from media_ai.cli.init import _binding_choice

        for bid, skills in init_mod.bindings_for_skills(["media-ai-image"]).items():
            assert _binding_choice(bid, skills).value == bid


# ------------------------------------------------------------- probe strategies


class TestEveryProbeStrategyActuallyRuns:
    """The probes rotted once already, and nothing noticed.

    `probe()` swallows every exception so the wizard cannot be taken down by one — which
    also means a probe calling a method that no longer exists reports `unreachable` for
    a perfectly good key, forever, in silence. These drive each strategy against a fake
    transport so a rename cannot pass unseen again.
    """

    def _run(self, monkeypatch, binding_id, responses):
        from conftest import FakeClient, adapter_for

        import media_ai.cli._verify as verify_mod

        adapter = adapter_for(binding_id)
        fake = FakeClient(responses)
        monkeypatch.setattr(adapter, "_prepare", lambda **kw: (fake, {"Authorization": "Bearer test"}))
        monkeypatch.setattr(verify_mod, "_adapter", lambda b: adapter)
        return verify_mod.probe(binding_id), fake

    @pytest.mark.parametrize("binding_id", ["openai/gpt-image-2", "gemini/nano-banana-2",
                                            "elevenlabs/eleven-multilingual-v2", "volc-ark/seedance-2.0"])
    def test_a_good_key_reads_as_ok(self, monkeypatch, binding_id):
        verdict, fake = self._run(monkeypatch, binding_id, [{}])
        assert verdict == "ok"
        assert fake.calls[0]["method"] == "GET", "a probe must not create anything"

    @pytest.mark.parametrize("binding_id", ["openai/gpt-image-2", "gemini/nano-banana-2",
                                            "elevenlabs/eleven-multilingual-v2", "volc-ark/seedance-2.0"])
    def test_the_request_names_no_model(self, monkeypatch, binding_id):
        """A probe that named a model would test that model, not the credential — and a
        4xx about the model would be read as a bad key."""
        _, fake = self._run(monkeypatch, binding_id, [{}])
        model_id = CATALOG.get(binding_id).model_id
        assert model_id not in fake.calls[0]["path"]

    def test_a_rejected_key_reads_as_invalid(self, monkeypatch):
        from media_ai.core.errors import ErrorCategory, MediaError

        verdict, _ = self._run(monkeypatch, "openai/gpt-image-2",
                               [MediaError("invalid api key", category=ErrorCategory.AUTH)])
        assert verdict == "invalid"

    def test_a_not_found_still_proves_the_key(self, monkeypatch):
        """Ark has no listing endpoint: it looks up a task that cannot exist, and the
        404 only arrives because authentication succeeded first."""
        from media_ai.core.errors import ErrorCategory, MediaError

        verdict, _ = self._run(monkeypatch, "volc-ark/seedance-2.0",
                               [MediaError("no such task", category=ErrorCategory.NOT_FOUND)])
        assert verdict == "ok"

    def test_an_unconfigured_binding_never_raises(self):
        """`_adapter` is not stubbed here, so this exercises the real lookup failing."""
        from media_ai.cli._verify import probe

        assert probe("openai/gpt-image-2") == "unreachable"

    def test_a_provider_with_no_strategy_says_so(self):
        from media_ai.cli._verify import probe

        assert probe("mock/mock") == "unsupported"


class TestNothingToCheckIsNeverReportedAsABadKey:
    """Calling an unresolved credential "invalid" sends someone to rotate a working key.

    Found live: a `cred://` account name that does not exist in `credentials.toml`
    raises `credential_unresolved` with the message "no [openai] account in
    credentials.toml" — which matched none of the prose markers and so came back as
    `invalid`. Classification now leads with the error *code*, which is a contract;
    the sentence is not, and can be reworded by anyone without a thought for this list.
    """

    @pytest.mark.parametrize("code", ["credential_unresolved", "credential_missing",
                                      "credential_scheme_unknown", "credential_backend_missing"])
    def test_every_unresolved_credential_reads_as_missing(self, code):
        from media_ai.cli._verify import classify
        from media_ai.core.errors import ErrorCategory, MediaError

        # AUTH is the category all four carry, and AUTH alone would mean "invalid".
        exc = MediaError("no [openai] account in credentials.toml",
                         category=ErrorCategory.AUTH, code=code)
        assert classify(exc) == "missing"

    def test_a_key_the_provider_actually_rejected_still_reads_as_invalid(self):
        from media_ai.cli._verify import classify
        from media_ai.core.errors import ErrorCategory, MediaError

        assert classify(MediaError("API key not valid", category=ErrorCategory.VALIDATION)) == "invalid"
        assert classify(MediaError("Unauthorized", category=ErrorCategory.AUTH)) == "invalid"
