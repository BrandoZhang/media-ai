"""``release-feed.json`` — the one document this project publishes *at* its users.

Everything else here is read by a build that shipped with it. The feed is the opposite:
**its readers are always older than it is.** A client fetches it precisely because it
does not know what has happened since it was built, and it can never be upgraded to
understand a change — it is already installed, on someone else's machine, possibly for
years.

That inverts the usual rules, and the tests below are those rules written down:

- **The shape only ever grows.** A new key is invisible to an old reader; a renamed or
  re-meaninged one is a lie to every reader already out there. `schema` exists so a
  client can *stop reading* rather than misread, and bumping it is abandoning everyone
  who has not upgraded — which is why it should stay 1.
- **Ranges are two explicit fields, never an expression.** A mini-language for "which
  versions does this apply to" would be parsed by the old clients, and one that
  misparses does not fail loudly: it applies a notice, or a block, to the wrong people.
- **It says only what has shipped.** `latest.version` has to be true the moment the tag
  is, so it is bumped in the same pull request as `__version__` rather than committed
  afterwards by the release job.

Nothing reads this file yet. That is deliberate — the shape and the URL are the
irreversible parts, so they land, and get reviewed, before there is a client whose
behaviour depends on them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import media_ai
from media_ai.core.versioning import VERSION, precedence

ROOT = Path(__file__).resolve().parent.parent
FEED_PATH = ROOT / "release-feed.json"

#: Every key this schema defines, at each level. A published document cannot have
#: stray keys in it: a typo'd `min_supported` is not a syntax error, it is a floor that
#: silently does nothing.
TOP_LEVEL = {"schema", "latest", "min_supported", "notices", "retired_bindings"}
LATEST = {"version", "url", "prerelease"}
NOTICE = {"id", "severity", "title", "body", "min_version", "max_version"}
RETIRED = {"binding", "since", "severity", "reason", "alternatives", "fixed_in"}

pytestmark = pytest.mark.skipif(
    not FEED_PATH.is_file(), reason="running against an installed package, not a checkout"
)


@pytest.fixture
def feed() -> dict:
    return json.loads(FEED_PATH.read_text(encoding="utf-8"))


def versions_in(feed: dict) -> list[tuple[str, str]]:
    """Every ``(where, value)`` in the document that has to be a version number."""
    out = [("latest.version", feed["latest"]["version"])]
    if "min_supported" in feed:
        out.append(("min_supported", feed["min_supported"]))
    for i, notice in enumerate(feed.get("notices", [])):
        out += [(f"notices[{i}].{k}", notice[k]) for k in ("min_version", "max_version") if k in notice]
    for i, entry in enumerate(feed.get("retired_bindings", [])):
        out += [(f"retired_bindings[{i}].fixed_in", entry[k]) for k in ("fixed_in",) if k in entry]
    return out


# ------------------------------------------------------------------ the shape


def test_the_schema_is_one(feed):
    """Bumping it abandons every client that has not upgraded — which is all of them.

    A client that meets a schema it does not know must ignore the whole document, so a
    bump is not a migration, it is a switch-off. The way out of a bad field is to add a
    better one beside it and leave the old one alone.
    """
    assert feed["schema"] == 1


def test_only_declared_keys_appear(feed):
    """A typo'd key is not a syntax error here, it is a policy that silently does nothing."""
    assert set(feed) <= TOP_LEVEL, f"undeclared top-level key(s): {set(feed) - TOP_LEVEL}"
    assert set(feed["latest"]) <= LATEST
    for notice in feed.get("notices", []):
        assert set(notice) <= NOTICE
    for entry in feed.get("retired_bindings", []):
        assert set(entry) <= RETIRED


def test_applicability_is_two_fields_and_never_an_expression(feed):
    """`min_version`/`max_version`, not `"<0.6.0"`.

    A range expression is a language parsed by the *old* clients, and one that
    misparses does not fail loudly — it applies a notice, or a block, to the wrong
    people. Two plain comparisons cannot go subtly wrong.
    """
    for _where, value in versions_in(feed):
        assert VERSION.match(value), f"{value!r} is not a plain version"


@pytest.mark.parametrize("key", ["notices", "retired_bindings"])
def test_the_lists_are_present_even_when_empty(feed, key):
    """This file is hand-edited for policy, so the empty lists are the documentation
    of where policy goes. A reader must treat absent as empty regardless."""
    assert isinstance(feed[key], list)


def test_every_notice_can_be_shown_without_being_understood(feed):
    """Notice text is display-only: an id to deduplicate on, a severity, and prose."""
    for notice in feed["notices"]:
        assert notice["id"] and isinstance(notice["id"], str)
        assert notice["severity"] in {"info", "warn"}
        assert notice["title"] and notice["body"]


def test_every_retirement_names_the_binding_and_a_way_forward(feed):
    """A block with no alternative is a dead end handed to an agent mid-task."""
    for entry in feed["retired_bindings"]:
        assert "/" in entry["binding"], "a retirement names a binding, not a provider"
        assert entry["severity"] in {"warn", "block"}
        assert entry["reason"]
        assert entry.get("alternatives") or entry.get("fixed_in"), f"{entry['binding']} offers no way forward"


# ------------------------------------------------- it says only what has shipped


def test_latest_is_the_version_in_this_tree(feed):
    """It has to become true at the moment the tag does, so it moves in the same
    pull request as `__version__` rather than being committed afterwards by the job."""
    assert feed["latest"]["version"] == media_ai.__version__


def test_the_release_link_names_that_version(feed):
    assert feed["latest"]["url"].endswith(f"/v{media_ai.__version__}")


def test_prerelease_agrees_with_how_the_release_is_published(feed):
    """`release.yml` publishes a 0.x line as a pre-release; the feed must not disagree."""
    assert feed["latest"]["prerelease"] is media_ai.__version__.startswith("0.")


def test_a_floor_is_never_ahead_of_what_has_shipped(feed):
    """`min_supported` above `latest` would refuse every client including up-to-date ones.

    Absent is the ordinary state and means no floor — the "absent limit is an absent
    field" rule, which matters here because a sentinel `0.0.0` invites a reader to
    compare against it and a typo'd one to lock everybody out.
    """
    if "min_supported" in feed:
        assert precedence(feed["min_supported"]) <= precedence(feed["latest"]["version"])


# ------------------------------------------------------------- how it is written


def test_the_file_is_stored_exactly_as_the_writer_produces_it():
    """So a bump never lands a formatting diff on top of the version it came for.

    `bump_version.py` rewrites the whole document, and a hand-edit in another style
    would show up as unrelated churn in the release pull request — or, worse, get
    reformatted by it and hide the real change.
    """
    data = json.loads(FEED_PATH.read_text(encoding="utf-8"))
    assert FEED_PATH.read_text(encoding="utf-8") == json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def test_the_bump_moves_the_feed_with_the_version(tmp_path, monkeypatch):
    from test_version import load_script

    bump_version = load_script("bump_version")
    feed = tmp_path / "release-feed.json"
    feed.write_text(FEED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(bump_version, "FEED", feed)

    assert bump_version._replace_feed("9.8.7")
    written = json.loads(feed.read_text(encoding="utf-8"))
    assert written["latest"] == {
        "version": "9.8.7",
        "url": "https://github.com/BrandoZhang/media-ai/releases/tag/v9.8.7",
        "prerelease": False,
    }


def test_the_bump_leaves_the_policy_fields_alone(tmp_path, monkeypatch):
    """Everything but `latest` is written by a human and reviewed as a change of its own."""
    from test_version import load_script

    bump_version = load_script("bump_version")
    feed = tmp_path / "release-feed.json"
    policy = {
        "schema": 1,
        "latest": {"version": "0.1.0", "url": "…", "prerelease": True},
        "min_supported": "0.1.0",
        "notices": [{"id": "x", "severity": "info", "title": "t", "body": "b"}],
        "retired_bindings": [{"binding": "p/m", "since": "2026-09-01", "severity": "block",
                              "reason": "gone", "alternatives": ["p/m2"]}],
    }
    feed.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(bump_version, "FEED", feed)

    bump_version._replace_feed("9.8.7")
    written = json.loads(feed.read_text(encoding="utf-8"))
    assert {k: v for k, v in written.items() if k != "latest"} == {
        k: v for k, v in policy.items() if k != "latest"
    }


def test_the_bump_is_idempotent(tmp_path, monkeypatch):
    """Re-running a release must not produce a diff; nothing in here is a timestamp."""
    from test_version import load_script

    bump_version = load_script("bump_version")
    feed = tmp_path / "release-feed.json"
    feed.write_text(FEED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(bump_version, "FEED", feed)

    bump_version._replace_feed("9.8.7")
    assert bump_version._replace_feed("9.8.7") is False


def test_a_feed_whose_shape_moved_fails_the_bump_rather_than_writing_nonsense(tmp_path, monkeypatch):
    from test_version import load_script

    bump_version = load_script("bump_version")
    feed = tmp_path / "release-feed.json"
    feed.write_text('{"schema": 1}\n', encoding="utf-8")
    monkeypatch.setattr(bump_version, "FEED", feed)

    with pytest.raises(SystemExit, match="latest.version"):
        bump_version._replace_feed("9.8.7")
