"""A binding on its way out, as declared by the manifest that ships with the build.

The feed covers the model withdrawn *after* a build was packaged (`test_feed_blocking`).
This covers the other half: one that was already deprecated when the manifest was
written. Both are the same condition and carry the same notice `kind` — a consumer
branches on the kind, not on which document knew first.

Two behaviours, and they are separate on purpose:

- **It is not recommended while anything else can be.** A hint is read as an
  instruction, so pointing at something being retired is pointing the wrong way.
- **It is still suggested when it is all there is**, and it still runs. Suppressing the
  only binding that serves a scene would turn "use this instead" into "nothing serves
  this scene", which is false in the direction that stops the work.
"""

from __future__ import annotations

import json
import sys

import pytest

from media_ai import register_manifest
from media_ai.core import notices
from media_ai.core.binding import Lifecycle
from media_ai.core.config import Config, UserBinding, render_config
from media_ai.core.registry import catalog
from media_ai.core.resolve import available_bindings, resolve
from media_ai.core.scene import Scene

# Two bindings on one throwaway provider, sharing the mock adapter so they can actually
# run: one deprecated, one not, which is the pair every question below is about.
MANIFEST = """
[provider]
name      = "sunset"
title     = "Sunset (test)"
transport = "local"
adapter   = "media_ai.providers.mock:MockAdapter"

[provider.auth]
kind = "none"

[[binding]]
id          = "sunset/old"
model       = "old"
lifecycle   = "deprecated"
replacement = "sunset/new"
scenes      = ["image.text_to_image"]

[[binding]]
id     = "sunset/new"
model  = "new"
scenes = ["image.text_to_image"]

# The other case: a scene whose only non-placeholder candidate is on its way out.
[[binding]]
id          = "sunset/lonely"
model       = "lonely"
lifecycle   = "deprecated"
replacement = "sunset/new"
scenes      = ["music.text_to_music"]
"""


@pytest.fixture
def sunset(clean_registry, tmp_path, monkeypatch):
    """Both bindings registered and configured, with a config this test owns."""
    register_manifest(MANIFEST, source="test_deprecation")
    path = tmp_path / "config.toml"
    path.write_text(
        render_config(Config(bindings={
            "sunset/old": UserBinding(id="sunset/old"),
            "sunset/new": UserBinding(id="sunset/new"),
        })),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIA_AI_CONFIG_FILE", str(path))
    notices.clear()
    yield
    notices.clear()


def config_with(*ids) -> Config:
    return Config(bindings={i: UserBinding(id=i) for i in ids}, exists=True)


def generate(*argv, expect=0, capsys) -> dict:
    from media_ai.cli import image as image_mod

    old, sys.argv = sys.argv, ["media-ai image", "generate", *argv]
    try:
        code = image_mod.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code == expect, f"{argv} -> {code}: {out}"
    return json.loads(out.strip().splitlines()[-1])


def notice_of(result, kind: str) -> dict | None:
    return next((n for n in result.get("notices", []) if n["kind"] == kind), None)


# ------------------------------------------------------------- what gets suggested


def hint_for(scene: Scene) -> str:
    """The hint from a call that named no binding, which is where suggestions live."""
    with pytest.raises(Exception) as exc:  # noqa: PT011 - MediaError, asserted by the caller
        resolve(binding=None, provider=None, model=None,
                scene=scene, catalog=catalog(), config=Config(exists=True))
    return exc.value.hint


def test_a_hint_passes_over_a_deprecated_binding(sunset):
    """The scene has two candidates; the one being retired is not the one to name."""
    hint = hint_for(Scene.IMAGE_TEXT_TO_IMAGE)
    assert "sunset/new" in hint
    assert "sunset/old" not in hint


def test_a_deprecated_binding_is_suggested_when_it_is_the_only_one(sunset):
    """Silence here would read as "nothing serves this", which is false."""
    assert "sunset/lonely" in hint_for(Scene.MUSIC_TEXT_TO_MUSIC)


def test_a_deprecated_binding_stays_in_the_listings(sunset):
    """Dropping it from `available` would be a different lie: it is genuinely there."""
    ids = {b.id for b in available_bindings(catalog(), config_with("sunset/old", "sunset/new"))}
    assert {"sunset/old", "sunset/new"} <= ids


def test_a_placeholder_is_still_dropped_outright(sunset):
    """The two exclusions are not one rule. A fabricated result is worse than no answer,
    so `mock/mock` is never named even when it is the only candidate — unlike a
    deprecated binding, which is named exactly then.
    """
    assert "mock/mock" not in hint_for(Scene.SPEECH_TEXT_TO_SPEECH)


# --------------------------------------------------------------- the notice


def test_using_a_deprecated_binding_says_so(sunset, tmp_path, capsys):
    """The caller who passed no flags and got the scene default is the one nobody told.

    `capabilities` has reported `lifecycle` all along and `init` labels the row; neither
    is on the path of an ordinary generation.
    """
    result = generate("--binding", "sunset/old", "--prompt", "x",
                      "--output", str(tmp_path / "x.png"), capsys=capsys)
    note = notice_of(result, "binding_deprecated")
    assert note is not None
    assert "sunset/new" in note["message"]
    assert note["severity"] == "info"


def test_the_call_still_runs(sunset, tmp_path, capsys):
    """Deprecated is not retired. The notice is the whole of the consequence."""
    out = tmp_path / "x.png"
    result = generate("--binding", "sunset/old", "--prompt", "x", "--output", str(out), capsys=capsys)
    assert result["ok"] is True and out.exists()


def test_a_current_binding_says_nothing(sunset, tmp_path, capsys):
    result = generate("--binding", "sunset/new", "--prompt", "x",
                      "--output", str(tmp_path / "x.png"), capsys=capsys)
    assert notice_of(result, "binding_deprecated") is None


def test_the_action_is_a_runnable_command(sunset, tmp_path, capsys):
    """`action` is documented as runnable verbatim, and the skills tell agents so —
    "re-run with --binding X" is a fine *hint* and not a command.
    """
    result = generate("--binding", "sunset/old", "--prompt", "x",
                      "--output", str(tmp_path / "x.png"), capsys=capsys)
    assert notice_of(result, "binding_deprecated")["action"].split()[1:] == [
        "capabilities", "--binding", "sunset/new",
    ]


def test_the_feed_wins_when_both_have_something_to_say(sunset, tmp_path, monkeypatch, capsys):
    """One line per call. The feed is the more current of the two documents, so its
    wording is the one that survives — and the manifest does not repeat it.
    """
    import time

    monkeypatch.setenv("MEDIA_AI_CONFIG_FILE", str(tmp_path / "config.toml"))
    (tmp_path / "update-cache.json").write_text(json.dumps({
        "checked_at": time.time(),
        "feed": {"schema": 1, "notices": [], "retired_bindings": [{
            "binding": "sunset/old", "severity": "warn",
            "reason": "the endpoint is gone", "alternatives": ["sunset/new"],
        }]},
    }), encoding="utf-8")

    result = generate("--binding", "sunset/old", "--prompt", "x",
                      "--output", str(tmp_path / "x.png"), capsys=capsys)
    found = [n for n in result["notices"] if n["kind"] == "binding_deprecated"]
    assert len(found) == 1
    assert "the endpoint is gone" in found[0]["message"]


# ------------------------------------------------------- the declaration itself


def test_deprecated_without_a_replacement_is_refused(clean_registry):
    """The notice always has somewhere to point because the parser guarantees it."""
    from media_ai.core.binding import ManifestError

    with pytest.raises(ManifestError, match="replacement"):
        register_manifest(
            MANIFEST.replace('replacement = "sunset/new"', ""), source="test_deprecation_bad"
        )


def test_the_lifecycle_reaches_discovery(sunset):
    """`capabilities` is where a machine asks, and it reads the same declaration."""
    spec = catalog().get("sunset/old")
    assert spec.lifecycle is Lifecycle.DEPRECATED
    assert spec.to_dict()["replacement"] == "sunset/new"
