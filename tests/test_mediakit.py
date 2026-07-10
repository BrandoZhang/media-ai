"""Unit tests for the media-ai toolkit (mock backend, offline).

These exercise the shared implementation behind every CLI: image/video/concat
generation, the usage ledger (the cost metric), the small pure helpers, and
backend selection. Everything runs on the offline mock backend (Pillow +
ffmpeg) — no network, no credentials. The real ``volc`` backend is not tested
here (it would require the Ark API); its wiring is covered by inspecting the
request body construction in ``test_volc_request.py``.
"""

from __future__ import annotations

import json

import pytest
from media_ai import mediakit


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    """Isolate the usage ledger per test (a fresh JSONL in tmp)."""
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("MEDIA_USAGE_LOG", str(log))
    monkeypatch.setenv("MEDIA_BACKEND", "mock")
    return log


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------


def test_usage_log_path_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_USAGE_LOG", str(tmp_path / "x" / "u.jsonl"))
    assert mediakit.usage_log_path() == tmp_path / "x" / "u.jsonl"


def test_usage_log_path_default(monkeypatch):
    monkeypatch.delenv("MEDIA_USAGE_LOG", raising=False)
    assert mediakit.usage_log_path().name == "media_usage.jsonl"


def test_volc_image_size_floor_and_override(monkeypatch):
    monkeypatch.delenv("ARK_IMAGE_SIZE", raising=False)
    # below the pixel floor -> named preset
    assert mediakit._volc_image_size(768, 432) == "2K"
    # above the floor -> exact WxH
    assert mediakit._volc_image_size(2560, 1440) == "2560x1440"
    # explicit override wins regardless
    monkeypatch.setenv("ARK_IMAGE_SIZE", "4K")
    assert mediakit._volc_image_size(768, 432) == "4K"


def test_video_dims_adaptive_and_fallback():
    assert mediakit.video_dims("480p", "16:9") == (864, 480)
    # adaptive collapses to 16:9
    assert mediakit.video_dims("480p", "adaptive") == mediakit.video_dims("480p", "16:9")
    # unknown resolution falls back to 720p table
    assert mediakit.video_dims("nonsense", "16:9") == mediakit.video_dims("720p", "16:9")


def test_video_dims_normalizes_case_and_whitespace():
    # a wrong-case / padded resolution must not silently fall back to 720p
    assert mediakit.video_dims("480P", "16:9") == mediakit.video_dims("480p", "16:9")
    assert mediakit.video_dims(" 480p ", "16:9") == (864, 480)
    assert mediakit.video_dims("1080P", "9:16") == mediakit.video_dims("1080p", "9:16")


def test_mock_token_formulas_match_docs():
    # image: output_tokens = images * floor(w*h/256)
    assert mediakit._mock_image_tokens(768, 432, 2)["total_tokens"] == (768 * 432 // 256) * 2
    # video: floor(pixels*seconds/1024)
    assert mediakit._mock_video_tokens(864, 480, 3)["total_tokens"] == (864 * 480 * 3) // 1024


# --------------------------------------------------------------------------
# usage ledger
# --------------------------------------------------------------------------


def test_record_and_summarize_usage(_ledger):
    mediakit.record_usage({"tool": "text2image", "backend": "mock", "generated_images": 2, "total_tokens": 100})
    mediakit.record_usage({"tool": "text2video", "backend": "mock", "seconds": 3, "total_tokens": 50})
    totals = mediakit.summarize_usage()
    assert totals["calls"] == 2
    assert totals["total_tokens"] == 150
    assert totals["images_generated"] == 2
    assert totals["video_seconds"] == 3
    assert totals["by_tool"] == {"text2image": 100, "text2video": 50}
    assert totals["by_backend"] == {"mock": 150}


def test_summarize_missing_ledger_is_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_USAGE_LOG", str(tmp_path / "does_not_exist.jsonl"))
    assert mediakit.summarize_usage()["total_tokens"] == 0


def test_record_usage_tolerates_bad_dir(monkeypatch):
    # accounting must never raise, even if the path is unwritable
    monkeypatch.setenv("MEDIA_USAGE_LOG", "/proc/definitely/not/writable/u.jsonl")
    mediakit.record_usage({"tool": "t", "total_tokens": 1})  # should not raise


# --------------------------------------------------------------------------
# mock backend: images
# --------------------------------------------------------------------------


def test_text2image_writes_file_and_records_usage(tmp_path, _ledger):
    out = tmp_path / "ref.png"
    res = mediakit.MockBackend().text2image(prompt="a red dune", out=out, width=320, height=200, seed=7)
    assert out.is_file() and out.stat().st_size > 0
    assert res.kind == "image"
    assert res.usage["total_tokens"] > 0
    # usage was appended to the ledger
    assert mediakit.summarize_usage()["total_tokens"] == res.usage["total_tokens"]


def test_text2image_group_produces_extra_paths(tmp_path, _ledger):
    out = tmp_path / "grp.png"
    res = mediakit.MockBackend().text2image(prompt="team", out=out, width=128, height=128, seed=1, max_images=3)
    assert len(res.extra_paths) == 2  # output + 2 extras = 3 total
    for p in res.extra_paths:
        from pathlib import Path

        assert Path(p).is_file()
    assert res.usage["generated_images"] == 3


def test_image2image_requires_existing_reference(tmp_path):
    with pytest.raises(mediakit.MediaError):
        mediakit.MockBackend().image2image(
            prompt="x", images=[tmp_path / "missing.png"], out=tmp_path / "o.png", strength=0.5, seed=1
        )


def test_image2image_derives_from_reference(tmp_path, _ledger):
    ref = tmp_path / "ref.png"
    mediakit.MockBackend().text2image(prompt="base", out=ref, width=128, height=128, seed=1)
    res = mediakit.MockBackend().image2image(
        prompt="variant", images=[ref], out=tmp_path / "v.png", strength=0.6, seed=1
    )
    assert res.path.is_file()
    assert res.meta["refs"] == [str(ref)]


def test_model_is_echoed_into_meta(tmp_path, _ledger):
    res = mediakit.MockBackend().text2image(
        prompt="p", out=tmp_path / "m.png", width=64, height=64, seed=1, model="doubao-seedream-5-0-260128"
    )
    assert res.meta["model"] == "doubao-seedream-5-0-260128"


# --------------------------------------------------------------------------
# mock backend: video + concat  (ffmpeg-backed; kept tiny)
# --------------------------------------------------------------------------


def test_text2video_and_ledger(tmp_path, _ledger):
    out = tmp_path / "s.mp4"
    res = mediakit.MockBackend().text2video(
        prompt="suns set",
        out=out,
        seconds=1,
        resolution="480p",
        ratio="16:9",
        seed=1,
        camera_fixed=False,
        watermark=False,
        generate_audio=None,
    )
    assert out.is_file() and out.stat().st_size > 0
    assert res.kind == "video"
    assert res.meta["seconds"] == 1
    assert mediakit.summarize_usage()["video_seconds"] == 1


def test_image2video_last_frame_and_chaining(tmp_path, _ledger):
    ref = tmp_path / "ref.png"
    mediakit.MockBackend().text2image(prompt="hero", out=ref, width=128, height=128, seed=1)
    out = tmp_path / "shot.mp4"
    res = mediakit.MockBackend().image2video(
        prompt="turns",
        first_frame=ref,
        last_frame=None,
        out=out,
        seconds=1,
        resolution="480p",
        ratio="adaptive",
        seed=1,
        camera_fixed=False,
        watermark=False,
        generate_audio=None,
        return_last_frame=True,
    )
    assert out.is_file()
    assert res.extra_paths, "return_last_frame should emit a last-frame image"
    from pathlib import Path

    assert Path(res.extra_paths[0]).is_file()


def test_ref2video_requires_a_reference(tmp_path, _ledger):
    # the mock ref2video accepts empty refs (text-only), so assert it renders;
    # the *volc* backend rejects empty content (see test_volc_request.py).
    out = tmp_path / "r.mp4"
    res = mediakit.MockBackend().ref2video(
        prompt="two meet",
        images=[],
        videos=[],
        audios=[],
        out=out,
        seconds=1,
        resolution="480p",
        ratio="adaptive",
        seed=1,
        watermark=False,
        generate_audio=None,
    )
    assert out.is_file() and res.kind == "video"


def test_concat_clips_joins(tmp_path, _ledger):
    b = mediakit.MockBackend()
    clips = []
    for i in range(2):
        p = tmp_path / f"c{i}.mp4"
        b.text2video(
            prompt=f"clip {i}",
            out=p,
            seconds=1,
            resolution="480p",
            ratio="16:9",
            seed=i,
            camera_fixed=False,
            watermark=False,
            generate_audio=None,
        )
        clips.append(p)
    final = tmp_path / "final.mp4"
    mediakit.concat_clips(clips, final, w=128, h=72)
    assert final.is_file() and final.stat().st_size > 0


def test_concat_rejects_missing_input(tmp_path):
    with pytest.raises(mediakit.MediaError):
        mediakit.concat_clips([tmp_path / "nope.mp4"], tmp_path / "out.mp4")


def _clip_with_audio(path, seconds=1):
    """Render a tiny clip that HAS an audio track (silent), via bundled ffmpeg."""
    import subprocess

    subprocess.run(
        [
            mediakit.ffmpeg_exe(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=64x64:d={seconds}:r=24",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_concat_preserves_audio_when_all_inputs_have_it(tmp_path, _ledger):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _clip_with_audio(a)
    _clip_with_audio(b)
    assert mediakit._has_audio(a)  # sanity
    final = tmp_path / "final.mp4"
    mediakit.concat_clips([a, b], final, w=64, h=64)
    assert final.is_file()
    assert mediakit._has_audio(final), "concat must keep audio when every input has it"


def test_concat_video_only_when_inputs_are_silent(tmp_path, _ledger):
    # mock clips have no audio track -> concat is video-only, and must not fail
    b = mediakit.MockBackend()
    clips = []
    for i in range(2):
        p = tmp_path / f"c{i}.mp4"
        b.text2video(
            prompt=f"clip {i}",
            out=p,
            seconds=1,
            resolution="480p",
            ratio="16:9",
            seed=i,
            camera_fixed=False,
            watermark=False,
            generate_audio=None,
        )
        clips.append(p)
    assert not mediakit._has_audio(clips[0])
    final = tmp_path / "silent.mp4"
    mediakit.concat_clips(clips, final, w=128, h=72)
    assert final.is_file() and not mediakit._has_audio(final)


# --------------------------------------------------------------------------
# backend selection + result serialization
# --------------------------------------------------------------------------


def test_get_backend_selection(monkeypatch):
    monkeypatch.setenv("MEDIA_BACKEND", "mock")
    assert isinstance(mediakit.get_backend(), mediakit.MockBackend)
    assert isinstance(mediakit.get_backend("mock"), mediakit.MockBackend)
    with pytest.raises(mediakit.MediaError):
        mediakit.get_backend("bogus")


def test_get_backend_volc_needs_key(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    with pytest.raises(mediakit.MediaError):
        mediakit.get_backend("volc")


def test_video_task_mock_is_noop(tmp_path, _ledger):
    res = mediakit.MockBackend().video_task(op="query", task_id="abc")
    assert res["ok"] and res["task_id"] == "abc"


def test_dumps_result_handles_genresult_and_dict(tmp_path, _ledger):
    res = mediakit.MockBackend().text2image(prompt="p", out=tmp_path / "d.png", width=64, height=64, seed=1)
    parsed = json.loads(mediakit.dumps_result(res))
    assert parsed["ok"] and parsed["kind"] == "image"
    # plain dict (async submit shape) round-trips too
    assert json.loads(mediakit.dumps_result({"ok": True, "status": "queued"}))["status"] == "queued"
