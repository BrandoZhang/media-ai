"""The shipped binding manifests must be complete and self-consistent.

This is the enforceable half of the "how to add a model" rule: a new model arrives as
a manifest entry naming its provider, its wire id, the scenes it serves, its limits
and how it authenticates — and these tests fail if any of that is missing, malformed,
or contradicts itself. What used to be a paragraph in a contributing guide is a set
of assertions.

Two checks are deliberately absent until the adapter interface lands: that every
``provider.adapter`` imports, and that each adapter implements the scenes its
bindings declare. Both need the ``Adapter`` base class, so they arrive with it.
Until then :func:`test_adapter_reference_is_well_formed` pins the *shape* of the
reference, which is what can be checked without the class existing.
"""

from __future__ import annotations

import pytest

from media_ai.core.binding import (
    AuthKind,
    Lifecycle,
    ManifestError,
    Transport,
    builtin_catalog,
    load_manifest,
)
from media_ai.core.scene import Scene

CATALOG = builtin_catalog()
BINDINGS = CATALOG.all()
BINDING_IDS = [b.id for b in BINDINGS]


def test_every_shipped_manifest_parses():
    assert CATALOG.providers, "no manifests were discovered"
    assert BINDINGS


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_binding_is_complete(binding_id):
    b = CATALOG.get(binding_id)
    provider = CATALOG.providers[b.provider]

    assert b.id == f"{b.provider}/{b.model}"
    assert b.scenes, "a binding with no scenes cannot be selected for anything"
    assert b.model_id, "the id sent on the wire must be declared"
    assert b.title

    # A local backend has no wire id of its own, so model_id defaults to the model.
    if provider.transport is not Transport.LOCAL:
        assert b.model_id != "" and b.model_id is not None


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_declared_options_are_unique_and_named(binding_id):
    options = CATALOG.get(binding_id).constraints.options
    assert len(set(options)) == len(options), "an option is declared twice"
    assert all(o and o == o.strip() for o in options)


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_verified_is_a_date_or_honestly_absent(binding_id):
    """``verified`` records a live-API run or says nothing. It never guesses.

    The parser enforces the format; this pins the intent, so that filling the field
    in to make a table look complete has to be a deliberate edit to a test named
    after what it protects.
    """
    verified = CATALOG.get(binding_id).verified
    assert verified is None or len(verified) == 10


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_deprecated_bindings_name_a_replacement(binding_id):
    b = CATALOG.get(binding_id)
    if b.lifecycle is Lifecycle.DEPRECATED:
        assert b.replacement and CATALOG.get(b.replacement), "replacement must be a real binding"


@pytest.mark.parametrize("provider_name", sorted(builtin_catalog().providers))
def test_adapter_reference_is_well_formed(provider_name):
    module, _, cls = CATALOG.providers[provider_name].adapter.partition(":")
    assert module.startswith("media_ai.") or "." in module
    assert cls and cls[0].isupper()


@pytest.mark.parametrize("provider_name", sorted(builtin_catalog().providers))
def test_credentialed_providers_declare_how_to_authenticate(provider_name):
    p = CATALOG.providers[provider_name]
    if p.auth.kind is AuthKind.API_KEY:
        assert p.auth.header, "an api_key provider must say which header carries it"
        assert p.auth.env, "the wizard offers these as a credential source"
        assert p.base_url.default
        assert p.setup_hint, "a user being asked for a key deserves to be told where to get one"
    if p.transport is Transport.LOCAL:
        assert p.auth.kind is AuthKind.NONE


def test_binding_ids_and_aliases_are_globally_unique():
    seen: set[str] = set()
    for b in BINDINGS:
        for key in (b.id, *b.aliases):
            assert key not in seen, f"duplicate binding key {key!r}"
            seen.add(key)


def test_every_scene_has_at_least_one_binding():
    """A scene nothing serves is a scene the CLI can derive but never satisfy."""
    unserved = [s.value for s in Scene if not CATALOG.for_scene(s)]
    assert not unserved, f"no binding serves: {', '.join(unserved)}"


def test_a_model_may_be_served_by_more_than_one_binding():
    """The whole point of the refactor: (provider, model) is the unit, not model.

    Nothing in the first batch exercises it yet — every model has one provider today —
    so this asserts the *lookup* handles the case rather than that the data contains it.
    """
    for b in BINDINGS:
        assert b in CATALOG.for_model(b.model)


def test_local_bindings_need_no_credential():
    for b in BINDINGS:
        provider = CATALOG.providers[b.provider]
        if provider.transport is Transport.LOCAL:
            assert not provider.auth.needs_credential


# --------------------------------------------------------------------------
# the parser refuses what a manifest must never say
# --------------------------------------------------------------------------

_MINIMAL = """
[provider]
name = "acme"
adapter = "acme_media:AcmeAdapter"
[provider.auth]
kind = "none"
[provider.base_url]
default = "https://api.acme.test"
[[binding]]
id = "acme/thing"
model = "thing"
model_id = "thing-v1"
scenes = ["image.text_to_image"]
"""


def _load(text):
    return load_manifest(text, source="test.toml")


def test_minimal_manifest_is_enough():
    provider, bindings = _load(_MINIMAL)
    assert provider.name == "acme"
    assert bindings[0].scenes == frozenset({Scene.IMAGE_TEXT_TO_IMAGE})
    assert bindings[0].verified is None


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda t: t.replace('id = "acme/thing"', 'id = "Acme/Thing"'), "lowercase"),
        (lambda t: t.replace('id = "acme/thing"', 'id = "other/thing"'), "provider"),
        (lambda t: t.replace('model = "thing"', 'model = "other"'), "does not match"),
        (lambda t: t.replace('scenes = ["image.text_to_image"]', 'scenes = ["image.hallucinate"]'), "unknown scene"),
        (lambda t: t.replace('scenes = ["image.text_to_image"]', "scenes = []"), "no scenes"),
        (lambda t: t.replace('model_id = "thing-v1"', 'model_id = ""'), "model_id"),
        (lambda t: t + '\nverified = "last tuesday"\n', "YYYY-MM-DD"),
        (lambda t: t + '\nlifecycle = "deprecated"\n', "replacement"),
        (lambda t: t.replace('adapter = "acme_media:AcmeAdapter"', 'adapter = "acme_media"'), "module:Class"),
        (lambda t: t.replace('kind = "none"', 'kind = "wishful"'), "not one of"),
    ],
)
def test_parser_rejects(mutation, expected):
    with pytest.raises(ManifestError) as exc:
        _load(mutation(_MINIMAL))
    assert expected in str(exc.value)
    assert "test.toml" in str(exc.value), "an error must name the manifest it came from"


def test_http_provider_needs_a_base_url():
    text = _MINIMAL.replace('default = "https://api.acme.test"', "")
    with pytest.raises(ManifestError, match="base_url"):
        _load(text)


def test_local_provider_may_not_require_a_credential():
    text = _MINIMAL.replace('kind = "none"', 'kind = "api_key"').replace(
        'adapter = "acme_media:AcmeAdapter"', 'adapter = "acme_media:AcmeAdapter"\ntransport = "local"'
    )
    with pytest.raises(ManifestError, match="local provider"):
        _load(text)
