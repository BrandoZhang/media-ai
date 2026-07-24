"""Guards for the packaged Agent Skills locator (``media_ai.agent_skills_dir``).

Embedding frameworks (e.g. uni-agent) call this to point their skill loader at
media-ai's skills without vendoring a copy, so the resolution contract matters —
including that it survives the ``src/`` package layout.
"""

from __future__ import annotations

import pytest

import media_ai
from media_ai.agent_skills import agent_skills_dir

# Every skill folder the CLI ships; the loader must be able to find them all.
EXPECTED_SKILLS = {
    "media-ai-shared",
    "media-ai-image",
    "media-ai-video",
    "media-ai-speech",
    "media-ai-music",
    "media-ai-sound",
    "media-ai-job",
    "media-ai-concat",
    "media-ai-capabilities",
    "media-ai-usage",
}


def test_agent_skills_dir_is_exported():
    assert media_ai.agent_skills_dir is agent_skills_dir


def test_agent_skills_dir_resolves_to_the_skill_folders():
    root = agent_skills_dir()
    assert root.is_dir()
    assert (root / "media-ai-shared" / "SKILL.md").is_file()
    found = {p.parent.name for p in root.glob("*/SKILL.md")}
    assert EXPECTED_SKILLS <= found, f"missing skills: {EXPECTED_SKILLS - found}"


def test_env_override_wins(tmp_path, monkeypatch):
    fake = tmp_path / "skills"
    (fake / "media-ai-shared").mkdir(parents=True)
    (fake / "media-ai-shared" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    monkeypatch.setenv("MEDIA_AI_SKILLS_DIR", str(fake))
    assert agent_skills_dir() == fake


def test_env_override_must_be_a_real_skills_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_AI_SKILLS_DIR", str(tmp_path))  # no media-ai-shared/SKILL.md
    with pytest.raises(FileNotFoundError):
        agent_skills_dir()
