"""Network-free tests for the Volc adapter: request-body construction + parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import PNG_1x1, PNG_1x1_BYTES
from media_ai.core.validate import validate_request
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.types import GeometrySpec, ImageRequest, JobRef, MediaRef, VideoRequest
from conftest import adapter_for


def test_text2image_body_uses_model_size_and_sequential(fake_provider, tmp_path):
    prov, fake = fake_provider("volc-ark/seedream-4.5", [{"data": [{"b64_json": PNG_1x1}], "usage": {"total_tokens": 7}, "model": "m"}])
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
    prov, fake = fake_provider("volc-ark/seedream-4.5", [{"data": [{"b64_json": PNG_1x1}, {"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="team", output=tmp_path / "o.png", count=2,
                                     geometry=GeometrySpec(width=2560, height=1440)))
    body = fake.calls[0]["body"]
    assert body["size"] == "2560x1440"  # above floor -> exact WxH
    assert body["sequential_image_generation"] == "auto"
    assert body["sequential_image_generation_options"] == {"max_images": 2}


def test_image_seed_omitted_when_negative(fake_provider, tmp_path):
    prov, fake = fake_provider("volc-ark/seedream-4.5", [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", seed=-1))
    assert "seed" not in fake.calls[0]["body"]


def test_image_response_without_images_raises(fake_provider, tmp_path):
    prov, _ = fake_provider("volc-ark/seedream-4.5", [{"data": [], "usage": {}}])
    with pytest.raises(MediaError) as ei:
        prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png"))
    assert ei.value.category == ErrorCategory.PROVIDER


def test_video_content_roles_and_optional_fields(fake_provider, tmp_path):
    ff, lf = tmp_path / "ff.png", tmp_path / "lf.png"
    ff.write_bytes(PNG_1x1_BYTES)
    lf.write_bytes(PNG_1x1_BYTES)
    prov, fake = fake_provider("volc-ark/seedance-2.0", [{"id": "task-1"}])
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
    prov, fake = fake_provider("volc-ark/seedance-2.0", [{"id": "t"}])
    prov.generate_video(VideoRequest(prompt="", output=tmp_path / "v.mp4", first_frame=MediaRef(str(ff)),
                                     geometry=GeometrySpec(resolution="480p"), duration=2, seed=-1, audio=None, wait=False))
    body = fake.calls[0]["body"]
    assert "seed" not in body and "generate_audio" not in body and "return_last_frame" not in body
    assert "camera_fixed" not in body  # not forced when the caller didn't set it


def test_camera_fixed_only_sent_when_requested(fake_provider, tmp_path):
    # not provided -> absent (some models reject an unrequested camera_fixed)
    prov, fake = fake_provider("volc-ark/seedream-4.5", [{"id": "t"}])
    prov.generate_video(VideoRequest(prompt="x", output=tmp_path / "v.mp4",
                                     geometry=GeometrySpec(resolution="480p"), duration=2, wait=False))
    assert "camera_fixed" not in fake.calls[0]["body"]
    # explicitly provided via --option -> sent with the given value
    prov2, fake2 = fake_provider("volc-ark/seedream-4.5", [{"id": "t"}])
    prov2.generate_video(VideoRequest(prompt="x", output=tmp_path / "v2.mp4",
                                      geometry=GeometrySpec(resolution="480p"), duration=2, wait=False,
                                      options={"camera_fixed": False}))
    assert fake2.calls[0]["body"]["camera_fixed"] is False


def test_retry_classifier_vetoes_quota_but_allows_rpm():
    prov = adapter_for("volc-ark/seedream-4.5")
    import json as _json
    quota = _json.dumps({"error": {"code": "QuotaExceeded", "message": "used up"}})
    rpm = _json.dumps({"error": {"code": "RateLimitExceeded.EndpointRPMExceeded", "message": "rpm"}})
    assert prov.retry_classifier(429, quota) is False  # hard cap -> don't retry
    assert prov.retry_classifier(429, rpm) is True      # transient -> retry


def test_video_needs_prompt_or_reference(fake_provider, tmp_path):
    prov, _ = fake_provider("volc-ark/seedance-2.0", [{"id": "t"}])
    with pytest.raises(MediaError) as ei:
        prov.generate_video(VideoRequest(prompt="", output=tmp_path / "v.mp4", wait=False))
    assert ei.value.category == ErrorCategory.VALIDATION


def test_ref2video_multimodal_roles(fake_provider, tmp_path):
    img = tmp_path / "r.png"
    img.write_bytes(PNG_1x1_BYTES)
    prov, fake = fake_provider("volc-ark/seedance-2.0", [{"id": "t"}])
    prov.generate_video(VideoRequest(prompt="scene", output=tmp_path / "v.mp4",
                                     reference_images=[MediaRef(str(img), "reference_image")],
                                     reference_videos=[MediaRef("https://example.com/v.mp4", "reference_video")],
                                     reference_audios=[MediaRef("https://example.com/a.mp3", "reference_audio")], wait=False))
    roles = [c.get("role") for c in fake.calls[0]["body"]["content"] if "role" in c]
    assert roles == ["reference_image", "reference_video", "reference_audio"]


def test_job_query_finalizes_and_downloads(fake_provider, tmp_path):
    prov, fake = fake_provider("volc-ark/seedance-2.0", [
        {"id": "task-9", "status": "succeeded", "duration": 5, "usage": {"total_tokens": 12},
         "content": {"video_url": "https://cdn/x.mp4"}},
    ])
    out = tmp_path / "out.mp4"
    status = prov.get_job(JobRef(provider="volc", id="task-9"), output=out)
    d = status.to_dict()
    assert d["status"] == "succeeded" and d["artifacts"][0]["path"] == str(out)
    assert out.is_file() and "https://cdn/x.mp4" in fake.downloads


def test_error_mapper_maps_safety(fake_provider, tmp_path):
    prov, _ = fake_provider("volc-ark/seedream-4.5", [])
    err = prov._error(400, "request contains sensitive content")
    assert err.category == ErrorCategory.SAFETY
    assert prov._error(429, "rate").category == ErrorCategory.RATE_LIMIT


def test_video_wait_true_output_safety_raises(fake_provider, tmp_path):
    # a task that fails with an OUTPUT-safety code must surface as SAFETY (exit 8),
    # not a generic provider error — this reason lives in the task result, not HTTP.
    prov, _ = fake_provider("volc-ark/seedance-2.0", [
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
    prov, _ = fake_provider("volc-ark/seedance-2.0", [{"status": "failed", "error": {"code": "InternalServiceError", "message": "boom"}}])
    with pytest.raises(MediaError) as ei:
        prov.get_job(JobRef(provider="volc", id="t"))
    assert ei.value.category == ErrorCategory.PROVIDER and ei.value.retryable is True


# --- deployment ids ---------------------------------------------------------

ENDPOINT = "ep-20260214051115-zrbtw"


def test_a_deployment_id_goes_on_the_wire_while_its_backing_model_supplies_the_limits(fake_provider, tmp_path):
    """An `ep-…` id names a deployment, not a model, so on its own it says nothing.

    It used to be classified from the *caller's question*: the same id claimed video
    when asked about video and images when asked about images, which made "does my
    endpoint support editing?" unanswerable. The binding answers it — `extends` names
    the model behind the id — and the wire keeps the id the API accepts.
    """
    prov, fake = fake_provider(
        "volc-ark/my-endpoint", [{"id": "task-1"}],
        extends="volc-ark/seedance-2.0", model_id=ENDPOINT,
    )
    assert prov.model_id == ENDPOINT
    assert prov.binding.spec.id == "volc-ark/seedance-2.0"

    req = VideoRequest(prompt="a shot", output=tmp_path / "v.mp4", model=prov.model_id,
                       geometry=GeometrySpec(resolution="480p"), wait=False)
    validate_request(req, prov.constraints)
    prov.generate_video(req)
    assert fake.calls[0]["body"]["model"] == ENDPOINT


def test_modality_is_never_guessed_from_a_model_id(fake_provider):
    """A seedance-shaped id on an image binding is still an image binding.

    Nothing reads the name: the binding declares its scenes, so a request that does not
    match is refused rather than reinterpreted.
    """
    from media_ai.core.scene import Scene

    prov, _ = fake_provider("volc-ark/seedream-4.5", [], model_id="doubao-seedance-2-0-260128")
    assert Scene.IMAGE_TEXT_TO_IMAGE in prov.binding.spec.scenes
    assert Scene.VIDEO_TEXT_TO_VIDEO not in prov.binding.spec.scenes


