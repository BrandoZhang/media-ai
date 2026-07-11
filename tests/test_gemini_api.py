"""Network-free tests for the Gemini adapter (generateContent + Imagen + Veo)."""

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


def test_imagen_predict_body_and_parse(fake_provider, tmp_path):
    prov, fake = fake_provider(GeminiProvider, [{"predictions": [{"mimeType": "image/png", "bytesBase64Encoded": PNG_1x1}]}])
    req = ImageRequest(prompt="mountain", output=tmp_path / "o.png", model="imagen-4.0-generate-001",
                       geometry=GeometrySpec(aspect_ratio="16:9"), count=1, seed=42, negative_prompt="text")
    res = prov.generate_image(req)
    call = fake.calls[0]
    assert call["path"] == "/models/imagen-4.0-generate-001:predict"
    params = call["body"]["parameters"]
    assert params["sampleCount"] == 1 and params["aspectRatio"] == "16:9"
    assert params["seed"] == 42 and params["negativePrompt"] == "text"
    assert Path(res.primary().path).is_file()


def test_imagen_no_predictions_is_safety(fake_provider, tmp_path):
    prov, _ = fake_provider(GeminiProvider, [{"predictions": []}])
    with pytest.raises(MediaError) as ei:
        prov.generate_image(ImageRequest(prompt="x", output=tmp_path / "o.png", model="imagen-4.0-generate-001"))
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
