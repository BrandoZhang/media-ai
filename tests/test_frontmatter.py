"""The SKILL.md frontmatter reader.

It is a YAML *subset*, so these pin both halves of the bargain: the shapes the
packaged skills use must parse exactly, and everything outside the subset must
degrade quietly rather than raise — a malformed skill should cost the installer that
skill's description, not the run.
"""

from __future__ import annotations

import pytest

from media_ai.cli._frontmatter import parse

SKILL = """---
name: media-ai-demo
description: >-
  A folded description that runs
  across several lines.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai demo --help"
  install:
    tier: optional
    needs: ["media-ai-job"]
    summary: >-
      What this is for.
---

# Body

Not frontmatter.
"""


@pytest.fixture(scope="module")
def parsed():
    return parse(SKILL)


# --------------------------------------------------------------------- shapes


def test_top_level_scalars(parsed):
    assert parsed["name"] == "media-ai-demo"
    assert parsed["version"] == "1.0.0"


def test_folded_block_scalar_is_joined_with_spaces(parsed):
    assert parsed["description"] == "A folded description that runs across several lines."


def test_nested_mapping(parsed):
    assert parsed["metadata"]["install"]["tier"] == "optional"


def test_flow_sequence(parsed):
    assert parsed["metadata"]["requires"]["bins"] == ["media-ai"]
    assert parsed["metadata"]["install"]["needs"] == ["media-ai-job"]


def test_quoted_scalar_loses_its_quotes(parsed):
    assert parsed["metadata"]["cliHelp"] == "media-ai demo --help"


def test_body_after_the_closing_fence_is_ignored(parsed):
    assert "Body" not in str(parsed)


def test_literal_block_scalar_keeps_line_breaks():
    out = parse("---\nnote: |\n  first\n  second\n---\n")
    assert out["note"] == "first\nsecond"


def test_block_sequence():
    out = parse("---\nneeds:\n  - a\n  - b\n---\n")
    assert out["needs"] == ["a", "b"]


def test_block_sequence_at_the_parent_indent():
    """Legal YAML, and the shape an editor's autoformat tends to produce."""
    out = parse("---\nneeds:\n- a\n- b\n---\n")
    assert out["needs"] == ["a", "b"]


def test_empty_flow_sequence():
    assert parse("---\nneeds: []\n---\n")["needs"] == []


def test_booleans_and_null():
    out = parse("---\nyes_: true\nno_: FALSE\nnothing: null\n---\n")
    assert out["yes_"] is True and out["no_"] is False and out["nothing"] is None


def test_numbers_stay_strings():
    """`version: 1.0.0` is a string, and so is every model id in these files."""
    assert parse("---\nversion: 2\n---\n")["version"] == "2"


def test_value_containing_a_colon_survives():
    out = parse('---\nurl: "https://example.com/x"\n---\n')
    assert out["url"] == "https://example.com/x"


def test_trailing_comment_is_dropped():
    assert parse("---\ntier: core  # always\n---\n")["tier"] == "core"


def test_comment_lines_are_ignored():
    out = parse("---\n# leading note\ntier: core\n---\n")
    assert out == {"tier": "core"}


# ------------------------------------------------------------------ degrading


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no frontmatter at all\n",
        "---\nname: x\n",  # unterminated: no closing fence
        "---\n---\n",  # empty
        "not a fence\n---\nname: x\n---\n",  # fence must be the first line
    ],
)
def test_missing_or_broken_frontmatter_is_empty_not_an_error(text):
    assert parse(text) == {}


def test_a_line_that_is_not_a_mapping_entry_is_skipped():
    out = parse("---\nname: x\nthis line has no colon\nversion: 1\n---\n")
    assert out == {"name": "x", "version": "1"}


def test_key_with_no_value_and_nothing_under_it():
    assert parse("---\nmetadata:\nname: x\n---\n") == {"metadata": None, "name": "x"}


def test_flow_sequence_with_a_quoted_comma():
    assert parse('---\nxs: ["a,b", c]\n---\n')["xs"] == ["a,b", "c"]


def test_deeply_nested_mappings_round_trip():
    out = parse("---\na:\n  b:\n    c:\n      d: deep\n---\n")
    assert out["a"]["b"]["c"]["d"] == "deep"


# ------------------------------------------------------------ the real thing


def test_every_packaged_skill_parses():
    """Rendered first, as `init` writes it — the packaged file is a template, and its
    `name:` is `{{skill}}image` until the brand is substituted in."""
    from media_ai.cli._discovery import available_skills, skill_root
    from media_ai.cli._render import render

    for skill in available_skills():
        front = parse(render((skill_root(skill) / "SKILL.md").read_text(encoding="utf-8")))
        assert front.get("name") == skill, f"{skill}: frontmatter name does not match its directory"
        assert isinstance(front.get("description"), str) and front["description"]
        assert isinstance(front.get("metadata"), dict)
