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
    assert parsed["operation"] == "doctor" and parsed["status"] == "warn"
    assert len(res.stdout.strip().splitlines()) == 1


# ---------------------------------------------------------------- credentials


def test_a_configured_provider_is_reported_by_source_never_by_value(home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    result = diagnose()
    assert checks(result)["credential:openai"]["status"] == "ok"
    assert "OPENAI_API_KEY" in checks(result)["credential:openai"]["detail"]
    assert SECRET not in json.dumps(result)


def test_the_offline_provider_is_never_asked_for_a_key(home):
    assert "credential:mock" not in checks(diagnose())


def test_a_loose_credentials_file_is_a_failure(home):
    """The resolver refuses this file outright, so every key in it is already dead."""
    path = home / "cfg" / "credentials.toml"
    path.write_text('[openai]\napi_key = "x"\n', encoding="utf-8")
    path.chmod(0o644)
    result = diagnose()
    assert checks(result)["credentials-file"]["status"] == "fail"
    assert result["status"] == "fail"


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


def test_a_symlinked_skill_can_never_be_stale(home):
    from media_ai.cli._discovery import skill_root

    dest = home / "fakehome" / ".claude" / "skills"
    dest.mkdir(parents=True)
    (dest / "media-ai-shared").symlink_to(str(skill_root("media-ai-shared")))
    assert checks(diagnose())["skills"]["status"] == "ok"


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
