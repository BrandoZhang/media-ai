"""The shipped binding manifests must be complete and self-consistent.

This is the enforceable half of the "how to add a model" rule: a new model arrives as
a manifest entry naming its provider, its wire id, the scenes it serves, its limits
and how it authenticates — and these tests fail if any of that is missing, malformed,
or contradicts itself. What used to be a paragraph in a contributing guide is a set
of assertions.

The two checks that matter most are the ones tying data to code: every
``provider.adapter`` must import, and it must implement every scene the manifests
name. Without them a manifest could promise anything — the failure mode a
declarative layer invites, caught here where it is free rather than at a call.
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
def test_the_declared_adapter_imports(provider_name):
    from media_ai.core.adapter import Adapter
    from media_ai.core.registry import load_adapter_class

    cls = load_adapter_class(CATALOG.providers[provider_name].adapter)
    assert issubclass(cls, Adapter)


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_every_declared_scene_is_implemented(binding_id):
    """A manifest cannot advertise a scene whose code was never written.

    This is the join between the two halves of a backend. The declaration is free to
    write and the implementation is not, so nothing but a test keeps them honest.
    """
    from media_ai.core.registry import load_adapter_class

    b = CATALOG.get(binding_id)
    adapter = load_adapter_class(CATALOG.providers[b.provider].adapter)
    implemented = adapter.supported_scenes(adapter.__new__(adapter))
    missing = sorted(s.value for s in b.scenes - implemented)
    assert not missing, f"{binding_id} declares scenes its adapter does not implement: {missing}"


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


def test_binding_ids_are_globally_unique():
    """One binding, one id. A second name for the same thing is what made "which
    binding was billed?" unanswerable from a log line."""
    seen: set[str] = set()
    for b in BINDINGS:
        assert b.id not in seen, f"duplicate binding id {b.id!r}"
        seen.add(b.id)


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


@pytest.mark.parametrize("binding_id", BINDING_IDS)
def test_no_bound_is_encoded_as_a_sentinel(binding_id):
    """An absent limit is an absent field, never ``0`` or ``-1``.

    A sentinel does not make a careless comparison fail — it makes it quietly do the
    opposite: ``w * h > pixel_total_max`` with a max of 0 or -1 rejects every request
    and says nothing. ``None`` cannot be compared by accident.
    """
    c = CATALOG.get(binding_id).constraints
    bounds = {
        "pixel_total_min": c.geometry.pixel_total_min,
        "pixel_total_max": c.geometry.pixel_total_max,
        "pixel_multiple": c.geometry.pixel_multiple,
        "pixel_max_edge": c.geometry.pixel_max_edge,
        "max_edge_ratio": c.geometry.max_edge_ratio,
        "max_total_images": c.output.max_total_images,
        "references.max_bytes": c.references.max_bytes,
        "references.max_pixels": c.references.max_pixels,
        "references.min_edge": c.references.min_edge,
        "audio.max_characters": c.audio.max_characters,
    }
    for name, value in bounds.items():
        assert value is None or value > 0, f"{name} = {value!r}: use an absent field, not a sentinel"

    for name, pair in (("geometry.ratio_range", c.geometry.ratio_range),
                       ("references.ratio_range", c.references.ratio_range),
                       ("audio.duration_ms", c.audio.duration_ms),
                       ("audio.duration_s", c.audio.duration_s)):
        if pair is not None:
            # A pair is only allowed where both ends are always known — so both ends
            # must be real. Anything half-known belongs in two optional scalars.
            assert all(v > 0 for v in pair), f"{name} = {pair!r}"
            assert pair[0] <= pair[1], f"{name} is inverted: {pair!r}"


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
