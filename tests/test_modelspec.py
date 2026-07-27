"""The declarative model catalogue.

Two halves: the generic resolution/lifecycle machinery, and invariants every built-in
provider's catalogue must satisfy. The second half is the point — it makes a
mis-edited catalogue fail here rather than at a provider call.
"""

from __future__ import annotations

import json

import pytest

from media_ai.core import registry
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.modelspec import Catalog, ModelSpec, ModelStatus, apply_spec
from media_ai.providers._catalog import CATALOGS

# --------------------------------------------------------------- resolution


@pytest.fixture
def cat():
    return Catalog(
        "demo",
        (
            ModelSpec(id="new-1.0", notes=("shiny",)),
            ModelSpec(id="old-1.0", status=ModelStatus.DEPRECATED, replacement="new-1.0",
                      reason="superseded", discoverable=False),
            ModelSpec(id="gone-1.0", status=ModelStatus.REMOVED, replacement="new-1.0",
                      reason="withdrawn", matches=("gone",), discoverable=False),
            ModelSpec(id="preview-1.0", status=ModelStatus.PREVIEW),
            ModelSpec(id="fallback", synthetic=True, matches=("demo-",), discoverable=False),
        ),
        fallback="fallback",
    )


def test_exact_id_wins(cat):
    assert cat.get("new-1.0").id == "new-1.0"


def test_lookup_is_case_insensitive(cat):
    assert cat.get("NEW-1.0").id == "new-1.0"


def test_prefix_match_when_no_exact_id(cat):
    assert cat.get("gone-1.0-20260101").id == "gone-1.0"


def test_unknown_id_lands_on_the_declared_fallback(cat):
    assert cat.get("demo-something-unheard-of").id == "fallback"


def test_no_fallback_means_unknown_resolves_to_none():
    bare = Catalog("bare", (ModelSpec(id="only"),))
    assert bare.get("mystery") is None


def test_aliases_resolve_to_the_same_spec():
    c = Catalog("a", (ModelSpec(id="canonical", aliases=("dated-20260101",)),))
    assert c.get("dated-20260101").id == "canonical"


def test_duplicate_ids_are_rejected_at_construction():
    with pytest.raises(ValueError, match="duplicate"):
        Catalog("dup", (ModelSpec(id="x"), ModelSpec(id="X")))


def test_alias_colliding_with_an_id_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        Catalog("dup", (ModelSpec(id="x"), ModelSpec(id="y", aliases=("x",))))


def test_unknown_fallback_is_rejected_at_construction():
    with pytest.raises(ValueError, match="fallback"):
        Catalog("bad", (ModelSpec(id="x"),), fallback="nope")


# ---------------------------------------------------------------- lifecycle


def test_require_returns_a_usable_model(cat):
    assert cat.require("new-1.0").id == "new-1.0"


def test_require_allows_a_deprecated_model(cat):
    """Deprecated still works — that is the difference from removed."""
    assert cat.require("old-1.0").status is ModelStatus.DEPRECATED


def test_require_refuses_a_removed_model(cat):
    with pytest.raises(MediaError) as ei:
        cat.require("gone-1.0")
    assert ei.value.category == ErrorCategory.UNSUPPORTED


def test_removal_error_names_the_replacement_and_reason(cat):
    with pytest.raises(MediaError) as ei:
        cat.require("gone-1.0")
    assert "new-1.0" in str(ei.value) and "withdrawn" in str(ei.value)


def test_require_on_an_unknown_id_without_fallback_is_not_found():
    bare = Catalog("bare", (ModelSpec(id="only"),))
    with pytest.raises(MediaError) as ei:
        bare.require("mystery")
    assert ei.value.category == ErrorCategory.NOT_FOUND


# ----------------------------------------------------------------- listings


def test_discoverable_excludes_withheld_removed_and_synthetic(cat):
    assert cat.discoverable_ids() == ["new-1.0", "preview-1.0"]


def test_real_ids_include_retired_but_not_synthetic(cat):
    ids = cat.real_ids()
    assert "gone-1.0" in ids and "old-1.0" in ids
    assert "fallback" not in ids


def test_status_query(cat):
    assert cat.ids_with_status(ModelStatus.DEPRECATED) == ["old-1.0"]
    assert cat.ids_with_status(ModelStatus.REMOVED) == ["gone-1.0"]


def test_unverified_lists_usable_models_without_a_date(cat):
    assert "gone-1.0" not in cat.unverified_ids()  # removed, not worth verifying
    assert "new-1.0" in cat.unverified_ids()


def test_verified_model_drops_out_of_unverified():
    c = Catalog("v", (ModelSpec(id="checked", verified="2026-07-27"), ModelSpec(id="not-checked")))
    assert c.unverified_ids() == ["not-checked"]


# -------------------------------------------------------------- apply_spec


class _Caps:
    def __init__(self):
        self.notes, self.aliases, self.experimental = (), (), False
        self.status, self.replacement, self.verified = "ga", None, None


def test_apply_spec_stamps_lifecycle():
    caps = apply_spec(_Caps(), ModelSpec(id="x", status=ModelStatus.DEPRECATED, replacement="y"))
    assert caps.status == "deprecated" and caps.replacement == "y"


def test_preview_sets_the_experimental_flag():
    """Kept in sync with the pre-existing `experimental` field rather than replacing it."""
    assert apply_spec(_Caps(), ModelSpec(id="x", status=ModelStatus.PREVIEW)).experimental is True


def test_apply_spec_records_missing_verification_explicitly():
    notes = apply_spec(_Caps(), ModelSpec(id="x")).notes
    assert any("not verified" in n for n in notes)


def test_apply_spec_reports_a_recorded_verification_date():
    notes = apply_spec(_Caps(), ModelSpec(id="x", verified="2026-07-27")).notes
    assert any("2026-07-27" in n for n in notes)


def test_apply_spec_on_none_is_a_no_op():
    caps = _Caps()
    assert apply_spec(caps, None) is caps


# ------------------------------------------------- built-in catalogue rules


ALL_SPECS = [(name, spec) for name, cat in CATALOGS.items() for spec in cat.specs]
IDS = [f"{n}:{s.id}" for n, s in ALL_SPECS]


@pytest.mark.parametrize("provider,spec", ALL_SPECS, ids=IDS)
def test_retired_models_name_a_replacement(provider, spec):
    """A retirement without somewhere to go is a dead end for the caller."""
    if spec.status in (ModelStatus.DEPRECATED, ModelStatus.REMOVED):
        assert spec.replacement, f"{provider}:{spec.id} is {spec.status.value} with no replacement"


@pytest.mark.parametrize("provider,spec", ALL_SPECS, ids=IDS)
def test_removed_models_are_not_discoverable(provider, spec):
    if spec.status is ModelStatus.REMOVED:
        assert not spec.discoverable


@pytest.mark.parametrize("provider,spec", ALL_SPECS, ids=IDS)
def test_synthetic_specs_are_never_discoverable(provider, spec):
    if spec.synthetic:
        assert not spec.discoverable


@pytest.mark.parametrize("name", list(CATALOGS))
def test_discovery_matches_the_catalogue(name):
    """models() and the catalogue must not drift apart."""
    assert registry.get_provider(name).models() == CATALOGS[name].discoverable_ids()


@pytest.mark.parametrize("name", list(CATALOGS))
def test_every_discoverable_model_describes_itself(name):
    prov = registry.get_provider(name)
    for model in prov.models():
        caps = prov.capabilities(model)
        assert caps.model == model
        json.dumps(caps.to_dict())


@pytest.mark.parametrize("name", list(CATALOGS))
def test_removed_models_refuse_to_describe_themselves(name):
    prov = registry.get_provider(name)
    for model in CATALOGS[name].ids_with_status(ModelStatus.REMOVED):
        with pytest.raises(MediaError):
            prov.capabilities(model)


@pytest.mark.parametrize("name", list(CATALOGS))
def test_deprecated_models_still_describe_themselves(name):
    """Deprecated is a warning, not a wall — a migration needs to inspect them."""
    prov = registry.get_provider(name)
    for model in CATALOGS[name].ids_with_status(ModelStatus.DEPRECATED):
        caps = prov.capabilities(model)
        assert caps.status == "deprecated" and caps.replacement


@pytest.mark.parametrize("name", list(CATALOGS))
def test_all_models_is_a_superset_of_models(name):
    prov = registry.get_provider(name)
    assert set(prov.models()) <= set(prov.all_models())


@pytest.mark.parametrize("name", list(CATALOGS))
def test_all_models_excludes_synthetic_fallbacks(name):
    synthetic = {s.id for s in CATALOGS[name].specs if s.synthetic}
    assert not (set(registry.get_provider(name).all_models()) & synthetic)


def test_verification_state_is_reported_not_assumed():
    """Nothing claims a live-API check it has not had."""
    for name, cat in CATALOGS.items():
        for spec in cat.specs:
            assert spec.verified is None or len(spec.verified) == 10, \
                f"{name}:{spec.id} has a malformed verified date {spec.verified!r}"


# ------------------------------------------------- substring family matching


def test_contains_claims_a_mid_id_marker():
    """A vendor family is not always a prefix — Google's TTS ids end in `-tts`."""
    c = Catalog(
        "g",
        (ModelSpec(id="tts-fallback", synthetic=True, contains=("tts",), discoverable=False),
         ModelSpec(id="image-fallback", synthetic=True, matches=("g-",), discoverable=False)),
        fallback="image-fallback",
    )
    assert c.get("g-2.5-flash-preview-tts").id == "tts-fallback"
    assert c.get("g-4-image").id == "image-fallback"


def test_narrower_spec_declared_first_wins():
    c = Catalog(
        "g",
        (ModelSpec(id="narrow", contains=("tts",)), ModelSpec(id="broad", matches=("g-",))),
    )
    assert c.get("g-x-tts").id == "narrow"


@pytest.mark.parametrize(
    "model",
    ["gemini-3.1-flash-tts", "gemini-9-ultra-tts-preview", "gemini-4-flash-tts-preview"],
)
def test_unrecognised_gemini_tts_ids_resolve_as_speech(model):
    """Regression: a prefix-only `gemini-` image fallback swallowed every one of these,
    so `speech generate` on an unreleased TTS id failed with 'does not support speech'."""
    from media_ai.providers._catalog import GEMINI

    assert GEMINI.get(model).caps.get("kind") == "tts"


def test_unrecognised_gemini_image_ids_still_resolve_as_images():
    from media_ai.providers._catalog import GEMINI

    assert GEMINI.get("gemini-9-flash-image").caps.get("kind") is None


def test_unknown_tts_model_reports_speech_operations():
    from media_ai.core.types import Operation as Op

    caps = registry.get_provider("gemini").capabilities("gemini-9-ultra-tts-preview")
    assert caps.audio is not None
    assert Op.SPEECH_GENERATE in caps.audio.operations


# ------------------------------------------------------ all_models superset


@pytest.mark.parametrize("name", list(CATALOGS))
def test_all_models_is_a_superset_even_when_discovery_is_configured(name, monkeypatch):
    """Volc discovers from config/env, not the catalogue; returning catalogue ids alone
    would drop the endpoints the account can actually call."""
    monkeypatch.setenv("ARK_IMAGE_MODEL", "ep-configured-image")
    monkeypatch.setenv("ARK_VIDEO_MODEL", "ep-configured-video")
    prov = registry.get_provider(name)
    assert set(prov.models()) <= set(prov.all_models())


def test_configured_volc_endpoints_appear_in_all_models(monkeypatch):
    monkeypatch.setenv("ARK_IMAGE_MODEL", "ep-configured-image")
    listed = registry.get_provider("volc").all_models()
    assert "ep-configured-image" in listed


def test_all_models_has_no_duplicates():
    for name in CATALOGS:
        ids = registry.get_provider(name).all_models()
        assert len(ids) == len(set(ids))


# ------------------------------------------------- capabilities CLI surface


def _run_cli(*argv):
    import subprocess
    import sys

    res = subprocess.run([sys.executable, "-m", "media_ai", "capabilities", *argv],
                         capture_output=True, text=True, timeout=60)
    return res.returncode, json.loads(res.stdout)


def test_naming_a_removed_model_is_an_error_not_a_stub():
    """Asking about one model by name and getting a retired one must stay exit 3 —
    turning it into ok:true would hide that the caller named something unusable."""
    code, out = _run_cli("--provider", "openai", "--model", "dall-e-3")
    assert code == 3 and out["ok"] is False
    assert out["error"]["category"] == "unsupported"


def test_naming_a_live_model_still_succeeds():
    code, out = _run_cli("--provider", "openai", "--model", "gpt-image-2")
    assert code == 0 and out["ok"] is True


def test_listing_all_models_includes_retired_entries():
    code, out = _run_cli("--provider", "openai", "--all-models")
    assert code == 0
    retired = [m for m in out["providers"][0]["models"] if m["status"] == "removed"]
    assert {m["model"] for m in retired} == {"dall-e-3", "sora"}


def test_retired_listing_entries_have_the_same_shape_as_live_ones():
    """Agents parse providers[].models[] as one uniform shape; a short dict would make
    a retired model look like a malformed one."""
    _, out = _run_cli("--provider", "openai", "--all-models")
    models = out["providers"][0]["models"]
    live = next(m for m in models if m["status"] == "ga")
    gone = next(m for m in models if m["status"] == "removed")
    assert set(gone) == set(live)


def test_retired_listing_entry_names_its_replacement():
    _, out = _run_cli("--provider", "openai", "--all-models")
    gone = next(m for m in out["providers"][0]["models"] if m["model"] == "dall-e-3")
    assert gone["replacement"] == "gpt-image-2"


def test_default_listing_omits_retired_models():
    _, out = _run_cli("--provider", "openai")
    assert all(m["status"] != "removed" for m in out["providers"][0]["models"])
