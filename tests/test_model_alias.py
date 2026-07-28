"""Mapping an opaque deployment id to the model behind it.

An Ark endpoint id (``ep-2026…-zrbtw``) names a *deployment*, not a model, so it
carries no capability information. Before this mapping existed, ``capabilities()``
answered whatever the caller asked: the same id claimed image.edit when asked about
images and video.generate when asked about video. That made "does my endpoint support
image editing?" unanswerable — which is exactly what the install wizard needs to know.

Mapping the id to the model behind it makes the answer come from the model.

The user-facing form is now ``extends`` in the config — one mechanism that also covers
a second account and a second region (see :mod:`media_ai.core.config`). What is tested
here is the adapter-level half it rests on.
"""

from __future__ import annotations

import pytest

from media_ai.core.capabilities import Operation
from media_ai.core.scene import Scene
from media_ai.core.types import Modality
from media_ai.providers.openai import OpenAIProvider
from media_ai.providers.volc import VolcProvider

IMG = "doubao-seedream-4-5-251128"
VID = "doubao-seedance-2-0-260128"
MAPPED = {"endpoints": {"ep-my-img": IMG, "ep-my-vid": VID}}

# 1x1 transparent PNG, base64 — same fixture the other adapter tests use.
PNG_1x1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def mapped_provider(fake_provider):
    """A VolcProvider carrying the endpoint mapping, with its HTTP layer recorded."""
    return lambda responses: fake_provider(VolcProvider, responses, config=MAPPED)


def ops(caps) -> set[str]:
    out = set()
    for block in (caps.image, caps.video, caps.audio):
        if block is not None:
            out |= {o.value for o in block.operations}
    return out


# ------------------------------------------------------- the fabrication bug


def test_unmapped_endpoint_still_answers_whatever_is_asked():
    """Documents the fail-open behaviour that remains when no mapping exists."""
    prov = VolcProvider(config={})
    assert ops(prov.capabilities("ep-unknown", modality=Modality.IMAGE)) == {"image.generate", "image.edit"}
    assert ops(prov.capabilities("ep-unknown", modality=Modality.VIDEO)) == {"video.generate"}


@pytest.mark.parametrize("asked", [Modality.IMAGE, Modality.VIDEO, None])
def test_mapped_image_endpoint_is_stable_across_what_is_asked(asked):
    """The fix: the answer comes from the model, not from the question."""
    prov = VolcProvider(config=MAPPED)
    assert ops(prov.capabilities("ep-my-img", modality=asked)) == {"image.generate", "image.edit"}


@pytest.mark.parametrize("asked", [Modality.IMAGE, Modality.VIDEO, None])
def test_mapped_video_endpoint_is_stable_across_what_is_asked(asked):
    prov = VolcProvider(config=MAPPED)
    assert ops(prov.capabilities("ep-my-vid", modality=asked)) == {"video.generate"}


def test_mapped_endpoint_reports_the_id_the_api_expects():
    """Capabilities describe the backing model but must name the id callers use."""
    caps = VolcProvider(config=MAPPED).capabilities("ep-my-img")
    assert caps.model == "ep-my-img"
    assert any(IMG in note for note in caps.notes)


def test_mapped_endpoint_gains_the_real_geometry_constraints():
    """An unmapped endpoint leaves geometry unconstrained; a mapped one inherits it."""
    unmapped = VolcProvider(config={}).capabilities("ep-my-img", modality=Modality.IMAGE)
    mapped = VolcProvider(config=MAPPED).capabilities("ep-my-img")
    assert unmapped.image.aspect_ratios == ()
    assert mapped.image.aspect_ratios  # inherited from doubao-seedream
    assert mapped.image.pixel_max == (4096, 4096)


def test_edit_support_is_answerable_for_a_mapped_endpoint():
    """The wizard's question: can this endpoint do image.edit?"""
    caps = VolcProvider(config=MAPPED).capabilities("ep-my-img")
    assert Operation.IMAGE_EDIT in caps.image.operations


# --------------------------------------------------------------- the wire


def test_wire_request_uses_the_endpoint_id_not_the_backing_model(mapped_provider, tmp_path):
    """The mapping must never leak into the request: Ark only accepts the ep- id."""
    from media_ai.core.types import ImageRequest

    prov, fake = mapped_provider([{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="a cat", model="ep-my-img", output=tmp_path / "o.png"))

    body = fake.calls[0]["body"]
    assert body["model"] == "ep-my-img", "the endpoint id must reach the API verbatim"
    assert IMG not in str(body), "the backing model must never appear on the wire"


def test_wire_geometry_follows_the_backing_model(mapped_provider, tmp_path):
    """A mapped endpoint is geometry-checked like the model it serves."""
    from media_ai.core.geometry import GeometrySpec
    from media_ai.core.types import ImageRequest

    prov, fake = mapped_provider([{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(
        ImageRequest(prompt="p", model="ep-my-img", output=tmp_path / "o.png",
                     geometry=GeometrySpec(width=2560, height=1440))
    )
    assert fake.calls[0]["body"]["size"] == "2560x1440"


# ------------------------------------------------------- generic resolution


def test_backing_model_is_identity_without_a_mapping():
    assert VolcProvider(config={}).backing_model("ep-x") == "ep-x"


def test_backing_model_resolves_through_config():
    assert VolcProvider(config=MAPPED).backing_model("ep-my-img") == IMG


def test_backing_model_passes_through_unmapped_ids():
    assert VolcProvider(config=MAPPED).backing_model("ep-other") == "ep-other"


@pytest.mark.parametrize("bad", [None, "", "not-a-dict", 123, []])
def test_malformed_endpoints_table_is_ignored(bad):
    """A hand-edited config must not crash discovery."""
    prov = VolcProvider(config={"endpoints": bad})
    assert prov.backing_model("ep-x") == "ep-x"


def test_empty_mapping_target_is_ignored():
    assert VolcProvider(config={"endpoints": {"ep-x": ""}}).backing_model("ep-x") == "ep-x"


def test_none_model_passes_through():
    assert VolcProvider(config=MAPPED).backing_model(None) is None


def test_alias_is_available_to_every_provider():
    """backing_model lives on the base class, so an Azure-style deployment works too."""
    prov = OpenAIProvider(config={"endpoints": {"my-deploy": "gpt-image-1-mini"}})
    assert prov.backing_model("my-deploy") == "gpt-image-1-mini"


# ------------------------------------------------------------ config file


def test_a_deployment_id_is_configured_with_extends(tmp_path, monkeypatch):
    """The user-facing form: name the binding, point `extends` at the real model.

    The same mechanism covers a second account and a second region, so there is no
    separate endpoints table to learn — and the wire keeps using the `ep-` id, which
    is the only name the API accepts.
    """
    from media_ai.core.binding import builtin_catalog
    from media_ai.core.config import Config, UserBinding
    from media_ai.core.resolve import resolve

    config = Config(bindings={
        "volc-ark/my-endpoint": UserBinding(
            id="volc-ark/my-endpoint", extends="volc-ark/seedream-4.5",
            model_id="ep-example-endpoint", credential="env://ARK_API_KEY",
        )
    })
    rb = resolve(binding="volc-ark/my-endpoint", catalog=builtin_catalog(), config=config)

    assert rb.model_id == "ep-example-endpoint", "the wire keeps the id the API knows"
    # …while every capability question is answered by the model it actually serves.
    assert rb.spec.id == "volc-ark/seedream-4.5"
    assert rb.spec.constraints.output.formats == ("jpeg",)
    assert Scene.IMAGE_IMAGE_TO_IMAGE in rb.spec.scenes


def test_an_extending_binding_appears_in_discovery(tmp_path):
    """Otherwise `bindings list` would describe the package, not this machine."""
    from media_ai.core.binding import builtin_catalog
    from media_ai.core.config import Config, UserBinding
    from media_ai.core.resolve import available_bindings

    config = Config(bindings={
        "volc-ark-sg/seedance-2.0": UserBinding(
            id="volc-ark-sg/seedance-2.0", extends="volc-ark/seedance-2.0",
            base_url="https://ark.ap-southeast.volces.com/api/v3", credential="env://ARK_SG_KEY",
        )
    })
    ids = {b.id for b in available_bindings(builtin_catalog(), config)}
    assert "volc-ark-sg/seedance-2.0" in ids


# --------------------------------------------------- lifecycle through a mapping


def test_notes_are_not_duplicated():
    """The adapter hard-coded notes the catalogue spec also carries, printing each twice."""
    caps = VolcProvider(config={}).capabilities(IMG)
    assert len(caps.notes) == len(set(caps.notes))


def test_mapped_endpoint_inherits_the_backing_model_verification():
    """Mapping hides the real model behind an opaque id; its provenance must survive."""
    prov = VolcProvider(config=MAPPED)
    direct = prov.capabilities(IMG)
    mapped = prov.capabilities("ep-my-img")
    assert mapped.status == direct.status
    assert mapped.verified == direct.verified


def test_mapped_endpoint_carries_the_backing_model_notes():
    caps = VolcProvider(config=MAPPED).capabilities("ep-my-img")
    assert any("2K" in n for n in caps.notes), caps.notes
    assert any(IMG in n for n in caps.notes)


def test_configured_video_model_outside_the_catalogue_is_still_video():
    """Both branches of _is_video_model must agree; only one used to check video_model."""
    cfg = {"endpoints": {"ep-v": "house-video-model"}, "video_model": "house-video-model"}
    caps = VolcProvider(config=cfg).capabilities("ep-v", modality=Modality.IMAGE)
    assert caps.video is not None and caps.image is None
