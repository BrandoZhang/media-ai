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


def test_configured_endpoint_id_is_sent_in_ark_model_field(fake_provider, tmp_path):
    endpoint_id = "ep-20260728-seedream50"
    prov, fake = fake_provider(
        "volc-ark/seedream-5.0", [{"data": [{"b64_json": PNG_1x1}], "usage": {}}], endpoint_id=endpoint_id,
    )
    prov.generate_image(ImageRequest(prompt="dune", output=tmp_path / "o.png"))
    assert prov.model_id == endpoint_id
    assert fake.calls[0]["body"]["model"] == endpoint_id


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


def test_image_response_format_option_is_validated_and_sent(fake_provider, tmp_path):
    prov, fake = fake_provider("volc-ark/seedream-5.0", [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", options={"response_format": "b64_json"}))
    assert fake.calls[0]["body"]["response_format"] == "b64_json"

    with pytest.raises(MediaError) as ei:
        prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "bad.png", options={"response_format": "binary"}))
    assert ei.value.category == ErrorCategory.VALIDATION


def test_streamed_group_response_is_saved_in_image_index_order(fake_provider, tmp_path):
    events = [
        {"type": "image_generation.partial", "image_index": 1, "url": "https://example.com/two.jpg", "model": "m"},
        {"type": "image_generation.partial", "image_index": 0, "url": "https://example.com/one.jpg", "model": "m"},
        {"type": "image_generation.completed", "model": "m", "usage": {"generated_images": 2}},
    ]
    prov, fake = fake_provider("volc-ark/seedream-5.0", [events])
    result = prov.generate_image(ImageRequest(
        prompt="a set", output=tmp_path / "set.jpg", count=2, options={"stream": True},
    ))
    assert fake.calls[0]["sse"] is True
    assert fake.calls[0]["body"]["stream"] is True
    assert fake.calls[0]["body"]["sequential_image_generation"] == "auto"
    assert fake.downloads == ["https://example.com/one.jpg", "https://example.com/two.jpg"]
    assert [artifact.role for artifact in result.artifacts] == [None, "group"]


def test_stream_option_requires_a_boolean(fake_provider, tmp_path):
    prov, _ = fake_provider("volc-ark/seedream-5.0", [])
    with pytest.raises(MediaError) as ei:
        prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "bad.png", options={"stream": "yes"}))
    assert ei.value.category == ErrorCategory.VALIDATION


def test_stream_false_is_sent_as_an_explicit_non_streaming_request(fake_provider, tmp_path):
    prov, fake = fake_provider("volc-ark/seedream-5.0", [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "one.png", options={"stream": False}))
    assert fake.calls[0]["body"]["stream"] is False
    assert "sse" not in fake.calls[0]


def test_image_edit_preserves_ordered_remote_reference_urls(fake_provider, tmp_path):
    refs = [
        MediaRef("https://example.com/person.png", "reference_image"),
        MediaRef("https://example.com/outfit.png", "reference_image"),
    ]
    prov, fake = fake_provider("volc-ark/seedream-5.0-pro", [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    result = prov.generate_image(
        ImageRequest(prompt="put outfit 2 on person 1", output=tmp_path / "o.png", references=refs, output_format="png")
    )
    assert fake.calls[0]["body"]["image"] == [ref.raw for ref in refs]
    assert fake.calls[0]["body"]["output_format"] == "png"
    assert "sequential_image_generation" not in fake.calls[0]["body"]
    assert result.primary().mime == "image/png"


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


@pytest.mark.parametrize("binding", ["volc-ark/seedance-2.0", "volc-ark/seedance-2.0-fast"])
def test_seedance_refuses_negative_prompt_instead_of_dropping_it(binding, tmp_path):
    """Both Seedance bindings declared `negative_prompt` while `_create_task` never read it.

    A `--negative-prompt` call therefore passed validation, reached the API without it,
    and billed a clip reported ok:true that had ignored the request. Ark documents no
    negative-prompt field for Seedance — constraint clauses go in the prompt — so the
    flag is refused rather than silently honoured-in-name.
    """
    prov = adapter_for(binding)
    req = VideoRequest(prompt="a cat", output=tmp_path / "v.mp4",
                       negative_prompt="no text, no watermark", wait=False)
    with pytest.raises(MediaError) as ei:
        validate_request(req, prov.constraints, binding=binding)
    assert ei.value.category is ErrorCategory.UNSUPPORTED
    assert "negative-prompt" in ei.value.message


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
    prov, fake = fake_provider("volc-ark/seedance-2.0", [{"id": "t"}])
    prov.generate_video(VideoRequest(prompt="scene", output=tmp_path / "v.mp4",
                                     reference_images=[MediaRef("https://example.com/i.png", "reference_image")],
                                     reference_videos=[MediaRef("https://example.com/v.mp4", "reference_video")],
                                     reference_audios=[MediaRef("https://example.com/a.mp3", "reference_audio")], wait=False))
    content = fake.calls[0]["body"]["content"]
    assert content[0] == {"type": "text", "text": "scene"}
    roles = [c.get("role") for c in content if "role" in c]
    assert roles == ["reference_image", "reference_video", "reference_audio"]
    assert content[1]["image_url"]["url"] == "https://example.com/i.png"
    assert content[2]["video_url"]["url"] == "https://example.com/v.mp4"
    assert content[3]["audio_url"]["url"] == "https://example.com/a.mp3"


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


def test_fast_deployment_keeps_its_own_backing_model(fake_provider, tmp_path):
    """Seedance 2.0 Fast is a sibling binding, not an alias for Seedance 2.0."""
    prov, fake = fake_provider(
        "volc-ark/my-fast-endpoint", [{"id": "task-fast"}],
        extends="volc-ark/seedance-2.0-fast", model_id="test-fast-deployment",
    )
    assert prov.binding.spec.id == "volc-ark/seedance-2.0-fast"
    prov.generate_video(VideoRequest(prompt="a quick shot", output=tmp_path / "fast.mp4", wait=False))
    assert fake.calls[0]["body"]["model"] == "test-fast-deployment"


def test_modality_is_never_guessed_from_a_model_id(fake_provider):
    """A seedance-shaped id on an image binding is still an image binding.

    Nothing reads the name: the binding declares its scenes, so a request that does not
    match is refused rather than reinterpreted.
    """
    from media_ai.core.scene import Scene

    prov, _ = fake_provider("volc-ark/seedream-4.5", [], model_id="doubao-seedance-2-0-260128")
    assert Scene.IMAGE_TEXT_TO_IMAGE in prov.binding.spec.scenes
    assert Scene.VIDEO_TEXT_TO_VIDEO not in prov.binding.spec.scenes


def test_a_bare_aspect_ratio_is_refused_rather_than_silently_becoming_a_tier(fake_provider, tmp_path):
    """Ark's `size` is pixels or a named tier — there is no field for a bare ratio.

    Falling through to the default tier billed the caller for a 2K square they did not
    ask for: the same silent geometry substitution the video path was fixed for, on the
    image path. An unusable request must not become a charge.
    """
    from media_ai.core.types import GeometrySpec

    prov, fake = fake_provider("volc-ark/seedream-5.0", [{"data": [], "usage": {}}])
    req = ImageRequest(prompt="x", output=tmp_path / "o.png",
                       geometry=GeometrySpec(aspect_ratio="21:9"))
    with pytest.raises(MediaError) as ei:
        prov.generate_image(req)
    assert ei.value.exit_code == 3 and ei.value.code == "geometry_not_expressible"
    assert not fake.calls, "the refusal must happen before the request"


def test_a_named_tier_and_explicit_pixels_still_work(fake_provider, tmp_path):
    from media_ai.core.types import GeometrySpec

    for geo, expected in ((GeometrySpec(resolution="4K"), "4K"),
                          (GeometrySpec(width=2048, height=2048), "2048x2048")):
        prov, fake = fake_provider("volc-ark/seedream-5.0", [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
        prov.generate_image(ImageRequest(prompt="x", output=tmp_path / "o.png", geometry=geo))
        assert fake.calls[0]["body"]["size"] == expected


def test_output_format_is_gated_on_the_declared_formats(fake_provider, tmp_path):
    """An unrequested wire field is what Ark rejects with InvalidParameter — the same
    failure the live run found for `sequential_image_generation`."""
    prov, fake = fake_provider("volc-ark/seedream-5.0", [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="x", output=tmp_path / "o.png", output_format="jpeg"))
    body = fake.calls[0]["body"]
    assert body["output_format"] == "jpeg"  # 5.0 declares formats, so it may receive one
