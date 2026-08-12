"""Unit tests for the provider-agnostic core: geometry, capability validation,
error taxonomy, result serialization, and the usage ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_ai.core import geometry, usage
from media_ai.core.binding import Audio, Constraints, Geometry, Output, References, Video
from media_ai.core.errors import ErrorCategory, MediaError
from media_ai.core.result import Artifact, GenerationResult, JobHandle
from media_ai.core.types import GeometrySpec, ImageRequest, MediaRef, VideoRequest
from media_ai.core.validate import UnsupportedPolicy, validate_request

# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def test_parse_size_ok_and_bad():
    assert geometry.parse_size("1024x768") == (1024, 768)
    assert geometry.parse_size("64X64") == (64, 64)
    with pytest.raises(MediaError):
        geometry.parse_size("not-a-size")


def test_mediaref_is_remote_only_matches_url_schemes():
    from media_ai.core.types import MediaRef

    # Only genuine URL-ish schemes are remote.
    for raw in ("http://x/a.png", "https://x/a.png", "data:image/png;base64,AA", "asset://abc", "gs://b/o"):
        assert MediaRef(raw).is_remote is True
    # A local filename is never remote — including names that start with "file-", which
    # is NOT special-cased (no provider consumes a file-id ref), so read_bytes() can read them.
    for raw in ("file-0.png", "file-1.jpg", "file-photo.png", "file-abc123DEF456ghi789", "./file-x", "my-file.png"):
        assert MediaRef(raw).is_remote is False, raw
        assert MediaRef(raw).is_local is True


def test_to_data_uri_propagates_media_type_for_unknown_extension(tmp_path):
    from media_ai.core.mediaref import to_data_uri
    from media_ai.core.types import MediaRef

    p = tmp_path / "clip_no_ext"  # no extension -> guess_mime falls back to the media kind
    p.write_bytes(b"\x00\x01\x02")
    assert to_data_uri(MediaRef(str(p)), "video").startswith("data:video/octet-stream;base64,")
    assert to_data_uri(MediaRef(str(p)), "audio").startswith("data:audio/octet-stream;base64,")
    assert to_data_uri(MediaRef(str(p)), "image").startswith("data:image/png;base64,")


def test_video_dims_normalizes_case_and_adaptive():
    assert geometry.video_dims("480p", "16:9") == (864, 480)
    assert geometry.video_dims(" 480P ", "16:9") == (864, 480)
    assert geometry.video_dims("480p", "adaptive") == geometry.video_dims("480p", "16:9")
    assert geometry.video_dims("nonsense", "16:9") == geometry.video_dims("720p", "16:9")


def test_resolve_image_pixels_modes():
    assert geometry.resolve_image_pixels(GeometrySpec(width=800, height=600), (1, 1)) == (800, 600)
    w, h = geometry.resolve_image_pixels(GeometrySpec(aspect_ratio="16:9", resolution="1K"), (1, 1))
    assert w > h and w % 2 == 0 and h % 2 == 0


@pytest.mark.parametrize("ratio", ["0:0", "16:0", "0:9", "-16:9", "16:-9", "abc", "16", "16:9:1", ""])
def test_ratio_to_wh_refuses_a_ratio_that_is_not_two_positive_sides(ratio):
    # "0:0" reached the division and surfaced as an exit-1 `unknown` "division by zero";
    # "16:0" and "abc" were silently substituted (a 2-pixel edge, a square). A bad ratio
    # is a request problem, so it reads like one — same as a bad --size.
    with pytest.raises(MediaError) as ei:
        geometry.ratio_to_wh(ratio, 1024)
    assert ei.value.category is ErrorCategory.VALIDATION and ei.value.exit_code == 3


def test_ratio_to_wh_accepts_either_orientation():
    assert geometry.ratio_to_wh("16:9", 1024) == (1024, 576)
    assert geometry.ratio_to_wh("9:16", 1024) == (576, 1024)


@pytest.mark.parametrize("bad", ["0:0", "16:0", "0:9", "-16:9", "abc", "16", "16:9:1", "1.85:1"])
def test_parse_ratio_refuses_a_value_that_is_not_a_ratio(bad):
    # The form of --aspect-ratio is the CLI's business (as --size's is); *which* ratios a
    # model accepts stays the manifest's. Four shipped bindings declare no ratio list, so
    # without this check "16:0" reached the wire — a billed request for a nonsense shape.
    with pytest.raises(MediaError) as ei:
        geometry.parse_ratio(bad)
    assert ei.value.category is ErrorCategory.VALIDATION and "--aspect-ratio" in ei.value.message


def test_parse_ratio_normalizes_and_passes_adaptive_through():
    assert geometry.parse_ratio(" 16 : 9 ") == "16:9"
    assert geometry.parse_ratio("Adaptive") == "adaptive"  # Ark asking the model to choose
    assert geometry.parse_ratio(None) is None


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
# validation against declared constraints
# --------------------------------------------------------------------------


def _image(**over) -> Constraints:
    """Constraints as a manifest would declare them, with nothing implied.

    Everything absent means *undeclared*, and undeclared means unchecked — so a test
    that wants a limit enforced has to say so, exactly like a manifest does.
    """
    geometry = Geometry(mode=over.pop("mode", "pixels"), **over.pop("geometry", {}))
    return Constraints(
        supports=over.pop("supports", {}),
        options=over.pop("options", ()),
        geometry=geometry,
        output=Output(**over.pop("output", {})),
        references=References(**over.pop("references", {})),
    )


def test_an_undeclared_capability_is_refused():
    req = ImageRequest(prompt="x", output=Path("o.png"), seed=5)
    with pytest.raises(MediaError) as ei:
        validate_request(req, _image())
    assert ei.value.category == ErrorCategory.UNSUPPORTED
    assert ei.value.exit_code == 3
    assert ei.value.code == "request_not_supported"


def test_a_declared_capability_passes():
    req = ImageRequest(prompt="x", output=Path("o.png"), seed=5)
    validate_request(req, _image(supports={"seed": True}))


def test_transparency_is_gated_but_opaque_never_is():
    with pytest.raises(MediaError):
        validate_request(ImageRequest(prompt="x", output=Path("o.png"), background="transparent"), _image())
    validate_request(ImageRequest(prompt="x", output=Path("o.png"), background="opaque"), _image())


def test_counts_and_references_are_capped():
    with pytest.raises(MediaError):
        validate_request(ImageRequest(prompt="x", output=Path("o.png"), count=5),
                         _image(output={"max_count": 1}))
    with pytest.raises(MediaError):
        validate_request(ImageRequest(prompt="x", output=Path("o.png"), references=[MediaRef("a.png")]),
                         _image(references={"max": 0}))


def test_a_joint_input_output_budget_is_enforced():
    """Seedream budgets references and outputs together; neither cap alone says that."""
    c = _image(output={"max_count": 15, "max_total_images": 15}, references={"max": 14})
    req = ImageRequest(prompt="x", output=Path("o.png"), count=10,
                       references=[MediaRef(f"{i}.png") for i in range(10)])
    with pytest.raises(MediaError) as ei:
        validate_request(req, c)
    assert "joint limit" in ei.value.message


def test_a_size_tier_outside_the_declared_set_is_refused():
    c = _image(mode="aspect_ratio", geometry={"aspect_ratios": ("1:1",), "named_sizes": ("1K",)})
    with pytest.raises(MediaError) as ei:
        validate_request(ImageRequest(prompt="x", output=Path("o.png"),
                                      geometry=GeometrySpec(aspect_ratio="1:1", resolution="4K")), c)
    assert "4K" in ei.value.message
    validate_request(ImageRequest(prompt="x", output=Path("o.png"),
                                  geometry=GeometrySpec(aspect_ratio="1:1", resolution="1K")), c)


def test_an_absent_bound_is_not_enforced():
    """A binding that publishes no ceiling gets none applied — the API stays the authority."""
    c = _image(geometry={"pixel_total_min": 1000})   # a floor, and deliberately no ceiling
    validate_request(ImageRequest(prompt="x", output=Path("o.png"),
                                  geometry=GeometrySpec(width=8000, height=8000)), c)
    with pytest.raises(MediaError):
        validate_request(ImageRequest(prompt="x", output=Path("o.png"),
                                      geometry=GeometrySpec(width=10, height=10)), c)


def test_an_unknown_option_is_rejected_and_the_policy_can_soften_it():
    req = ImageRequest(prompt="x", output=Path("o.png"), options={"bogus": 1})
    c = _image(options=("moderation",))
    with pytest.raises(MediaError):
        validate_request(req, c, UnsupportedPolicy.ERROR)
    warnings = validate_request(req, c, UnsupportedPolicy.WARN)
    assert warnings and "bogus" in warnings[0]
    assert validate_request(req, c, UnsupportedPolicy.IGNORE) == []


def test_video_duration_and_flags_follow_the_declaration():
    c = Constraints(video=Video(durations=(4, 8)))
    with pytest.raises(MediaError):
        validate_request(VideoRequest(prompt="x", output=Path("o.mp4"), duration=5), c)
    validate_request(VideoRequest(prompt="x", output=Path("o.mp4"), duration=8), c)


def test_a_coordinate_prompt_aimed_at_a_model_that_cannot_read_it_is_refused():
    """The failure this catches is silent: the tags are read as prose and the image is
    quietly wrong, with a 200 OK and a bill."""
    req = ImageRequest(prompt="move the subject to <bbox>10 10 90 90</bbox>", output=Path("o.png"))
    with pytest.raises(MediaError) as ei:
        validate_request(req, _image())
    assert "<bbox>" in ei.value.message
    validate_request(req, _image(supports={"interactive_edit": True}))


def test_an_oversized_local_reference_is_refused_before_the_upload(tmp_path):
    big = tmp_path / "huge.png"
    big.write_bytes(b"\0" * 2048)
    c = _image(references={"max": 4, "max_bytes": 1024})
    with pytest.raises(MediaError) as ei:
        validate_request(ImageRequest(prompt="x", output=Path("o.png"), references=[MediaRef(str(big))]), c)
    assert "exceeds" in ei.value.message


def test_a_reference_in_an_undeclared_format_is_refused(tmp_path):
    c = _image(references={"max": 4, "formats": ("png", "jpeg")})
    with pytest.raises(MediaError) as ei:
        validate_request(ImageRequest(prompt="x", output=Path("o.png"), references=[MediaRef("clip.gif")]), c)
    assert "gif" in ei.value.message


def test_a_remote_reference_is_the_providers_to_judge():
    """Nothing local to inspect, so nothing is claimed about it."""
    c = _image(references={"max": 4, "formats": ("png",), "max_bytes": 1})
    validate_request(ImageRequest(prompt="x", output=Path("o.png"),
                                  references=[MediaRef("https://example.test/a.gif")]), c)


def test_audio_duration_ranges_are_enforced():
    c = Constraints(audio=Audio(duration_ms=(3000, 600000)))
    from media_ai.core.types import MusicRequest

    with pytest.raises(MediaError):
        validate_request(MusicRequest(output=Path("o.mp3"), prompt="jazz", duration_ms=1000), c)
    validate_request(MusicRequest(output=Path("o.mp3"), prompt="jazz", duration_ms=30000), c)


def test_the_error_names_the_binding_and_how_to_ask_what_it_supports():
    with pytest.raises(MediaError) as ei:
        validate_request(ImageRequest(prompt="x", output=Path("o.png"), seed=1), _image(),
                         binding="acme/thing")
    assert "acme/thing" in ei.value.message
    assert ei.value.hint == "media-ai capabilities --binding acme/thing"


# --------------------------------------------------------------------------
# result serialization
# --------------------------------------------------------------------------


def test_generation_result_contract_keys(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"12345")
    r = GenerationResult(modality="image", provider="mock", model="mock",
                         artifacts=[Artifact.from_path(p, "image", mime="image/png")], usage={"total_tokens": 3})
    d = r.to_dict()
    assert d["ok"] and d["schema_version"] >= 1
    assert d["artifacts"] == [{"path": str(p), "kind": "image", "mime": "image/png", "bytes": 5, "role": None}]
    # Every produced file is in artifacts[] and nowhere else: a flat `path` alias let a
    # consumer read the first artifact two ways and silently miss the rest.
    assert not {"path", "bytes", "extra_paths", "kind", "operation"} & set(d)


def test_job_handle_has_poll_hint():
    d = JobHandle(provider="volc", model="m", id="task-1", output="/o.mp4").to_dict()
    assert d["status"] == "queued" and d["job"]["id"] == "task-1"
    assert "media-ai job query" in d["poll"] and "task_id" not in d


# --------------------------------------------------------------------------
# usage ledger
# --------------------------------------------------------------------------


def test_record_and_summarize(_ledger):
    usage.record_usage({"binding": "mock/mock", "scene": "image.text_to_image", "generated_images": 2,
                        "total_tokens": 100})
    usage.record_usage({"binding": "mock/mock", "scene": "video.text_to_video", "seconds": 3, "total_tokens": 50})
    usage.record_usage({"binding": "gemini/gemini-tts", "scene": "speech.text_to_speech", "characters": 42,
                        "total_tokens": 20})
    totals = usage.summarize_usage()
    assert totals["calls"] == 3 and totals["total_tokens"] == 170
    assert totals["images_generated"] == 2 and totals["video_seconds"] == 3
    assert totals["speech_characters"] == 42
    assert totals["by_binding"] == {"mock/mock": 150, "gemini/gemini-tts": 20}
    assert totals["by_scene"]["speech.text_to_speech"] == 20


def test_summarize_buckets_a_scene_less_line_rather_than_dropping_it(tmp_path, monkeypatch):
    # `job query` finalizes work whose scene is unknowable by then; the cost is still real.
    log = tmp_path / "u.jsonl"
    log.write_text(json.dumps({"binding": "volc-ark/seedance-2.0", "total_tokens": 7}) + "\n")
    monkeypatch.setenv("MEDIA_USAGE_LOG", str(log))
    totals = usage.summarize_usage()
    assert totals["by_binding"] == {"volc-ark/seedance-2.0": 7} and totals["by_scene"] == {"?": 7}


def test_record_usage_never_raises(monkeypatch):
    monkeypatch.setenv("MEDIA_USAGE_LOG", "/proc/definitely/not/writable/u.jsonl")
    usage.record_usage({"total_tokens": 1})  # must not raise


def test_parse_options_coerces_bool_int_float():
    from media_ai.cli.common import parse_options
    o = parse_options(["a=true", "b=3", "c=7.5", "d=hello", "e=off", "f=-2"])
    assert o == {"a": True, "b": 3, "c": 7.5, "d": "hello", "e": False, "f": -2}


def test_parse_args_emits_json_error_on_bad_flag(capsys):
    # A parse error must still honor the machine contract: one JSON object on stdout
    # (category cli) plus exit code 2 — not an empty stdout.
    import argparse

    from media_ai.cli import common

    ap = argparse.ArgumentParser(prog="media-ai usage")
    ap.add_argument("--log")
    with pytest.raises(SystemExit) as ei:
        common.parse_args(ap, ["--bogus"])
    assert ei.value.code == 2
    obj = json.loads(capsys.readouterr().out)
    assert obj["ok"] is False and obj["error"]["category"] == "cli"


def test_parse_args_help_preserves_standard_stdout(capsys):
    # --help stays standard CLI behavior: help text on stdout, exit 0 (not turned into JSON).
    import argparse

    from media_ai.cli import common

    ap = argparse.ArgumentParser(prog="media-ai usage")
    with pytest.raises(SystemExit) as ei:
        common.parse_args(ap, ["--help"])
    assert ei.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_a_declared_false_survives_the_capabilities_filter():
    """`v not in (None, (), 0)` looked like "drop the empties" and was not: `False == 0`
    in Python, so a deliberately declared `async = false` vanished and a reader fell
    back to the opposite default. Booleans are always meaningful."""
    from media_ai.core.binding import Constraints, Video

    body = Constraints(video=Video(is_async=False)).to_dict()
    assert body["video"] == {"is_async": False}


def test_an_undeclared_block_is_absent_rather_than_defaulted():
    """An image-only binding was printing `"video": {"is_async": true}` — a video
    capability read off a binding that serves no video scene."""
    from media_ai.core.binding import Constraints

    assert "video" not in Constraints().to_dict()
