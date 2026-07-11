"""Network-free tests for the Gemini adapter (generateContent + Veo)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import PNG_1x1, PNG_1x1_BYTES
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.types import GeometrySpec, ImageRequest, JobRef, MediaRef, VideoRequest
from media_ai.providers.gemini import GeminiProvider


def test_native_generatecontent_body_and_parse(fake_provider, tmp_path):
    resp = {"candidates": [{"content": {"parts": [{"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": PNG_1x1}}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"candidatesTokenCount": 1290, "totalTokenCount": 1302}}
    prov, fake = fake_provider(GeminiProvider, [resp])
    req = ImageRequest(prompt="a fox", output=tmp_path / "o.png", model="gemini-2.5-flash-image",
                       geometry=GeometrySpec(aspect_ratio="16:9"))
    res = prov.generate_image(req)
    call = fake.calls[0]
    assert call["path"] == "/models/gemini-2.5-flash-image:generateContent"
    gc = call["body"]["generationConfig"]
    assert "IMAGE" in gc["responseModalities"]
    assert gc["imageConfig"]["aspectRatio"] == "16:9"
    assert Path(res.primary().path).is_file()
    assert res.usage["totalTokenCount"] == 1302


def test_native_reference_becomes_inline_data(fake_provider, tmp_path):
    ref = tmp_path / "r.png"
    ref.write_bytes(PNG_1x1_BYTES)
    resp = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": PNG_1x1}}]}}]}
    prov, fake = fake_provider(GeminiProvider, [resp])
    prov.generate_image(ImageRequest(prompt="edit", output=tmp_path / "o.png", model="gemini-2.5-flash-image",
                                     references=[MediaRef(str(ref), "reference_image")]))
    parts = fake.calls[0]["body"]["contents"][0]["parts"]
    assert any("inlineData" in p for p in parts)


def test_200_but_no_image_is_safety_error(fake_provider, tmp_path):
    resp = {"candidates": [{"content": {"parts": [{"text": "I can't create that."}]}, "finishReason": "IMAGE_SAFETY"}]}
    prov, _ = fake_provider(GeminiProvider, [resp])
    with pytest.raises(MediaError) as ei:
        prov.generate_image(ImageRequest(prompt="x", output=tmp_path / "o.png", model="gemini-2.5-flash-image"))
    assert ei.value.category == ErrorCategory.SAFETY
    assert not (tmp_path / "o.png").exists()  # no empty file written


def test_prompt_block_is_safety_error(fake_provider, tmp_path):
    prov, _ = fake_provider(GeminiProvider, [{"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}])
    with pytest.raises(MediaError) as ei:
        prov.generate_image(ImageRequest(prompt="x", output=tmp_path / "o.png", model="gemini-2.5-flash-image"))
    assert ei.value.category == ErrorCategory.SAFETY


def test_veo_lro_poll_and_download(fake_provider, tmp_path):
    op = "models/veo-3.0-generate-001/operations/abc"
    responses = [
        {"name": op, "done": False},  # create
        {"name": op, "done": False},  # first poll
        {"name": op, "done": True, "response": {"generateVideoResponse": {"generatedSamples": [
            {"video": {"uri": "https://generativelanguage.googleapis.com/v1beta/files/X:download", "mimeType": "video/mp4"}}]}}},
    ]
    prov, fake = fake_provider(GeminiProvider, responses)
    prov.poll_interval = 0
    req = VideoRequest(prompt="drone shot", output=tmp_path / "v.mp4", model="veo-3.0-generate-001",
                       geometry=GeometrySpec(aspect_ratio="16:9", resolution="1080p"), duration=8)
    res = prov.generate_video(req)
    create_body = fake.calls[0]["body"]
    assert fake.calls[0]["path"] == "/models/veo-3.0-generate-001:predictLongRunning"
    assert create_body["parameters"]["durationSeconds"] == 8 and create_body["parameters"]["resolution"] == "1080p"
    assert (tmp_path / "v.mp4").is_file()
    assert any("files/X:download" in u for u in fake.downloads)
    assert res.modality == "video"


def test_veo_image_to_video_inlines_first_frame(fake_provider, tmp_path):
    ff = tmp_path / "ff.png"
    ff.write_bytes(PNG_1x1_BYTES)
    prov, fake = fake_provider(GeminiProvider, [
        {"name": "op"},  # create
        {"name": "op", "done": True,  # poll -> done
         "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://x/files/Y:download"}}]}}}])
    prov.poll_interval = 0
    prov.generate_video(VideoRequest(prompt="move", output=tmp_path / "v.mp4", model="veo-3.1-generate-preview",
                                     first_frame=MediaRef(str(ff), "first_frame"), duration=8, wait=True))
    instance = fake.calls[0]["body"]["instances"][0]
    assert "image" in instance and instance["image"]["mimeType"] == "image/png"


def test_veo_cancel_is_unsupported():
    with pytest.raises(MediaError) as ei:
        GeminiProvider().cancel_job(JobRef(provider="gemini", id="op"))
    assert ei.value.category == ErrorCategory.UNSUPPORTED


def test_veo_get_job_failed_operation_raises(fake_provider):
    prov, _ = fake_provider(GeminiProvider, [{"name": "op", "done": True, "error": {"code": 13, "message": "boom"}}])
    with pytest.raises(MediaError) as ei:
        prov.get_job(JobRef(provider="gemini", id="op"))
    assert ei.value.category == ErrorCategory.PROVIDER


# ---- new image features (grounding / image-search / thinking) ------------


def _one_image():
    return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": PNG_1x1}}]}}]}


def test_native_grounding_adds_google_search_tool(fake_provider, tmp_path):
    prov, fake = fake_provider(GeminiProvider, [_one_image()])
    prov.generate_image(ImageRequest(prompt="last night's match", output=tmp_path / "o.png",
                                     model="gemini-3.1-flash-image", options={"grounding": True}))
    assert fake.calls[0]["body"]["tools"] == [{"google_search": {}}]


def test_native_image_search_adds_search_types(fake_provider, tmp_path):
    prov, fake = fake_provider(GeminiProvider, [_one_image()])
    prov.generate_image(ImageRequest(prompt="a Timareta butterfly", output=tmp_path / "o.png",
                                     model="gemini-3.1-flash-image", options={"image_search": True}))
    tool = fake.calls[0]["body"]["tools"][0]["google_search"]
    assert tool["search_types"] == ["web_search", "image_search"]


def test_native_thinking_level_option(fake_provider, tmp_path):
    prov, fake = fake_provider(GeminiProvider, [_one_image()])
    prov.generate_image(ImageRequest(prompt="a glass city", output=tmp_path / "o.png",
                                     model="gemini-3.1-flash-image", options={"thinking_level": "high"}))
    assert fake.calls[0]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}


# ---- new video features (reference images / seed / extension) ------------


def test_veo_reference_images_and_seed(fake_provider, tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    for p in (a, b):
        p.write_bytes(PNG_1x1_BYTES)
    prov, fake = fake_provider(GeminiProvider, [
        {"name": "op"},
        {"name": "op", "done": True,
         "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://x/files/Z:download"}}]}}}])
    prov.poll_interval = 0
    prov.generate_video(VideoRequest(prompt="walk", output=tmp_path / "v.mp4", model="veo-3.1-generate-preview",
                                     reference_images=[MediaRef(str(a), "reference_image"),
                                                       MediaRef(str(b), "reference_image")],
                                     seed=7, duration=8))
    body = fake.calls[0]["body"]
    refs = body["instances"][0]["referenceImages"]
    assert len(refs) == 2 and refs[0]["referenceType"] == "asset"
    assert refs[0]["image"]["mimeType"] == "image/png"
    assert body["parameters"]["seed"] == 7


def test_veo_extension_maps_reference_video_to_video_param(fake_provider, tmp_path):
    clip = tmp_path / "src.mp4"
    clip.write_bytes(b"FAKE-MP4")
    prov, fake = fake_provider(GeminiProvider, [
        {"name": "op"},
        {"name": "op", "done": True,
         "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://x/files/E:download"}}]}}}])
    prov.poll_interval = 0
    prov.generate_video(VideoRequest(prompt="continue", output=tmp_path / "v.mp4", model="veo-3.1-generate-preview",
                                     reference_videos=[MediaRef(str(clip), "reference_video")], duration=8))
    instance = fake.calls[0]["body"]["instances"][0]
    assert instance["video"]["mimeType"] == "video/mp4"


# ---- capabilities: current Nano Banana + Veo 3.1 lineup ------------------


def test_models_expose_nano_banana_and_veo31():
    models = GeminiProvider().models()
    assert "gemini-3.1-flash-image" in models and "gemini-3.1-flash-lite-image" in models
    assert "veo-3.1-generate-preview" in models
    # deprecated snapshots are dropped from discovery
    assert "veo-2.0-generate-001" not in models and "veo-3.0-generate-001" not in models


def test_native_caps_per_tier():
    prov = GeminiProvider()
    flash = prov.capabilities("gemini-3.1-flash-image").image
    assert "1:4" in flash.aspect_ratios and flash.named_sizes == ("512", "1K", "2K", "4K")
    assert flash.max_references == 14 and "grounding" in flash.options and "thinking_level" in flash.options

    lite = prov.capabilities("gemini-3.1-flash-lite-image").image
    assert lite.named_sizes == ("1K",) and "1:4" not in lite.aspect_ratios and lite.options == ()

    pro = prov.capabilities("gemini-3-pro-image").image
    assert pro.named_sizes == ("1K", "2K", "4K") and "1:4" not in pro.aspect_ratios and pro.options == ("grounding",)

    legacy = prov.capabilities("gemini-2.5-flash-image").image
    assert legacy.named_sizes == ("1K",) and legacy.max_references == 3


def test_veo_caps_reference_and_resolution_by_version():
    prov = GeminiProvider()
    v31 = prov.capabilities("veo-3.1-generate-preview").video
    assert v31.supports_reference_images and v31.supports_reference_videos and "4k" in v31.resolutions
    lite = prov.capabilities("veo-3.1-lite-generate-preview").video
    assert not lite.supports_reference_images and "4k" not in lite.resolutions and lite.resolutions == ("720p", "1080p")
