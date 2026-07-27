"""``[providers.<name>.endpoints]`` — mapping an opaque deployment id to a real model.

An Ark endpoint id (``ep-2026…-zrbtw``) names a *deployment*, not a model, so it
carries no capability information. Before this mapping existed, ``capabilities()``
answered whatever the caller asked: the same id claimed image.edit when asked about
images and video.generate when asked about video. That made "does my endpoint support
image editing?" unanswerable — which is exactly what the install wizard needs to know.

Mapping the id to the model behind it makes the answer come from the model.
"""

from __future__ import annotations

import pytest

from media_ai.core import registry
from media_ai.core.capabilities import Operation
from media_ai.core.types import Modality
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
    prov = registry.get_provider("volc", config={})
    assert ops(prov.capabilities("ep-unknown", modality=Modality.IMAGE)) == {"image.generate", "image.edit"}
    assert ops(prov.capabilities("ep-unknown", modality=Modality.VIDEO)) == {"video.generate"}


@pytest.mark.parametrize("asked", [Modality.IMAGE, Modality.VIDEO, None])
def test_mapped_image_endpoint_is_stable_across_what_is_asked(asked):
    """The fix: the answer comes from the model, not from the question."""
    prov = registry.get_provider("volc", config=MAPPED)
    assert ops(prov.capabilities("ep-my-img", modality=asked)) == {"image.generate", "image.edit"}


@pytest.mark.parametrize("asked", [Modality.IMAGE, Modality.VIDEO, None])
def test_mapped_video_endpoint_is_stable_across_what_is_asked(asked):
    prov = registry.get_provider("volc", config=MAPPED)
    assert ops(prov.capabilities("ep-my-vid", modality=asked)) == {"video.generate"}


def test_mapped_endpoint_reports_the_id_the_api_expects():
    """Capabilities describe the backing model but must name the id callers use."""
    caps = registry.get_provider("volc", config=MAPPED).capabilities("ep-my-img")
    assert caps.model == "ep-my-img"
    assert any(IMG in note for note in caps.notes)


def test_mapped_endpoint_gains_the_real_geometry_constraints():
    """An unmapped endpoint leaves geometry unconstrained; a mapped one inherits it."""
    unmapped = registry.get_provider("volc", config={}).capabilities("ep-my-img", modality=Modality.IMAGE)
    mapped = registry.get_provider("volc", config=MAPPED).capabilities("ep-my-img")
    assert unmapped.image.aspect_ratios == ()
    assert mapped.image.aspect_ratios  # inherited from doubao-seedream
    assert mapped.image.pixel_max == (4096, 4096)


def test_edit_support_is_answerable_for_a_mapped_endpoint():
    """The wizard's question: can this endpoint do image.edit?"""
    caps = registry.get_provider("volc", config=MAPPED).capabilities("ep-my-img")
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
    prov = registry.get_provider("openai", config={"endpoints": {"my-deploy": "gpt-image-1-mini"}})
    assert prov.backing_model("my-deploy") == "gpt-image-1-mini"


# ------------------------------------------------------------ config file


def test_endpoints_load_from_the_config_file(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[providers.volc]\n'
        'image_model = "ep-my-img"\n\n'
        '[providers.volc.endpoints]\n'
        f'"ep-my-img" = "{IMG}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(cfg))
    prov = registry.get_provider("volc")
    assert prov.image_model == "ep-my-img"
    assert ops(prov.capabilities("ep-my-img", modality=Modality.VIDEO)) == {"image.generate", "image.edit"}
