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

import pytest

from media_ai.cli import init as init_mod
from media_ai.cli._prompt import Cancelled, ScriptedPrompter
from media_ai.core.errors import ErrorCategory, MediaError

SECRET = "sk-sentinel-do-not-leak-9f3a"


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
    assert summary["providers"] == []
    assert not (home / "credentials.toml").exists()


# -------------------------------------------------------------------- skills


def test_shared_skill_is_always_included(home):
    """media-ai-shared is the contract the others build on."""
    args = make_args(skills_dest=str(home / "sk"), skills_only=True)
    summary, _ = run(args, [[2]])  # pick a single non-shared skill
    assert "media-ai-shared" in summary["skills"][0]["installed"]


def test_installs_to_several_destinations(home):
    args = make_args(skills_only=True)
    # skills=[image]; destinations=first two; no custom path
    summary, _ = run(args, [[2], [0, 1], False])
    assert len(summary["skills"]) == 2
    assert all(entry["installed"] for entry in summary["skills"])


def test_custom_destination_is_accepted(home):
    args = make_args(skills_only=True)
    summary, _ = run(args, [[2], [], True, str(home / "custom")])
    assert summary["skills"][0]["dest"] == str(home / "custom")
    assert (home / "custom" / "media-ai-shared" / "SKILL.md").is_file()


def test_existing_skill_can_be_skipped(home):
    dest = home / "sk"
    (dest / "media-ai-shared").mkdir(parents=True)
    (dest / "media-ai-shared" / "SKILL.md").write_text("hand written", encoding="utf-8")
    args = make_args(skills_only=True, skills_dest=str(dest))
    summary, _ = run(args, [[5], 1])  # 1 = "skip"
    assert "media-ai-shared" in summary["skills"][0]["skipped"]
    assert (dest / "media-ai-shared" / "SKILL.md").read_text() == "hand written"


def test_existing_skill_can_be_overwritten(home):
    dest = home / "sk"
    (dest / "media-ai-shared").mkdir(parents=True)
    (dest / "media-ai-shared" / "SKILL.md").write_text("stale", encoding="utf-8")
    args = make_args(skills_only=True, skills_dest=str(dest))
    run(args, [[5], 0])  # 0 = "overwrite"
    assert "stale" not in (dest / "media-ai-shared" / "SKILL.md").read_text()


# --------------------------------------------------------------- credentials


def image_only_flow(home, *, key=SECRET, mode=0):
    """skills=[image] -> dest -> providers=[first] -> storage mode -> key."""
    return make_args(skills_dest=str(home / "sk")), [[2], [0], mode, key]


def test_pasted_key_lands_in_credentials_toml(home):
    args, answers = image_only_flow(home)
    summary, _ = run(args, answers)
    written = creds(home)
    assert summary["providers"], "no provider configured"
    provider = summary["providers"][0]
    assert written[provider]["api_key"] == SECRET


def test_credentials_file_is_0600(home):
    args, answers = image_only_flow(home)
    run(args, answers)
    path = home / "credentials.toml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)


def test_env_reference_mode_keeps_the_key_off_disk(home):
    args = make_args(skills_dest=str(home / "sk"))
    # mode 1 = reference an env var; then accept the suggested variable name
    summary, _ = run(args, [[2], [0], 1, None])
    provider = summary["providers"][0]
    assert creds(home)[provider]["api_key"].startswith("env://")


def test_secret_never_reaches_the_summary(home):
    """The summary is printed to stdout; a key in it would be a leak."""
    args, answers = image_only_flow(home)
    summary, _ = run(args, answers)
    assert SECRET not in json.dumps(summary)


def test_blank_key_configures_nothing(home):
    args, answers = image_only_flow(home, key="   ")
    summary, _ = run(args, answers)
    assert summary["providers"] == []
    assert not (home / "credentials.toml").exists()


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
    summary, _ = run(args, [[2], [0], 0, SECRET])
    assert summary["dry_run"] is True
    assert not (home / "credentials.toml").exists()
    assert not (home / "sk").exists()


def test_dry_run_still_reports_what_it_would_write(home):
    args = make_args(skills_dest=str(home / "sk"), dry_run=True)
    summary, _ = run(args, [[2], [0], 0, SECRET])
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
    # skills, destinations, providers answered; then the user aborts at the key prompt.
    monkeypatch.setattr(
        init_mod, "get_prompter",
        lambda **kw: ScriptedPrompter([[2], [0], 0, Cancelled]),
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
    assert parsed["ok"] is True and parsed["operation"] == "init"
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


class TestModelChoicesSurfaceLifecycle:
    """Discovery still lists deprecated and preview models — withholding them would be
    worse — so anything offering a model to a human must label it. A user picking a
    superseded model on setup day should not find out months later."""

    @staticmethod
    def hints(provider, group):
        from media_ai.cli.init import _models_for

        return {o.label: o.hint for o in _models_for(provider, group)}

    def test_deprecated_model_is_labelled_with_its_replacement(self):
        hint = self.hints("gemini", "image")["gemini-2.5-flash-image"]
        assert "deprecated" in hint and "gemini-3.1-flash-image" in hint

    def test_preview_model_is_labelled(self):
        assert all("preview" in h for h in self.hints("gemini", "video").values())

    def test_unverified_model_says_so(self):
        assert "never live-tested" in self.hints("gemini", "video")["veo-3.1-generate-preview"]

    def test_verified_model_shows_its_date(self):
        assert "2026-07-12" in self.hints("gemini", "image")["gemini-3.1-flash-image"]

    def test_every_candidate_carries_a_hint(self):
        """A blank hint would render as an unqualified recommendation."""
        for provider, group in (("gemini", "image"), ("gemini", "video"), ("openai", "image")):
            for label, hint in self.hints(provider, group).items():
                assert hint, f"{provider}/{group}:{label} offered with no lifecycle hint"

    def test_current_and_verified_sort_ahead_of_deprecated(self):
        from media_ai.cli.init import _models_for

        labels = [o.label for o in _models_for("gemini", "image")]
        assert labels[0] == "gemini-3.1-flash-image"
        assert labels[-1] == "gemini-2.5-flash-image"

    def test_removed_models_are_never_offered(self):
        from media_ai.cli.init import _models_for

        for group in ("image", "video"):
            labels = {o.label for o in _models_for("openai", group)} | {
                o.label for o in _models_for("gemini", group)
            }
            assert not any(x in labels for x in ("dall-e-3", "sora", "imagen-3.0-generate-002"))

    def test_options_carry_the_id_as_their_value(self):
        """The wizard writes Option.value into config; a mismatch would write a label."""
        from media_ai.cli.init import _models_for

        for o in _models_for("gemini", "image"):
            assert o.value == o.label

    def test_unknown_provider_degrades_to_free_text(self):
        from media_ai.cli.init import _models_for

        assert _models_for("nonexistent-provider", "image") == []
