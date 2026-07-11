"""Network-free tests for the Volc adapter: request-body construction + parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import PNG_1x1, PNG_1x1_BYTES
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.types import GeometrySpec, ImageRequest, JobRef, MediaRef, VideoRequest
from media_ai.providers.volc import VolcProvider


def test_text2image_body_uses_model_size_and_sequential(fake_provider, tmp_path):
    prov, fake = fake_provider(VolcProvider, [{"data": [{"b64_json": PNG_1x1}], "usage": {"total_tokens": 7}, "model": "m"}])
    req = ImageRequest(prompt="dune", output=tmp_path / "o.png", model="doubao-seedream-5-0-260128",
                       geometry=GeometrySpec(width=768, height=432), count=1, seed=1)
    res = prov.generate_image(req)
    body = fake.calls[0]["body"]
    assert fake.calls[0]["path"] == "/images/generations"
    assert body["model"] == "doubao-seedream-5-0-260128"
    assert body["size"] == "2K"  # 768x432 below floor -> named preset
    assert body["sequential_image_generation"] == "disabled"
    assert body["seed"] == 1
    assert res.primary().path.endswith("o.png") and Path(res.primary().path).is_file()


def test_group_sets_sequential_options(fake_provider, tmp_path):
    prov, fake = fake_provider(VolcProvider, [{"data": [{"b64_json": PNG_1x1}, {"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="team", output=tmp_path / "o.png", count=2,
                                     geometry=GeometrySpec(width=2560, height=1440)))
    body = fake.calls[0]["body"]
    assert body["size"] == "2560x1440"  # above floor -> exact WxH
    assert body["sequential_image_generation"] == "auto"
    assert body["sequential_image_generation_options"] == {"max_images": 2}


def test_image_seed_omitted_when_negative(fake_provider, tmp_path):
    prov, fake = fake_provider(VolcProvider, [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", seed=-1))
    assert "seed" not in fake.calls[0]["body"]


def test_image_response_without_images_raises(fake_provider, tmp_path):
    prov, _ = fake_provider(VolcProvider, [{"data": [], "usage": {}}])
    with pytest.raises(MediaError) as ei:
        prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png"))
    assert ei.value.category == ErrorCategory.PROVIDER


def test_video_content_roles_and_optional_fields(fake_provider, tmp_path):
    ff, lf = tmp_path / "ff.png", tmp_path / "lf.png"
    ff.write_bytes(PNG_1x1_BYTES)
    lf.write_bytes(PNG_1x1_BYTES)
    prov, fake = fake_provider(VolcProvider, [{"id": "task-1"}])
    req = VideoRequest(prompt="turns", output=tmp_path / "v.mp4",
                       first_frame=MediaRef(str(ff), "first_frame"), last_frame=MediaRef(str(lf), "last_frame"),
                       geometry=GeometrySpec(resolution="480p", aspect_ratio="adaptive"), duration=3, seed=5,
                       audio=True, return_last_frame=True, wait=False, options={"camera_fixed": True},
                       model="doubao-seedance-2-0-260128")
    handle = prov.generate_video(req)
    body = fake.calls[0]["body"]
    roles = [c.get("role") for c in body["content"] if "role" in c]
    assert roles == ["first_frame", "last_frame"]
    assert any(c.get("type") == "text" for c in body["content"])
    assert body["camera_fixed"] is True and body["generate_audio"] is True
    assert body["return_last_frame"] is True and body["seed"] == 5
    assert handle.to_dict()["status"] == "queued" and handle.id == "task-1"


def test_create_task_omits_seed_and_audio_when_unset(fake_provider, tmp_path):
    ff = tmp_path / "ff.png"
    ff.write_bytes(PNG_1x1_BYTES)
    prov, fake = fake_provider(VolcProvider, [{"id": "t"}])
    prov.generate_video(VideoRequest(prompt="", output=tmp_path / "v.mp4", first_frame=MediaRef(str(ff)),
                                     geometry=GeometrySpec(resolution="480p"), duration=2, seed=-1, audio=None, wait=False))
    body = fake.calls[0]["body"]
    assert "seed" not in body and "generate_audio" not in body and "return_last_frame" not in body


def test_video_needs_prompt_or_reference(fake_provider, tmp_path):
    prov, _ = fake_provider(VolcProvider, [{"id": "t"}])
    with pytest.raises(MediaError) as ei:
        prov.generate_video(VideoRequest(prompt="", output=tmp_path / "v.mp4", wait=False))
    assert ei.value.category == ErrorCategory.VALIDATION


def test_ref2video_multimodal_roles(fake_provider, tmp_path):
    img = tmp_path / "r.png"
    img.write_bytes(PNG_1x1_BYTES)
    prov, fake = fake_provider(VolcProvider, [{"id": "t"}])
    prov.generate_video(VideoRequest(prompt="scene", output=tmp_path / "v.mp4",
                                     reference_images=[MediaRef(str(img), "reference_image")],
                                     reference_videos=[MediaRef("https://example.com/v.mp4", "reference_video")],
                                     reference_audios=[MediaRef("https://example.com/a.mp3", "reference_audio")], wait=False))
    roles = [c.get("role") for c in fake.calls[0]["body"]["content"] if "role" in c]
    assert roles == ["reference_image", "reference_video", "reference_audio"]


def test_job_query_finalizes_and_downloads(fake_provider, tmp_path):
    prov, fake = fake_provider(VolcProvider, [
        {"id": "task-9", "status": "succeeded", "duration": 5, "usage": {"total_tokens": 12},
         "content": {"video_url": "https://cdn/x.mp4"}},
    ])
    out = tmp_path / "out.mp4"
    status = prov.get_job(JobRef(provider="volc", id="task-9"), output=out)
    d = status.to_dict()
    assert d["status"] == "succeeded" and d["path"] == str(out)
    assert out.is_file() and "https://cdn/x.mp4" in fake.downloads


def test_error_mapper_maps_safety(fake_provider, tmp_path):
    prov, _ = fake_provider(VolcProvider, [])
    err = prov._error(400, "request contains sensitive content")
    assert err.category == ErrorCategory.SAFETY
    assert prov._error(429, "rate").category == ErrorCategory.RATE_LIMIT


def test_video_wait_true_output_safety_raises(fake_provider, tmp_path):
    # a task that fails with an OUTPUT-safety code must surface as SAFETY (exit 8),
    # not a generic provider error — this reason lives in the task result, not HTTP.
    prov, _ = fake_provider(VolcProvider, [
        {"id": "task-x"},  # create
        {"status": "failed", "error": {"code": "OutputVideoSensitiveContentDetected", "message": "blocked"}},  # poll
    ])
    req = VideoRequest(prompt="scene", output=tmp_path / "v.mp4",
                       geometry=GeometrySpec(resolution="480p"), duration=2, wait=True)
    with pytest.raises(MediaError) as ei:
        prov.generate_video(req)
    assert ei.value.category == ErrorCategory.SAFETY
    assert "OutputVideoSensitiveContentDetected" in ei.value.message


def test_get_job_failed_raises_categorized(fake_provider, tmp_path):
    prov, _ = fake_provider(VolcProvider, [{"status": "failed", "error": {"code": "InternalServiceError", "message": "boom"}}])
    with pytest.raises(MediaError) as ei:
        prov.get_job(JobRef(provider="volc", id="t"))
    assert ei.value.category == ErrorCategory.PROVIDER and ei.value.retryable is True
