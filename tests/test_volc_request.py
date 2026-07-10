"""Network-free tests for the Volcengine Ark backend.

We never hit the real API: request *body* construction is checked by capturing
what the backend hands to ``_request``, and the retry/idempotency policy is
checked by faking ``urllib.request.urlopen``. This locks in two things that are
easy to regress and expensive to catch in production:

* the multimodal ``content`` array (roles, model passthrough, optional fields);
* that a non-idempotent POST (create video task) is **not** retried on a
  transient 5xx / network error (which could double-submit a billed task),
  while 429 and idempotent GET/DELETE are.
"""

from __future__ import annotations

import base64
import io
import urllib.error

import pytest
from media_ai import mediakit

# a 1x1 transparent PNG, so _save_image_item can write bytes without a network fetch
_PNG_1x1 = base64.b64encode(
    base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
).decode()


@pytest.fixture
def volc(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("MEDIA_USAGE_LOG", "/dev/null")
    monkeypatch.delenv("ARK_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("ARK_VIDEO_MODEL", raising=False)
    return mediakit.VolcBackend()


def _capture(monkeypatch, backend, response):
    """Replace ``backend._request`` with a recorder returning ``response``."""
    calls = []

    def fake_request(method, path, body=None):
        calls.append({"method": method, "path": path, "body": body})
        return response

    monkeypatch.setattr(backend, "_request", fake_request)
    return calls


# --------------------------------------------------------------------------
# image body construction
# --------------------------------------------------------------------------


def test_text2image_body_uses_model_and_size(volc, monkeypatch, tmp_path):
    calls = _capture(monkeypatch, volc, {"data": [{"b64_json": _PNG_1x1}], "usage": {"total_tokens": 7}, "model": "m"})
    res = volc.text2image(
        prompt="dune", out=tmp_path / "o.png", width=768, height=432, seed=1, model="doubao-seedream-5-0-260128"
    )
    body = calls[0]["body"]
    assert calls[0]["method"] == "POST" and calls[0]["path"] == "/images/generations"
    assert body["model"] == "doubao-seedream-5-0-260128"
    assert body["size"] == "2K"  # 768x432 below floor -> named preset
    assert body["sequential_image_generation"] == "disabled"
    assert res.path.is_file()


def test_text2image_group_sets_sequential_options(volc, monkeypatch, tmp_path):
    calls = _capture(
        monkeypatch,
        volc,
        {"data": [{"b64_json": _PNG_1x1}, {"b64_json": _PNG_1x1}], "usage": {}, "model": "m"},
    )
    volc.text2image(prompt="team", out=tmp_path / "o.png", width=768, height=432, seed=1, max_images=2)
    body = calls[0]["body"]
    assert body["sequential_image_generation"] == "auto"
    assert body["sequential_image_generation_options"] == {"max_images": 2}


def test_text2image_defaults_to_configured_model(volc, monkeypatch, tmp_path):
    calls = _capture(monkeypatch, volc, {"data": [{"b64_json": _PNG_1x1}], "usage": {}})
    volc.text2image(prompt="p", out=tmp_path / "o.png", width=768, height=432, seed=1)
    assert calls[0]["body"]["model"] == volc.image_model  # built-in default


def test_image_response_without_images_raises(volc, monkeypatch, tmp_path):
    _capture(monkeypatch, volc, {"data": [], "usage": {}})
    with pytest.raises(mediakit.MediaError):
        volc.text2image(prompt="p", out=tmp_path / "o.png", width=768, height=432, seed=1)


def test_image_seed_is_forwarded(volc, monkeypatch, tmp_path):
    calls = _capture(monkeypatch, volc, {"data": [{"b64_json": _PNG_1x1}], "usage": {}})
    volc.text2image(prompt="p", out=tmp_path / "o.png", width=768, height=432, seed=42)
    assert calls[0]["body"]["seed"] == 42  # reproducibility promise honored


def test_image_seed_omitted_when_negative(volc, monkeypatch, tmp_path):
    calls = _capture(monkeypatch, volc, {"data": [{"b64_json": _PNG_1x1}], "usage": {}})
    volc.image2image(images=[], prompt="p", out=tmp_path / "o.png", strength=0.5, seed=-1)
    assert "seed" not in calls[0]["body"]


# --------------------------------------------------------------------------
# video content construction
# --------------------------------------------------------------------------


def test_image2video_content_roles(volc, monkeypatch, tmp_path):
    ff = tmp_path / "ff.png"
    lf = tmp_path / "lf.png"
    ff.write_bytes(base64.b64decode(_PNG_1x1))
    lf.write_bytes(base64.b64decode(_PNG_1x1))
    calls = _capture(monkeypatch, volc, {"id": "task-1"})
    res = volc.image2video(
        prompt="turns",
        first_frame=ff,
        last_frame=lf,
        out=tmp_path / "v.mp4",
        seconds=3,
        resolution="480p",
        ratio="adaptive",
        seed=5,
        camera_fixed=True,
        watermark=False,
        generate_audio=True,
        return_last_frame=True,
        wait=False,  # don't poll
        model="doubao-seedance-2-0-260128",
    )
    body = calls[0]["body"]
    roles = [c.get("role") for c in body["content"] if "role" in c]
    assert roles == ["first_frame", "last_frame"]
    assert any(c.get("type") == "text" for c in body["content"])
    assert body["model"] == "doubao-seedance-2-0-260128"
    assert body["camera_fixed"] is True
    assert body["generate_audio"] is True
    assert body["return_last_frame"] is True
    assert body["seed"] == 5
    # wait=False -> submitted (queued) descriptor, not a downloaded clip
    assert res["status"] == "queued" and res["task_id"] == "task-1"


def test_create_task_omits_seed_and_audio_when_unset(volc, monkeypatch, tmp_path):
    ff = tmp_path / "ff.png"
    ff.write_bytes(base64.b64decode(_PNG_1x1))
    calls = _capture(monkeypatch, volc, {"id": "t"})
    volc.image2video(
        prompt="",
        first_frame=ff,
        last_frame=None,
        out=tmp_path / "v.mp4",
        seconds=2,
        resolution="480p",
        ratio="adaptive",
        seed=-1,  # negative -> omitted
        camera_fixed=False,
        watermark=False,
        generate_audio=None,  # None -> omitted
        return_last_frame=False,
        wait=False,
    )
    body = calls[0]["body"]
    assert "seed" not in body
    assert "generate_audio" not in body
    assert "return_last_frame" not in body


def test_ref2video_rejects_empty_references(volc, monkeypatch, tmp_path):
    _capture(monkeypatch, volc, {"id": "t"})
    with pytest.raises(mediakit.MediaError):
        volc.ref2video(
            prompt="",
            images=[],
            videos=[],
            audios=[],
            out=tmp_path / "v.mp4",
            seconds=2,
            resolution="480p",
            ratio="adaptive",
            seed=1,
            watermark=False,
            generate_audio=None,
            wait=False,
        )


def test_video_task_query_tags_finalized_clip(volc, monkeypatch, tmp_path):
    """An async-finalized clip must be tagged kind=video + meta so the reward's
    film discovery / footage totals recognize a one-shot async deliverable."""
    monkeypatch.setattr(
        volc,
        "_request",
        lambda *a, **k: {"id": "task-9", "status": "succeeded", "duration": 5, "usage": {"total_tokens": 12}},
    )
    fake = mediakit.GenResult(
        tmp_path / "out.mp4", "volc", "video", usage={"total_tokens": 12}, meta={"seconds": 5, "task_id": "task-9"}
    )
    monkeypatch.setattr(volc, "_finalize_video", lambda *a, **k: fake)
    out = volc.video_task(op="query", task_id="task-9", output=str(tmp_path / "out.mp4"))
    assert out["kind"] == "video"
    assert out["meta"] == {"seconds": 5, "task_id": "task-9"}
    assert out["path"] == str(tmp_path / "out.mp4")
    assert out["id"] == "task-9"  # from **res, used by the reward to dedup re-queries


def test_ref2video_builds_multimodal_roles(volc, monkeypatch, tmp_path):
    img = tmp_path / "r.png"
    img.write_bytes(base64.b64decode(_PNG_1x1))
    calls = _capture(monkeypatch, volc, {"id": "t"})
    volc.ref2video(
        prompt="scene",
        images=[img],
        videos=["https://example.com/v.mp4"],
        audios=["https://example.com/a.mp3"],
        out=tmp_path / "v.mp4",
        seconds=2,
        resolution="480p",
        ratio="adaptive",
        seed=1,
        watermark=False,
        generate_audio=None,
        wait=False,
    )
    roles = [c.get("role") for c in calls[0]["body"]["content"] if "role" in c]
    assert roles == ["reference_image", "reference_video", "reference_audio"]


# --------------------------------------------------------------------------
# retry / idempotency policy  (fake urlopen)
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def _http_error(code: int):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b'{"error":"boom"}'))


def _install_urlopen(monkeypatch, behaviors):
    """Fake urlopen that yields each behavior in turn (Exception -> raise)."""
    seq = list(behaviors)
    count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        count["n"] += 1
        b = seq.pop(0)
        if isinstance(b, Exception):
            raise b
        return _Resp(b)

    monkeypatch.setattr(mediakit.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mediakit.time, "sleep", lambda *a, **k: None)  # no real backoff
    return count


def test_post_not_retried_on_5xx(volc, monkeypatch):
    # a transient 5xx on a POST create-task must NOT be retried (could double-submit)
    count = _install_urlopen(monkeypatch, [_http_error(503)])
    with pytest.raises(mediakit.MediaError):
        volc._request("POST", "/contents/generations/tasks", {"x": 1})
    assert count["n"] == 1


def test_post_not_retried_on_urlerror(volc, monkeypatch):
    count = _install_urlopen(monkeypatch, [urllib.error.URLError("conn reset")])
    with pytest.raises(mediakit.MediaError):
        volc._request("POST", "/contents/generations/tasks", {"x": 1})
    assert count["n"] == 1


def test_post_retried_on_429(volc, monkeypatch):
    # 429 means "rejected, not processed" -> always safe to retry
    count = _install_urlopen(monkeypatch, [_http_error(429), b'{"ok": true}'])
    out = volc._request("POST", "/contents/generations/tasks", {"x": 1})
    assert out == {"ok": True}
    assert count["n"] == 2


def test_get_retried_on_5xx(volc, monkeypatch):
    # GET is idempotent -> retrying a transient 5xx is safe
    count = _install_urlopen(monkeypatch, [_http_error(500), b'{"status": "succeeded"}'])
    out = volc._request("GET", "/contents/generations/tasks/abc")
    assert out["status"] == "succeeded"
    assert count["n"] == 2


def test_delete_retried_on_urlerror(volc, monkeypatch):
    count = _install_urlopen(monkeypatch, [urllib.error.URLError("x"), b"{}"])
    volc._request("DELETE", "/contents/generations/tasks/abc")
    assert count["n"] == 2
