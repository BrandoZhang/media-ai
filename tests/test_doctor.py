"""``media-ai doctor`` — the offline health check.

Two things are load-bearing and easy to regress: it must never make a network call
(that is what ``init --verify`` is for), and it must never print a key while
reporting which keys are configured.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import pytest

from media_ai.cli import doctor as doctor_mod
from media_ai.cli._skillstore import copy_skill

SECRET = "sk-sentinel-doctor-must-not-print-7c1d"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_CREDENTIALS_FILE", str(tmp_path / "cfg" / "credentials.toml"))
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(tmp_path / "cfg" / "config.toml"))
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    (tmp_path / "fakehome").mkdir()
    (tmp_path / "cfg").mkdir()
    for var in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ARK_API_KEY",
                "VOLC_API_KEY", "ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "MEDIA_CRED_BROKER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def diagnose():
    return doctor_mod._diagnose(argparse.Namespace(pretty=False, log_level=None, metadata_out=None))


def checks(result) -> dict[str, dict]:
    return {c["check"]: c for c in result["checks"]}


# ------------------------------------------------------------------- verdicts


def test_reports_the_version_and_the_local_media_stack(home):
    found = checks(diagnose())
    assert "media-ai" in found["version"]["detail"]
    assert found["ffmpeg"]["status"] == "ok"
    assert found["pillow"]["status"] == "ok"


def test_status_is_the_worst_of_the_checks(home):
    result = diagnose()
    assert result["status"] in ("ok", "warn", "fail")
    assert result["status"] == max((c["status"] for c in result["checks"]), key=("ok", "warn", "fail").index)


def test_a_clean_machine_with_no_keys_is_a_warning_not_a_failure(home):
    """Nobody configures all four providers; an unconfigured one is not a fault."""
    result = diagnose()
    assert result["status"] == "warn"
    assert all(c["status"] != "fail" for c in result["checks"])


def test_exit_code_is_zero_even_when_checks_warn(home):
    """A diagnosis that ran is not a CLI failure, whatever it found — the contract
    reserves non-zero for error categories. `status` is what a script branches on."""
    res = subprocess.run(
        [sys.executable, "-m", "media_ai", "doctor"],
        capture_output=True, text=True, timeout=60, cwd=str(home),
        env={**dict(__import__("os").environ), "HOME": str(home / "fakehome"),
             "MEDIA_CREDENTIALS_FILE": str(home / "cfg" / "credentials.toml"),
             "MEDIA_CONFIG_FILE": str(home / "cfg" / "config.toml")},
    )
    assert res.returncode == 0
    parsed = json.loads(res.stdout)
    assert parsed["command"] == "doctor" and parsed["status"] == "warn"
    assert len(res.stdout.strip().splitlines()) == 1


# ---------------------------------------------------------------- credentials


def test_a_configured_binding_is_reported_by_source_never_by_value(home, monkeypatch, configured):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    configured({"openai/gpt-image-2": "env://OPENAI_API_KEY"})
    result = diagnose()
    entry = checks(result)["binding:openai/gpt-image-2"]
    assert entry["status"] == "ok"
    assert "env" in entry["detail"]
    assert SECRET not in json.dumps(result)


def test_a_binding_whose_credential_does_not_resolve_is_a_failure(home, monkeypatch, configured):
    """The one thing doctor exists to catch: configured, and still not callable."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    configured({"openai/gpt-image-2": "env://OPENAI_API_KEY"})
    entry = checks(diagnose())["binding:openai/gpt-image-2"]
    assert entry["status"] == "fail"
    assert "OPENAI_API_KEY" in entry["detail"]


def test_a_default_pointing_at_nothing_is_a_failure(home, configured):
    configured({}, defaults={"image.text_to_image": "openai/gpt-image-2"})
    assert checks(diagnose())["defaults"]["status"] == "fail"


def test_the_offline_backends_are_never_asked_for_a_key(home):
    entries = checks(diagnose())
    assert entries["binding:mock/mock"]["status"] == "ok"
    assert "no credential needed" in entries["binding:mock/mock"]["detail"]


def test_a_loose_credentials_file_is_a_failure(home):
    """The resolver refuses this file outright, so every key in it is already dead."""
    path = home / "cfg" / "credentials.toml"
    path.write_text('[openai]\napi_key = "x"\n', encoding="utf-8")
    path.chmod(0o644)
    result = diagnose()
    assert checks(result)["credentials-file"]["status"] == "fail"
    assert result["status"] == "fail"


class TestABrokenFileDoesNotTakeDownTheDiagnosis:
    """A hand-edited config with a typo in it is the most likely thing a user is
    holding when they reach for `doctor` — so it has to be reported, not raised."""

    def test_unparseable_credentials_are_a_finding(self, home):
        path = home / "cfg" / "credentials.toml"
        path.write_text("this is not toml =\n", encoding="utf-8")
        path.chmod(0o600)
        result = diagnose()
        assert result["status"] == "fail"
        assert any("not valid TOML" in c["detail"] for c in result["checks"])

    def test_the_other_checks_still_run(self, home):
        """The version, ffmpeg and file-mode lines are what explain the problem."""
        path = home / "cfg" / "credentials.toml"
        path.write_text("this is not toml =\n", encoding="utf-8")
        path.chmod(0o600)
        found = checks(diagnose())
        assert found["ffmpeg"]["status"] == "ok" and "media-ai" in found["version"]["detail"]

    def test_unparseable_config_is_a_finding_too(self, home):
        (home / "cfg" / "config.toml").write_text("[broken\n", encoding="utf-8")
        assert any("not valid TOML" in c["detail"] for c in diagnose()["checks"])

    def test_a_file_gets_one_entry_not_two(self, home):
        """`status` is what a script branches on, so an `ok` line followed by a `fail`
        line for the same path makes the obvious lookup contradict the report."""
        (home / "cfg" / "config.toml").write_text("[broken\n", encoding="utf-8")
        names = [c["check"] for c in diagnose()["checks"]]
        assert len(names) == len(set(names))
        assert checks(diagnose())["config"]["status"] == "fail"


def test_the_fail_verdict_stays_inside_the_encoding_it_chose(home, monkeypatch, capsys):
    """It names the mark it just printed; a hard-coded ✗ would both point at a glyph
    that was never drawn and raise on the stderr that made us degrade."""
    monkeypatch.setenv("MEDIA_ASCII", "1")
    path = home / "cfg" / "credentials.toml"
    path.write_text('[openai]\napi_key = "x"\n', encoding="utf-8")
    path.chmod(0o644)
    assert diagnose()["status"] == "fail"
    err = capsys.readouterr().err
    assert "✗" not in err and "FAIL lines above" in err


def test_marks_degrade_on_a_terminal_that_cannot_encode_them(home, monkeypatch, capsys):
    """`doctor` is most useful on a constrained box — which is exactly where stderr
    may not encode ✓, and where raising would take down the diagnosis."""
    monkeypatch.setenv("MEDIA_ASCII", "1")
    diagnose()
    err = capsys.readouterr().err
    assert "✓" not in err and "ok  " in err


def test_a_correct_credentials_file_passes(home):
    path = home / "cfg" / "credentials.toml"
    path.write_text('[openai]\napi_key = "x"\n', encoding="utf-8")
    path.chmod(0o600)
    assert checks(diagnose())["credentials-file"]["status"] == "ok"


# --------------------------------------------------------------------- skills


def test_no_skills_installed_is_a_warning_with_the_fix(home):
    assert "media-ai init" in checks(diagnose())["skills"]["detail"]


def test_installed_skills_are_counted(home):
    dest = home / "fakehome" / ".claude" / "skills"
    copy_skill("media-ai-shared", dest)
    found = checks(diagnose())["skills"]
    assert found["status"] == "ok" and "1 skill" in found["detail"]


def test_a_stale_copy_is_flagged(home):
    """Skills are copied, not linked, so a CLI upgrade leaves yesterday's instructions
    in the agent's directory. Nothing else reports that."""
    dest = home / "fakehome" / ".claude" / "skills"
    copy_skill("media-ai-shared", dest)
    (dest / "media-ai-shared" / "SKILL.md").write_text("from an older version", encoding="utf-8")
    found = checks(diagnose())["skills"]
    assert found["status"] == "warn"
    assert "media-ai-shared" in found["detail"] and "media-ai init" in found["detail"]


def test_a_skill_this_version_does_not_ship_is_a_warning(home):
    """The agent is reading instructions for a CLI that is no longer installed;
    reporting "everything checks out" over that is the wrong answer."""
    dest = home / "fakehome" / ".claude" / "skills"
    copy_skill("media-ai-shared", dest)
    (dest / "media-ai-from-the-future").mkdir()
    (dest / "media-ai-from-the-future" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    result = diagnose()
    assert checks(result)["skills"]["status"] == "warn"
    assert "media-ai-from-the-future" in checks(result)["skills"]["detail"]


def test_a_dangling_symlink_is_not_reported_as_up_to_date(home, tmp_path):
    dest = home / "fakehome" / ".claude" / "skills"
    dest.mkdir(parents=True)
    (dest / "media-ai-shared").symlink_to(tmp_path / "nowhere")
    assert checks(diagnose())["skills"]["status"] == "warn"


def test_a_symlink_to_the_packaged_tree_is_reported_stale(home):
    """The inverse of what this used to assert, and deliberately so: the packaged tree
    is a template now, so a link to it hands the agent an unrendered `{{cli}}`. `doctor`
    has to say that out loud — blessing it would be blessing a skill whose every command
    is wrong."""
    from media_ai.cli._discovery import skill_root

    dest = home / "fakehome" / ".claude" / "skills"
    dest.mkdir(parents=True)
    (dest / "media-ai-shared").symlink_to(str(skill_root("media-ai-shared")))
    assert checks(diagnose())["skills"]["status"] == "warn"


def test_doctor_is_a_registered_group():
    from media_ai.__main__ import _GROUPS

    assert "doctor" in _GROUPS


# ------------------------------------------------------------------- offline


def test_no_check_opens_a_socket(home, monkeypatch):
    """`doctor` is the diagnosis you can run on a plane; verification costs a request
    and lives behind `init --verify`."""
    import socket

    def refuse(*a, **kw):
        raise AssertionError("doctor made a network call")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert diagnose()["ok"] is True
