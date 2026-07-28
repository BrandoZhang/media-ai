"""Network-free tests for the OpenAI adapter (Images API, image-only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import PNG_1x1, PNG_1x1_BYTES
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.types import GeometrySpec, ImageRequest, MediaRef
from conftest import adapter_for


def test_gpt_image_generations_body(fake_provider, tmp_path):
    prov, fake = fake_provider("openai/gpt-image-2", [{"data": [{"b64_json": PNG_1x1}], "usage": {"total_tokens": 40}}])
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


def test_response_format_never_sent(fake_provider, tmp_path):
    # DALL·E is dropped; the Images API rejects `response_format`, so we never send it.
    prov, fake = fake_provider("openai/gpt-image-2", [{"data": [{"b64_json": PNG_1x1}]}])
    prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", model="gpt-image-2",
                                     geometry=GeometrySpec(aspect_ratio="16:9")))
    assert "response_format" not in fake.calls[0]["body"]


def test_gpt_image_2_resolution_tier_maps_to_4k(fake_provider, tmp_path):
    # gpt-image-2 supports arbitrary sizes, so a ratio + tier picks a documented 4K size.
    prov, fake = fake_provider("openai/gpt-image-2", [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", model="gpt-image-2",
                                     geometry=GeometrySpec(aspect_ratio="16:9", resolution="4K")))
    assert fake.calls[0]["body"]["size"] == "3840x2160"


def test_input_fidelity_not_forwarded_to_gpt_image_2(fake_provider, tmp_path):
    # Even if the option slips through (e.g. --on-unsupported ignore), never send it
    # to a model that rejects it.
    prov, fake = fake_provider("openai/gpt-image-2", [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", model="gpt-image-2",
                                     options={"input_fidelity": "high"}))
    assert "input_fidelity" not in fake.calls[0]["body"]


def test_error_mapper_content_policy_is_safety():
    prov = adapter_for("openai/gpt-image-2")
    assert prov._error(400, '{"error":{"code":"content_policy_violation"}}').category == ErrorCategory.SAFETY
    assert prov._error(429, "insufficient_quota").category == ErrorCategory.RATE_LIMIT
    assert prov._error(401, "bad key").category == ErrorCategory.AUTH


def test_error_maps_moderation_details():
    body = json.dumps({"error": {"type": "image_generation_user_error", "code": "moderation_blocked",
                                 "moderation_details": {"moderation_stage": "input", "categories": ["harassment"]}}})
    err = adapter_for("openai/gpt-image-2")._error(400, body)
    assert err.category == ErrorCategory.SAFETY and err.code == "moderation_blocked"
    assert err.retryable is False  # user must change the prompt/inputs
    assert err.details.get("moderation_stage") == "input"
    assert err.details.get("moderation_categories") == ["harassment"]


def test_error_body_non_object_json_maps_to_status_not_unknown():
    # A valid-JSON error body whose top level isn't an object (gateway/proxy quirks)
    # must not crash error mapping into an UNKNOWN fallback — it maps to the status.
    from media_ai.providers.openai import _parse_error

    prov = adapter_for("openai/gpt-image-2")
    for body in ('"unauthorized"', "[1, 2]", "true", "123"):
        assert _parse_error(body) == (None, {}), body  # no AttributeError, degrades cleanly
        err = prov._error(401, body)
        assert isinstance(err, MediaError) and err.category == ErrorCategory.AUTH, body


def test_malformed_aspect_ratio_raises_validation_not_valueerror():
    # A non-numeric ratio must surface as a predictable VALIDATION MediaError, not a
    # raw ValueError from float() that escapes as an UNKNOWN error.
    prov = adapter_for("openai/gpt-image-2")
    for bad in ("foo", "16:9:4", ":", "16:x"):
        req = ImageRequest(prompt="x", output=Path("o.png"), model="gpt-image-2",
                           geometry=GeometrySpec(aspect_ratio=bad))
        with pytest.raises(MediaError) as ei:
            prov._size("gpt-image-2", req)
        assert ei.value.category == ErrorCategory.VALIDATION, bad


def test_artifact_mime_follows_response_output_format(fake_provider, tmp_path):
    # Response says the bytes are webp even though the file is named .png -> mime is authoritative.
    prov, _ = fake_provider("openai/gpt-image-2", [{"data": [{"b64_json": PNG_1x1}], "output_format": "webp",
                                              "size": "1024x1024", "usage": {}}])
    res = prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", model="gpt-image-2"))
    assert res.primary().mime == "image/webp"


def test_meta_size_and_settings_come_from_response(fake_provider, tmp_path):
    # size:"auto" was requested; the response resolves it to a concrete size we surface.
    prov, _ = fake_provider("openai/gpt-image-2", [{"data": [{"b64_json": PNG_1x1}], "size": "1536x1024",
                                              "quality": "low", "output_format": "png", "usage": {}}])
    res = prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", model="gpt-image-2"))
    assert res.meta["size"] == "1536x1024"          # actual, not the requested "auto"
    assert res.meta["quality"] == "low" and res.meta["output_format"] == "png"


def test_usage_ledger_records_input_tokens(fake_provider, tmp_path):
    usage = {"input_tokens": 2048, "output_tokens": 196, "total_tokens": 2244}
    prov, _ = fake_provider("openai/gpt-image-2", [{"data": [{"b64_json": PNG_1x1}], "usage": usage}])
    prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", model="gpt-image-2"))
    entries = [json.loads(x) for x in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert entries[-1]["input_tokens"] == 2048
    assert entries[-1]["output_tokens"] == 196 and entries[-1]["total_tokens"] == 2244


def test_multi_output_groups_extra_artifacts(fake_provider, tmp_path):
    # n>1 -> primary + group artifacts written to _2/_3 siblings.
    prov, _ = fake_provider("openai/gpt-image-2", [{"data": [{"b64_json": PNG_1x1}, {"b64_json": PNG_1x1}], "usage": {}}])
    res = prov.generate_image(ImageRequest(prompt="p", output=tmp_path / "o.png", model="gpt-image-2", count=2))
    assert len(res.artifacts) == 2
    assert res.artifacts[0].role is None and res.artifacts[1].role == "group"
    assert (tmp_path / "o.png").is_file() and (tmp_path / "o_2.png").is_file()


# Per-tier declarations (which sizes, whether transparency works, whether
# input_fidelity exists) live in the manifest now — see bindings/openai.toml, checked
# by tests/test_manifests.py and exercised by tests/test_contract.py. Retired models
# are simply absent from it, so there is no removal error left to assert.


def test_edit_uses_multipart_with_reference_images(fake_provider, tmp_path):
    """Editing posts the references as multipart parts, not as inline base64."""
    ref = tmp_path / "a.png"
    ref.write_bytes(PNG_1x1_BYTES)
    prov, fake = fake_provider("openai/gpt-image-2", [{"data": [{"b64_json": PNG_1x1}], "usage": {}}])
    prov.generate_image(ImageRequest(prompt="make it night", output=tmp_path / "o.png",
                                     references=[MediaRef(str(ref), "reference_image")]))
    call = fake.calls[0]
    assert call["path"] == "/images/edits" and call["multipart"]
    assert [name for name, *_ in call["files"]] == ["image[]"]
