"""Unit tests for the provider-agnostic core: geometry, capability validation,
error taxonomy, result serialization, and the usage ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from media_ai.core import geometry, usage
from media_ai.core.capabilities import (
    GeometryMode,
    ImageCaps,
    ModelCapabilities,
    UnsupportedPolicy,
    VideoCaps,
    validate_request,
)
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.result import Artifact, GenerationResult, JobHandle
from media_ai.core.types import GeometrySpec, ImageRequest, Modality, Operation, VideoRequest


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def test_parse_size_ok_and_bad():
    assert geometry.parse_size("1024x768") == (1024, 768)
    assert geometry.parse_size("64X64") == (64, 64)
    with pytest.raises(MediaError):
        geometry.parse_size("not-a-size")


def test_video_dims_normalizes_case_and_adaptive():
    assert geometry.video_dims("480p", "16:9") == (864, 480)
    assert geometry.video_dims(" 480P ", "16:9") == (864, 480)
    assert geometry.video_dims("480p", "adaptive") == geometry.video_dims("480p", "16:9")
    assert geometry.video_dims("nonsense", "16:9") == geometry.video_dims("720p", "16:9")


def test_resolve_image_pixels_modes():
    assert geometry.resolve_image_pixels(GeometrySpec(width=800, height=600), (1, 1)) == (800, 600)
    w, h = geometry.resolve_image_pixels(GeometrySpec(aspect_ratio="16:9", resolution="1K"), (1, 1))
    assert w > h and w % 2 == 0 and h % 2 == 0


# --------------------------------------------------------------------------
# error taxonomy
# --------------------------------------------------------------------------


def test_error_exit_codes_and_retryable():
    assert MediaError("x", category=ErrorCategory.AUTH).exit_code == 4
    assert MediaError("x", category=ErrorCategory.RATE_LIMIT).exit_code == 5
    assert MediaError("x", category=ErrorCategory.UNSUPPORTED).exit_code == 3
    assert MediaError("x", category=ErrorCategory.SAFETY).exit_code == 8
    assert MediaError("x", category=ErrorCategory.RATE_LIMIT).retryable is True
    assert MediaError("x", category=ErrorCategory.VALIDATION).retryable is False


def test_error_to_dict_shape():
    d = MediaError("boom", category=ErrorCategory.PROVIDER, provider="volc", model="m").to_dict()
    assert d["category"] == "provider" and d["provider"] == "volc" and d["retryable"] is True


# --------------------------------------------------------------------------
# capability validation
# --------------------------------------------------------------------------


def _img_caps(**over) -> ModelCapabilities:
    base = dict(operations=frozenset({Operation.IMAGE_GENERATE}), geometry_mode=GeometryMode.PIXELS,
                max_count=1, supports_seed=False, supports_transparency=False, max_references=0)
    base.update(over)
    return ModelCapabilities(provider="p", model="m", modalities=frozenset({Modality.IMAGE}), image=ImageCaps(**base))


def test_validate_unsupported_seed_raises():
    req = ImageRequest(prompt="x", output=Path("o.png"), seed=5)
    with pytest.raises(MediaError) as ei:
        validate_request(req, _img_caps())
    assert ei.value.category == ErrorCategory.UNSUPPORTED
    assert ei.value.exit_code == 3


def test_validate_transparency_gated():
    req = ImageRequest(prompt="x", output=Path("o.png"), background="transparent")
    with pytest.raises(MediaError):
        validate_request(req, _img_caps(supports_transparency=False))
    # opaque is always fine
    validate_request(ImageRequest(prompt="x", output=Path("o.png"), background="opaque"),
                     _img_caps(supports_transparency=False))


def test_validate_count_and_references():
    with pytest.raises(MediaError):
        validate_request(ImageRequest(prompt="x", output=Path("o.png"), count=5), _img_caps(max_count=1))
    from media_ai.core.types import MediaRef

    with pytest.raises(MediaError):
        validate_request(ImageRequest(prompt="x", output=Path("o.png"), references=[MediaRef("a.png")]),
                         _img_caps(max_references=0))


def test_validate_named_size_tier_gated():
    # An image model with a fixed set of size tiers rejects an out-of-range tier
    # (parity with video resolution validation; capabilities drive pre-flight checks).
    caps = _img_caps(geometry_mode=GeometryMode.ASPECT_RATIO, aspect_ratios=("1:1",), named_sizes=("1K",))
    with pytest.raises(MediaError) as ei:
        validate_request(ImageRequest(prompt="x", output=Path("o.png"),
                                      geometry=GeometrySpec(aspect_ratio="1:1", resolution="4K")), caps)
    assert ei.value.category == ErrorCategory.UNSUPPORTED and "4K" in ei.value.message
    # a supported tier passes
    validate_request(ImageRequest(prompt="x", output=Path("o.png"),
                                  geometry=GeometrySpec(aspect_ratio="1:1", resolution="1K")), caps)


def test_validate_unknown_option_rejected_but_warn_mode_passes():
    req = ImageRequest(prompt="x", output=Path("o.png"), options={"bogus": 1})
    caps = _img_caps(options=("moderation",))
    with pytest.raises(MediaError):
        validate_request(req, caps, UnsupportedPolicy.ERROR)
    warnings = validate_request(req, caps, UnsupportedPolicy.WARN)
    assert warnings and "bogus" in warnings[0]
    assert validate_request(req, caps, UnsupportedPolicy.IGNORE) == []


def test_validate_video_duration_and_refs():
    caps = ModelCapabilities(provider="p", model="m", modalities=frozenset({Modality.VIDEO}),
                             video=VideoCaps(durations=(4, 8), supports_first_frame=False))
    with pytest.raises(MediaError):
        validate_request(VideoRequest(prompt="x", output=Path("o.mp4"), duration=5), caps)
    from media_ai.core.types import MediaRef

    with pytest.raises(MediaError):
        validate_request(VideoRequest(prompt="x", output=Path("o.mp4"), first_frame=MediaRef("f.png")), caps)


def test_capabilities_to_dict_is_json_serializable():
    d = _img_caps(options=("moderation",)).to_dict()
    json.dumps(d)  # must not raise
    assert d["image"]["operations"] == ["image.generate"]
    assert d["modalities"] == ["image"]


# --------------------------------------------------------------------------
# result serialization
# --------------------------------------------------------------------------


def test_generation_result_contract_keys(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"12345")
    r = GenerationResult(modality="image", operation="image.generate", provider="mock", model="mock",
                         artifacts=[Artifact.from_path(p, "image", mime="image/png")], usage={"total_tokens": 3})
    d = r.to_dict()
    assert d["ok"] and d["schema_version"] >= 1
    assert d["path"] == str(p) and d["bytes"] == 5  # compat aliases
    assert d["artifacts"][0]["mime"] == "image/png"


def test_job_handle_has_poll_hint():
    d = JobHandle(provider="volc", model="m", id="task-1", output="/o.mp4").to_dict()
    assert d["status"] == "queued" and d["job"]["id"] == "task-1"
    assert "media-ai job query" in d["poll"] and d["task_id"] == "task-1"


# --------------------------------------------------------------------------
# usage ledger
# --------------------------------------------------------------------------


def test_record_and_summarize(_ledger):
    usage.record_usage({"operation": "image.generate", "provider": "mock", "generated_images": 2, "total_tokens": 100})
    usage.record_usage({"operation": "video.generate", "provider": "mock", "seconds": 3, "total_tokens": 50})
    usage.record_usage({"operation": "speech.generate", "provider": "gemini", "characters": 42, "total_tokens": 20})
    totals = usage.summarize_usage()
    assert totals["calls"] == 3 and totals["total_tokens"] == 170
    assert totals["images_generated"] == 2 and totals["video_seconds"] == 3
    assert totals["speech_characters"] == 42
    assert totals["by_provider"] == {"mock": 150, "gemini": 20}
    assert totals["by_tool"]["speech.generate"] == 20


def test_summarize_tolerates_legacy_backend_key(tmp_path, monkeypatch):
    log = tmp_path / "u.jsonl"
    log.write_text(json.dumps({"tool": "image.generate", "backend": "volc", "total_tokens": 7}) + "\n")
    monkeypatch.setenv("MEDIA_USAGE_LOG", str(log))
    assert usage.summarize_usage()["by_provider"] == {"volc": 7}


def test_record_usage_never_raises(monkeypatch):
    monkeypatch.setenv("MEDIA_USAGE_LOG", "/proc/definitely/not/writable/u.jsonl")
    usage.record_usage({"total_tokens": 1})  # must not raise


def test_parse_options_coerces_bool_int_float():
    from media_ai.cli.common import parse_options
    o = parse_options(["a=true", "b=3", "c=7.5", "d=hello", "e=off", "f=-2"])
    assert o == {"a": True, "b": 3, "c": 7.5, "d": "hello", "e": False, "f": -2}
