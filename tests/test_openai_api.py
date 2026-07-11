"""Network-free tests for the OpenAI adapter (Images API)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import PNG_1x1, PNG_1x1_BYTES
from media_ai.core.capabilities import validate_request
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.types import GeometrySpec, ImageRequest, JobRef, MediaRef, Modality, Operation
from media_ai.providers.openai import OpenAIProvider


def test_gpt_image_generations_body(fake_provider, tmp_path):
    prov, fake = fake_provider(OpenAIProvider, [{"data": [{"b64_json": PNG_1x1}], "usage": {"total_tokens": 40}}])
    req = ImageRequest(prompt="a cat", output=tmp_path / "o.png", model="gpt-image-2",
                       geometry=GeometrySpec(width=1024, height=1536), count=1, quality="high")
    res = prov.generate_image(req)
    call = fake.calls[0]
    assert call["path"] == "/images/generations"
    body = call["body"]
    assert body["model"] == "gpt-image-2" and body["size"] == "1024x1536"
    assert body["quality"] == "high" and body["n"] == 1
    assert "response_format" not in body  # gpt-image is always base64
    assert Path(res.primary().path).is_file()
    assert res.usage["total_tokens"] == 40


def test_dalle_sets_response_format_b64(fake_provider, tmp_path):
    prov, fake = fake_provider(OpenAIProvider, [{"data": [{"b64_json": PNG_1x1}]}])
    prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", model="dall-e-3",
                                     geometry=GeometrySpec(aspect_ratio="16:9")))
    body = fake.calls[0]["body"]
    assert body["response_format"] == "b64_json"
    assert body["size"] == "1792x1024"  # landscape ratio -> dall-e-3 landscape size


def test_edit_uses_multipart_with_images_and_mask(fake_provider, tmp_path):
    ref, mask = tmp_path / "r.png", tmp_path / "m.png"
    ref.write_bytes(PNG_1x1_BYTES)
    mask.write_bytes(PNG_1x1_BYTES)
    prov, fake = fake_provider(OpenAIProvider, [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    req = ImageRequest(prompt="add a hat", output=tmp_path / "o.png", operation=Operation.IMAGE_EDIT,
                       model="gpt-image-2", references=[MediaRef(str(ref), "reference_image")],
                       mask=MediaRef(str(mask), "mask"))
    prov.generate_image(req)
    call = fake.calls[0]
    assert call.get("multipart") and call["path"] == "/images/edits"
    field_names = [f[0] for f in call["files"]]
    assert field_names == ["image[]", "mask"]


def test_gpt_image_2_rejects_transparent_via_capabilities():
    caps = OpenAIProvider().capabilities("gpt-image-2")
    assert caps.image.supports_transparency is False
    caps1 = OpenAIProvider().capabilities("gpt-image-1")
    assert caps1.image.supports_transparency is True


def test_error_mapper_content_policy_is_safety():
    prov = OpenAIProvider()
    assert prov._error(400, '{"error":{"code":"content_policy_violation"}}').category == ErrorCategory.SAFETY
    assert prov._error(429, "insufficient_quota").category == ErrorCategory.RATE_LIMIT
    assert prov._error(401, "bad key").category == ErrorCategory.AUTH


def test_sora_video_submit_returns_job(fake_provider, tmp_path):
    prov, fake = fake_provider(OpenAIProvider, [{"id": "vid_123", "status": "queued"}])
    from media_ai.core.types import VideoRequest

    handle = prov.generate_video(VideoRequest(prompt="a wave", output=tmp_path / "v.mp4", model="sora-2",
                                              duration=4, wait=False, options={"size": "1280x720"}))
    body = fake.calls[0]["body"]
    assert fake.calls[0]["path"] == "/videos" and body["seconds"] == "4" and body["size"] == "1280x720"
    assert handle.id == "vid_123"


def test_aspect_ratio_not_rejected_preflight():
    # `_size` maps any ratio to a valid pixel size, so validation must not block it
    prov = OpenAIProvider()
    for model in ("gpt-image-2", "dall-e-3"):
        caps = prov.capabilities(model, Modality.IMAGE)
        req = ImageRequest(prompt="x", output=Path("o.png"), model=model, geometry=GeometrySpec(aspect_ratio="16:9"))
        validate_request(req, caps)  # must not raise


def test_dalle_rejects_out_of_enum_pixel_size():
    prov = OpenAIProvider()
    caps = prov.capabilities("dall-e-3", Modality.IMAGE)
    with pytest.raises(MediaError) as ei:
        validate_request(ImageRequest(prompt="x", output=Path("o.png"), model="dall-e-3",
                                      geometry=GeometrySpec(width=500, height=500)), caps)
    assert ei.value.category == ErrorCategory.UNSUPPORTED


def test_sora_get_job_failed_raises(fake_provider):
    prov, _ = fake_provider(OpenAIProvider, [{"id": "v", "status": "failed"}])
    with pytest.raises(MediaError) as ei:
        prov.get_job(JobRef(provider="openai", id="v"))
    assert ei.value.category == ErrorCategory.PROVIDER
