"""Network-free tests for the Gemini adapter (generateContent + Veo + TTS)."""

from __future__ import annotations

import base64
import urllib.error
import wave
from pathlib import Path

import pytest
from conftest import PNG_1x1, PNG_1x1_BYTES
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.types import (
    DialogueRequest,
    DialogueTurn,
    GeometrySpec,
    ImageRequest,
    JobRef,
    MediaRef,
    SpeechRequest,
    VideoRequest,
)
from conftest import adapter_for

_PCM_B64 = base64.b64encode(b"\x00\x01" * 240).decode()  # 240 headerless PCM frames
_AUDIO_MIME = "audio/L16;codec=pcm;rate=24000"


def _audio_resp(data=_PCM_B64, mime=_AUDIO_MIME):
    return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": mime, "data": data}}]}}],
            "usageMetadata": {"totalTokenCount": 55}}


def test_native_generatecontent_body_and_parse(fake_provider, tmp_path):
    resp = {"candidates": [{"content": {"parts": [{"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": PNG_1x1}}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"candidatesTokenCount": 1290, "totalTokenCount": 1302}}
    prov, fake = fake_provider("gemini/nano-banana-2", [resp])
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
    prov, fake = fake_provider("gemini/nano-banana-2", [resp])
    prov.generate_image(ImageRequest(prompt="edit", output=tmp_path / "o.png", model="gemini-2.5-flash-image",
                                     references=[MediaRef(str(ref), "reference_image")]))
    parts = fake.calls[0]["body"]["contents"][0]["parts"]
    assert any("inlineData" in p for p in parts)


def test_200_but_no_image_is_safety_error(fake_provider, tmp_path):
    resp = {"candidates": [{"content": {"parts": [{"text": "I can't create that."}]}, "finishReason": "IMAGE_SAFETY"}]}
    prov, _ = fake_provider("gemini/nano-banana-2", [resp])
    with pytest.raises(MediaError) as ei:
        prov.generate_image(ImageRequest(prompt="x", output=tmp_path / "o.png", model="gemini-2.5-flash-image"))
    assert ei.value.category == ErrorCategory.SAFETY
    assert not (tmp_path / "o.png").exists()  # no empty file written


def test_prompt_block_is_safety_error(fake_provider, tmp_path):
    prov, _ = fake_provider("gemini/nano-banana-2", [{"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}])
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
    prov, fake = fake_provider("gemini/veo-3.1", responses)
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
    prov, fake = fake_provider("gemini/veo-3.1", [
        {"name": "op"},  # create
        {"name": "op", "done": True,  # poll -> done
         "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://x/files/Y:download"}}]}}}], options={"poll_interval": 0})
    prov.generate_video(VideoRequest(prompt="move", output=tmp_path / "v.mp4", model="veo-3.1-generate-preview",
                                     first_frame=MediaRef(str(ff), "first_frame"), duration=8, wait=True))
    instance = fake.calls[0]["body"]["instances"][0]
    assert "image" in instance and instance["image"]["mimeType"] == "image/png"


def test_veo_cancel_is_unsupported():
    with pytest.raises(MediaError) as ei:
        adapter_for("gemini/veo-3.1").cancel_job(JobRef(provider="gemini", id="op"))
    assert ei.value.category == ErrorCategory.UNSUPPORTED


def test_veo_get_job_failed_operation_raises(fake_provider):
    prov, _ = fake_provider("gemini/veo-3.1", [{"name": "op", "done": True, "error": {"code": 13, "message": "boom"}}])
    with pytest.raises(MediaError) as ei:
        prov.get_job(JobRef(provider="gemini", id="op"))
    assert ei.value.category == ErrorCategory.PROVIDER


# ---- new image features (grounding / thinking / thought-filtering) --------


def _one_image():
    return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": PNG_1x1}}]}}]}


def test_native_grounding_adds_google_search_tool(fake_provider, tmp_path):
    prov, fake = fake_provider("gemini/nano-banana-2", [_one_image()])
    prov.generate_image(ImageRequest(prompt="last night's match", output=tmp_path / "o.png",
                                     model="gemini-3.1-flash-image", options={"grounding": True}))
    assert fake.calls[0]["body"]["tools"] == [{"google_search": {}}]


def test_native_no_tools_without_grounding(fake_provider, tmp_path):
    prov, fake = fake_provider("gemini/nano-banana-2", [_one_image()])
    prov.generate_image(ImageRequest(prompt="a fox", output=tmp_path / "o.png", model="gemini-3.1-flash-image"))
    assert "tools" not in fake.calls[0]["body"]


def test_native_skips_thought_images(fake_provider, tmp_path):
    # a thinking model may emit interim "thought" images; only the final one counts
    resp = {"candidates": [{"content": {"parts": [
        {"thought": True, "inlineData": {"mimeType": "image/png", "data": PNG_1x1}},
        {"inlineData": {"mimeType": "image/png", "data": PNG_1x1}},
    ]}}]}
    prov, _ = fake_provider("gemini/nano-banana-2", [resp])
    res = prov.generate_image(ImageRequest(prompt="x", output=tmp_path / "o.png", model="gemini-3.1-flash-image"))
    assert len(res.artifacts) == 1  # the thought image is not saved as an artifact


def test_native_thinking_level_option(fake_provider, tmp_path):
    prov, fake = fake_provider("gemini/nano-banana-2", [_one_image()])
    prov.generate_image(ImageRequest(prompt="a glass city", output=tmp_path / "o.png",
                                     model="gemini-3.1-flash-image", options={"thinking_level": "high"}))
    assert fake.calls[0]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}


def test_native_captures_model_version_response_id_text_and_grounding(fake_provider, tmp_path):
    resp = {"candidates": [{"content": {"parts": [
                {"text": "Here is your image."},
                {"inlineData": {"mimeType": "image/png", "data": PNG_1x1}},
                {"thought": True, "text": "internal reasoning"}]},
             "groundingMetadata": {"searchEntryPoint": {"renderedContent": "<div>suggestions</div>"}}}],
            "modelVersion": "gemini-3.1-flash-image-001", "responseId": "RESP123"}
    prov, _ = fake_provider("gemini/nano-banana-2", [resp])
    res = prov.generate_image(ImageRequest(prompt="x", output=tmp_path / "o.png",
                                           model="gemini-3.1-flash-image", options={"grounding": True}))
    assert res.model == "gemini-3.1-flash-image-001"          # resolved modelVersion, not the requested id
    assert res.meta["response_id"] == "RESP123"
    assert res.meta["text"] == "Here is your image."           # the thought text is excluded
    assert res.meta["grounding"]["searchEntryPoint"]["renderedContent"] == "<div>suggestions</div>"


def test_native_large_reference_uploads_via_files_api(fake_provider, tmp_path, monkeypatch):
    import media_ai.providers.gemini as gem
    seen = {}

    def fake_upload(base_url, headers, data, mime, *, display_name="input", **kw):
        seen.update(base_url=base_url, mime=mime, size=len(data), name=display_name)
        return "https://generativelanguage.googleapis.com/v1beta/files/UP:download"

    monkeypatch.setattr(gem._gemini_files, "upload_bytes", fake_upload)
    small = tmp_path / "small.png"
    small.write_bytes(PNG_1x1_BYTES)
    big = tmp_path / "big.png"
    big.write_bytes(PNG_1x1_BYTES * 4000)  # ~large
    # The inline ceiling is a per-binding option: an endpoint behind a proxy with a
    # smaller body limit says so without changing anyone else's.
    prov, fake = fake_provider("gemini/nano-banana-2", [_one_image()], options={"inline_max_bytes": 500})
    res = prov.generate_image(ImageRequest(prompt="compose", output=tmp_path / "o.png",
                                           model="gemini-3.1-flash-image",
                                           references=[MediaRef(str(small), "reference_image"),
                                                       MediaRef(str(big), "reference_image")]))
    parts = fake.calls[0]["body"]["contents"][0]["parts"]
    assert any("inlineData" in p for p in parts)  # small stayed inline
    fd = [p for p in parts if "fileData" in p]
    assert fd and fd[0]["fileData"] == {"mimeType": "image/png",
                                        "fileUri": "https://generativelanguage.googleapis.com/v1beta/files/UP:download"}
    assert res.meta.get("uploaded_refs") == 1 and seen["size"] == big.stat().st_size


def test_veo_oversized_inline_media_is_rejected(fake_provider, tmp_path, monkeypatch):
    # Veo image inputs are inline-only (the API rejects file URIs), so oversized
    # media still fails fast with a clear error rather than silently truncating.
    import media_ai.providers.gemini as gem
    monkeypatch.setattr(gem, "_INLINE_LIMIT", 8)  # bytes
    big = tmp_path / "big.png"
    big.write_bytes(b"x" * 64)
    prov, _ = fake_provider("gemini/veo-3.1", [{"name": "op"}])
    with pytest.raises(MediaError) as ei:
        prov.generate_video(VideoRequest(prompt="move", output=tmp_path / "v.mp4",
                                         model="veo-3.1-generate-preview",
                                         first_frame=MediaRef(str(big), "first_frame"), duration=8))
    assert ei.value.category == ErrorCategory.VALIDATION and "inline" in ei.value.message.lower()


def test_gemini_files_endpoint_derivation():
    from media_ai.providers._gemini_files import _files_endpoint
    assert _files_endpoint("https://generativelanguage.googleapis.com/v1beta") == \
        "https://generativelanguage.googleapis.com/upload/v1beta/files"


def test_a_brokered_binding_refuses_the_upload_instead_of_sending_a_keyless_request(tmp_path, monkeypatch):
    """The resumable upload cannot be brokered, so a brokered binding must say so.

    ``_prepare`` hands a brokered call only a session token and the upstream to forward
    to — no ``x-goog-api-key`` — while the Files API is a separate Google endpoint the
    broker does not forward. Uploading anyway is a guaranteed 401 that blames the key.
    """
    import media_ai.providers.gemini as gem
    from conftest import FakeClient, adapter_for

    monkeypatch.setattr(gem._gemini_files, "upload_bytes",
                        lambda *a, **k: pytest.fail("a brokered binding must not reach the Files API"))
    prov = adapter_for("gemini/nano-banana-2", credential="broker://broker.test",
                       options={"inline_max_bytes": 500})
    _, headers = prov._auth(prov.credential())  # the real brokered headers, not a stand-in
    assert prov.brokered(headers) and not any(h.lower() == "x-goog-api-key" for h in headers)
    monkeypatch.setattr(prov, "_prepare", lambda **kw: (FakeClient([_one_image()]), headers))

    big = tmp_path / "big.png"
    big.write_bytes(PNG_1x1_BYTES * 4000)
    with pytest.raises(MediaError) as ei:
        prov.generate_image(ImageRequest(prompt="compose", output=tmp_path / "o.png",
                                        model="gemini-3.1-flash-image",
                                        references=[MediaRef(str(big), "reference_image")]))
    assert ei.value.category is ErrorCategory.UNSUPPORTED and ei.value.code == "broker_upload_unsupported"
    assert "GEMINI_API_KEY" in ei.value.hint  # the fix: a direct key, or a smaller reference


def test_a_direct_key_still_uploads(tmp_path, monkeypatch):
    """The gate is about the broker alone — an ordinary key path is untouched."""
    import media_ai.providers.gemini as gem
    from conftest import FakeClient, adapter_for

    monkeypatch.setenv("MEDIA_TEST_KEY", "secret-key")
    monkeypatch.setattr(gem._gemini_files, "upload_bytes", lambda *a, **k: "files/UP")
    prov = adapter_for("gemini/nano-banana-2", options={"inline_max_bytes": 500})
    _, headers = prov._auth(prov.credential())
    assert not prov.brokered(headers)
    monkeypatch.setattr(prov, "_prepare", lambda **kw: (FakeClient([_one_image()]), headers))

    big = tmp_path / "big.png"
    big.write_bytes(PNG_1x1_BYTES * 4000)
    res = prov.generate_image(ImageRequest(prompt="compose", output=tmp_path / "o.png",
                                           model="gemini-3.1-flash-image",
                                           references=[MediaRef(str(big), "reference_image")]))
    assert res.meta["uploaded_refs"] == 1


@pytest.mark.parametrize("exc", [
    TimeoutError("timed out"),                                 # urlopen raises the deadline directly…
    urllib.error.URLError(TimeoutError("timed out")),           # …or wraps it in a URLError
])
def test_files_api_timeout_is_a_timeout_not_a_provider_fault(monkeypatch, exc):
    """Exit 7 tells the caller to raise the deadline or shrink the input; exit 6 sends
    them looking for an upstream outage that isn't there."""
    import urllib.request

    from media_ai.providers import _gemini_files

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(exc))
    with pytest.raises(MediaError) as ei:
        _gemini_files.upload_bytes("https://generativelanguage.googleapis.com/v1beta",
                                   {"x-goog-api-key": "k"}, b"bytes", "image/png")
    assert ei.value.category is ErrorCategory.TIMEOUT and ei.value.exit_code == 7


def test_files_api_transport_failure_stays_a_provider_error(monkeypatch):
    import urllib.request

    from media_ai.providers import _gemini_files

    err = urllib.error.URLError(OSError("connection reset"))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(err))
    with pytest.raises(MediaError) as ei:
        _gemini_files.upload_bytes("https://generativelanguage.googleapis.com/v1beta",
                                   {"x-goog-api-key": "k"}, b"bytes", "image/png")
    assert ei.value.category is ErrorCategory.PROVIDER


# ---- new video features (reference images / seed / extension) ------------


def test_veo_reference_images_and_seed(fake_provider, tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    for p in (a, b):
        p.write_bytes(PNG_1x1_BYTES)
    prov, fake = fake_provider("gemini/veo-3.1", [
        {"name": "op"},
        {"name": "op", "done": True,
         "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://x/files/Z:download"}}]}}}], options={"poll_interval": 0})
    prov.generate_video(VideoRequest(prompt="walk", output=tmp_path / "v.mp4", model="veo-3.1-generate-preview",
                                     reference_images=[MediaRef(str(a), "reference_image"),
                                                       MediaRef(str(b), "reference_image")],
                                     seed=7, duration=8))
    body = fake.calls[0]["body"]
    refs = body["instances"][0]["referenceImages"]
    assert len(refs) == 2 and refs[0]["referenceType"] == "asset"
    assert refs[0]["image"]["mimeType"] == "image/png"
    assert body["parameters"]["seed"] == 7


def test_veo_extension_uses_video_uri(fake_provider, tmp_path):
    # extension continues a prior Veo clip referenced by URI (the API rejects inline video)
    uri = "https://generativelanguage.googleapis.com/v1beta/files/abc:download?alt=media"
    prov, fake = fake_provider("gemini/veo-3.1", [
        {"name": "op"},
        {"name": "op", "done": True,
         "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://x/files/E:download"}}]}}}], options={"poll_interval": 0})
    prov.generate_video(VideoRequest(prompt="continue", output=tmp_path / "v.mp4", model="veo-3.1-generate-preview",
                                     reference_videos=[MediaRef(uri, "reference_video")], duration=8))
    assert fake.calls[0]["body"]["instances"][0]["video"] == {"uri": uri, "mimeType": "video/mp4"}


def test_veo_extension_local_file_is_rejected(fake_provider, tmp_path):
    clip = tmp_path / "src.mp4"
    clip.write_bytes(b"FAKE-MP4")
    prov, _ = fake_provider("gemini/veo-3.1", [{"name": "op"}])
    with pytest.raises(MediaError) as ei:
        prov.generate_video(VideoRequest(prompt="continue", output=tmp_path / "v.mp4",
                                         model="veo-3.1-generate-preview",
                                         reference_videos=[MediaRef(str(clip), "reference_video")], duration=8))
    assert ei.value.category == ErrorCategory.VALIDATION and "uri" in ei.value.message.lower()


def _done_video(uri="https://x/files/S:download"):
    return {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": uri}}]}}


def test_veo_seconds_uses_true_probed_output_length(fake_provider, tmp_path, monkeypatch):
    # The ledger bills the TRUE output length, not the request: an extension whose
    # combined clip is 11s must record 11 even though only 8s was requested.
    from media_ai.core.usage import usage_log_path
    import media_ai.providers.gemini as gem
    monkeypatch.setattr(gem.ffmpeg, "probe_duration", lambda p: 11.0)
    prov, _ = fake_provider("gemini/veo-3.1", [
        {"name": "op"}, {"name": "op", "done": True, "response": _done_video()}], options={"poll_interval": 0})
    res = prov.generate_video(VideoRequest(prompt="x", output=tmp_path / "v.mp4",
                                           model="veo-3.1-generate-preview", duration=8, wait=True))
    assert res.meta["seconds"] == 11  # probed output length wins over the requested 8
    videos = [json.loads(ln) for ln in usage_log_path().read_text().splitlines() if ln.strip()]
    videos = [e for e in videos if e.get("kind") == "video"]
    assert videos and videos[-1]["seconds"] == 11


def test_veo_seconds_probed_on_async_job_query(fake_provider, tmp_path, monkeypatch):
    # async `job query` never knew a requested duration -> the probe is the only source
    import media_ai.providers.gemini as gem
    monkeypatch.setattr(gem.ffmpeg, "probe_duration", lambda p: 4.0)
    prov, _ = fake_provider("gemini/veo-3.1", [{"name": "op", "done": True, "response": _done_video()}])
    st = prov.get_job(JobRef(provider="gemini", id="op"), output=tmp_path / "v.mp4")
    assert st.result is not None and st.result.meta["seconds"] == 4


def test_veo_seconds_falls_back_to_requested_when_probe_fails(fake_provider, tmp_path, monkeypatch):
    # if ffmpeg can't read the clip (probe -> 0), fall back to the requested duration
    import media_ai.providers.gemini as gem
    monkeypatch.setattr(gem.ffmpeg, "probe_duration", lambda p: 0.0)
    prov, _ = fake_provider("gemini/veo-3.1", [
        {"name": "op"}, {"name": "op", "done": True, "response": _done_video()}], options={"poll_interval": 0})
    res = prov.generate_video(VideoRequest(prompt="x", output=tmp_path / "v.mp4",
                                           model="veo-3.1-generate-preview", duration=6, wait=True))
    assert res.meta["seconds"] == 6


# ---- capabilities: current Nano Banana + Veo 3.1 lineup ------------------


# ---- structured error classification (troubleshooting guide) -------------

import json  # noqa: E402


def _err_body(code, status, message="boom"):
    return json.dumps({"error": {"code": code, "status": status, "message": message}})


def test_error_maps_google_status_strings():
    prov = adapter_for("gemini/nano-banana-2")
    cases = {
        (400, "INVALID_ARGUMENT"): ErrorCategory.VALIDATION,
        (400, "FAILED_PRECONDITION"): ErrorCategory.VALIDATION,
        (403, "PERMISSION_DENIED"): ErrorCategory.AUTH,
        (404, "NOT_FOUND"): ErrorCategory.NOT_FOUND,
        (429, "RESOURCE_EXHAUSTED"): ErrorCategory.RATE_LIMIT,
        (504, "DEADLINE_EXCEEDED"): ErrorCategory.TIMEOUT,
        (500, "INTERNAL"): ErrorCategory.PROVIDER,
        (503, "UNAVAILABLE"): ErrorCategory.PROVIDER,
    }
    for (code, status), expected in cases.items():
        err = prov._error(code, _err_body(code, status))
        assert err.category == expected, (status, err.category)
        assert err.details.get("google_status") == status


def test_error_failed_precondition_has_billing_hint():
    err = adapter_for("gemini/nano-banana-2")._error(400, _err_body(400, "FAILED_PRECONDITION", "free tier not available"))
    assert err.category == ErrorCategory.VALIDATION and "billing" in err.message.lower()


def test_error_leaked_key_is_auth_with_hint():
    body = _err_body(403, "PERMISSION_DENIED", "Your API key was reported as leaked. Please use another API key.")
    err = adapter_for("gemini/nano-banana-2")._error(403, body)
    assert err.category == ErrorCategory.AUTH and "leaked" in err.message.lower()


def test_error_invalid_argument_with_safety_word_stays_validation():
    # a malformed request that merely mentions "safety" must not be miscast as a
    # content-safety block — the authoritative INVALID_ARGUMENT status wins.
    err = adapter_for("gemini/nano-banana-2")._error(400, _err_body(400, "INVALID_ARGUMENT", "Invalid value at 'safetySettings'"))
    assert err.category == ErrorCategory.VALIDATION


def test_retry_classifier_vetoes_daily_cap_but_allows_rpm():
    prov = adapter_for("gemini/nano-banana-2")
    assert prov.retry_classifier(429, '{"error":{"message":"Quota exceeded: requests per day"}}') is False
    assert prov.retry_classifier(429, '{"error":{"message":"Quota exceeded per_minute"}}') is True
    assert prov.retry_classifier(503, "overloaded") is True  # non-429 untouched


def test_error_falls_back_to_http_status_for_unstructured_body():
    # 504 with a plain-text (non-JSON) body still classifies as a timeout
    err = adapter_for("gemini/nano-banana-2")._error(504, "upstream timed out")
    assert err.category == ErrorCategory.TIMEOUT
    # unknown status with no parseable body -> provider
    assert adapter_for("gemini/nano-banana-2")._error(418, "teapot").category == ErrorCategory.PROVIDER


def test_veo_operation_error_classifies_by_grpc_code(fake_provider):
    # gRPC 8 = RESOURCE_EXHAUSTED -> rate_limit (was a generic provider error before)
    prov, _ = fake_provider("gemini/veo-3.1", [
        {"name": "op", "done": True, "error": {"code": 8, "message": "quota exceeded"}}])
    with pytest.raises(MediaError) as ei:
        prov.get_job(JobRef(provider="gemini", id="op"))
    assert ei.value.category == ErrorCategory.RATE_LIMIT
    assert ei.value.details.get("google_status") == "RESOURCE_EXHAUSTED"


def test_veo_operation_error_detects_safety(fake_provider):
    prov, _ = fake_provider("gemini/veo-3.1", [
        {"name": "op", "done": True, "error": {"code": 3, "message": "the request was blocked by safety filters"}}])
    with pytest.raises(MediaError) as ei:
        prov.get_job(JobRef(provider="gemini", id="op"))
    assert ei.value.category == ErrorCategory.SAFETY


# ---- TTS -----------------------------------------------------------------

def _valid_wav(path, expect_rate=24000) -> bool:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() > 0 and w.getnchannels() == 1 and w.getframerate() == expect_rate


def test_tts_single_speaker_body_and_wav(fake_provider, tmp_path):
    prov, fake = fake_provider("gemini/gemini-tts", [_audio_resp()])
    req = SpeechRequest(text="Say cheerfully: Have a wonderful day!", output=tmp_path / "o.wav",
                        model="gemini-2.5-flash-preview-tts", voice="Kore")
    res = prov.generate_speech(req)
    call = fake.calls[0]
    assert call["path"] == "/models/gemini-2.5-flash-preview-tts:generateContent"
    gc = call["body"]["generationConfig"]
    assert gc["responseModalities"] == ["AUDIO"]  # NOT ["TEXT","AUDIO"] — TTS 400s on TEXT
    assert gc["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    assert call["body"]["contents"][0]["parts"][0]["text"].startswith("Say cheerfully")
    assert _valid_wav(res.primary().path) and res.primary().mime == "audio/wav"
    assert res.modality == "audio"


def test_tts_pcm_rate_parsed_from_mime(fake_provider, tmp_path):
    prov, _ = fake_provider("gemini/gemini-tts", [_audio_resp(mime="audio/L16;codec=pcm;rate=16000")])
    res = prov.generate_speech(SpeechRequest(text="hi", output=tmp_path / "o.wav",
                                             model="gemini-2.5-flash-preview-tts"))
    assert _valid_wav(res.primary().path, expect_rate=16000)


def test_tts_multi_speaker_body_and_prompt(fake_provider, tmp_path):
    prov, fake = fake_provider("gemini/gemini-tts", [_audio_resp()])
    req = DialogueRequest(
        turns=[DialogueTurn("Joe", "How's it going?"), DialogueTurn("Jane", "Not bad!")],
        cast={"Joe": "Kore", "Jane": "Puck"}, instruction="TTS this conversation:",
        output=tmp_path / "d.wav", model="gemini-2.5-flash-preview-tts",
    )
    res = prov.generate_dialogue(req)
    body = fake.calls[0]["body"]
    cfgs = body["generationConfig"]["speechConfig"]["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"]
    assert cfgs == [{"speaker": "Joe", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}},
                    {"speaker": "Jane", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Puck"}}}]
    prompt = body["contents"][0]["parts"][0]["text"]
    assert prompt == "TTS this conversation:\n\nJoe: How's it going?\nJane: Not bad!"
    assert _valid_wav(res.primary().path)


def test_tts_200_but_no_audio_is_safety(fake_provider, tmp_path):
    resp = {"candidates": [{"content": {"parts": [{"text": "instructions read aloud..."}]},
                            "finishReason": "PROHIBITED_CONTENT"}]}
    prov, _ = fake_provider("gemini/gemini-tts", [resp])
    with pytest.raises(MediaError) as ei:
        prov.generate_speech(SpeechRequest(text="x", output=tmp_path / "o.wav",
                                           model="gemini-2.5-flash-preview-tts"))
    assert ei.value.category == ErrorCategory.SAFETY
    assert not (tmp_path / "o.wav").exists()




# Per-tier capability tables (which ratios, how many references, which resolutions)
# moved to the binding manifests, where discovery and validation both read them.
# tests/test_manifests.py checks they are coherent; tests/test_contract.py checks each
# declared scene is actually usable. Asserting them here again would be a second copy
# of the same data with nothing keeping the two in step.
