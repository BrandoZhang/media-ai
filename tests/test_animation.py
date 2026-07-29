"""Animated-image export: the filter graph, the three containers, and what came out.

Two halves. The first builds ffmpeg arguments and asserts their *shape* — cheap, and
where the order-of-operations rules live. The second runs the bundled ffmpeg and reads
the finished file, because every bug this feature has actually had was invisible to the
first half: an escaped comma that broke every ``--max-width``, trim flags on the wrong
side of ``-i`` that undid ``--bounce``, and a GIF loop count that ffmpeg accepts and then
discards. Each of those produced plausible arguments and a wrong animation, reported as
success.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import have_media_stack
from media_ai.core.errors import MediaError
from media_ai.core.types import AnimationRequest, GeometrySpec, MediaRef
from media_ai.media import animation


def req(tmp_path, name="out.gif", **kw) -> AnimationRequest:
    kw.setdefault("source", MediaRef(str(tmp_path / "in.mp4")))
    return AnimationRequest(output=tmp_path / name, **kw)


def args_for(request, fmt=None, *, transparent=False) -> list[str]:
    container = animation.container_for(Path(request.output), fmt)
    return animation.build_args(request, container, transparent=transparent)


def filters(argv: list[str]) -> str:
    return argv[argv.index("-vf") + 1]


# ------------------------------------------------------------------ containers


def test_the_container_comes_from_the_extension_and_an_explicit_format_wins(tmp_path):
    assert animation.container_for(tmp_path / "a.gif").name == "gif"
    assert animation.container_for(tmp_path / "a.webp").name == "webp"
    assert animation.container_for(tmp_path / "a.apng").name == "apng"
    assert animation.container_for(tmp_path / "a.png").name == "apng"
    assert animation.container_for(tmp_path / "a.gif", "webp").name == "webp"


def test_an_unknown_format_is_refused_rather_than_handed_to_a_muxer(tmp_path):
    with pytest.raises(MediaError) as ei:
        animation.container_for(tmp_path / "a.gif", "mp4")
    assert ei.value.code == "animation_format_unsupported"
    assert "gif" in str(ei.value) and "webp" in str(ei.value)

    with pytest.raises(MediaError) as ei:
        animation.container_for(tmp_path / "a.mov")
    assert ei.value.code == "animation_format_unknown"
    assert ei.value.exit_code == 3


def test_only_gif_gets_a_palette_pass(tmp_path):
    """A single-pass GIF quantises to a fixed web palette and bands badly; webp and apng
    have no palette at all, so the same filters there would be an error."""
    assert "palettegen" in filters(args_for(req(tmp_path, "a.gif")))
    assert "palettegen" not in filters(args_for(req(tmp_path, "a.webp")))
    assert "palettegen" not in filters(args_for(req(tmp_path, "a.apng")))


def test_a_transparent_gif_reserves_a_palette_entry_and_a_threshold(tmp_path):
    """Without both, the keyed region comes out **black** rather than transparent —
    palettegen has nowhere to put "transparent" and paletteuse has no cut point."""
    chain = filters(args_for(req(tmp_path, "a.gif", transparent=True), transparent=True))
    assert "reserve_transparent=1" in chain
    assert "alpha_threshold=" in chain
    assert "max_colors=255" in chain  # one entry spent on transparency, not 256

    opaque = filters(args_for(req(tmp_path, "a.gif")))
    assert "reserve_transparent" not in opaque and "max_colors=256" in opaque


@pytest.mark.parametrize("name,opaque,alpha", [("webp", "yuv420p", "yuva420p"), ("apng", "rgb24", "rgba")])
def test_the_real_alpha_containers_switch_pixel_format(tmp_path, name, opaque, alpha):
    plain = args_for(req(tmp_path, f"a.{name}"))
    keyed = args_for(req(tmp_path, f"a.{name}", transparent=True), transparent=True)
    assert plain[plain.index("-pix_fmt") + 1] == opaque
    assert keyed[keyed.index("-pix_fmt") + 1] == alpha


# ------------------------------------------------------------------ trimming


def test_both_trim_flags_precede_the_input(tmp_path):
    """On the *output* they bound the result instead of the source, so they truncate
    after the filters have run: `--bounce` loses its reversed half and `--speed 0.5` is
    cut back to the source length. The request honoured, then undone."""
    argv = args_for(req(tmp_path, start_seconds=1, end_seconds=3))
    assert argv.index("-ss") < argv.index("-i")
    assert argv.index("-t") < argv.index("-i")
    assert argv[argv.index("-t") + 1] == "2.0"  # end - start, not end


def test_a_duration_is_taken_from_the_start_offset(tmp_path):
    argv = args_for(req(tmp_path, start_seconds=5, duration_seconds=2))
    assert argv[argv.index("-ss") + 1] == "5"
    assert argv[argv.index("-t") + 1] == "2"


@pytest.mark.parametrize("kw,code", [
    (dict(end_seconds=1, duration_seconds=1), "animation_trim_conflict"),
    (dict(start_seconds=2, end_seconds=1), "animation_trim_invalid"),
    (dict(start_seconds=1, end_seconds=1), "animation_trim_invalid"),
])
def test_contradictory_trims_are_refused(tmp_path, kw, code):
    with pytest.raises(MediaError) as ei:
        args_for(req(tmp_path, **kw))
    assert ei.value.code == code


# ------------------------------------------------------------------ the chain


def test_the_filter_order_is_speed_then_rate_then_scale_then_key(tmp_path):
    """Load-bearing: speed rewrites the timestamps `fps` resamples, scaling precedes the
    key so it runs on fewer pixels, and a key needs an alpha plane before anything
    downstream flattens it away."""
    chain = filters(args_for(
        req(tmp_path, "a.webp", speed=2, fps=10, max_width=100, transparent=True), transparent=True))
    order = [chain.index(x) for x in ("setpts", "fps=", "scale=", "chromakey")]
    assert order == sorted(order)
    assert chain.index("format=rgba") > chain.index("chromakey")


def test_reverse_and_bounce_come_last_and_bounce_splits_the_chain(tmp_path):
    # webp, not gif: both buffer whole frames, so they must come after everything that
    # thins the stream — but on a gif the palette pass is appended after them, and
    # asserting the end of *that* chain would be asserting the palette instead.
    assert filters(args_for(req(tmp_path, "a.webp", fps=8, reverse=True))) == "fps=8,reverse"
    bounced = filters(args_for(req(tmp_path, "a.webp", bounce=True)))
    assert "split[fwd][tmp]" in bounced and "concat=n=2:v=1:a=0" in bounced


def test_a_bare_request_still_gets_a_filter_graph(tmp_path):
    """`-vf` with an empty value is an ffmpeg error, so "no filters" needs a name."""
    assert filters(args_for(req(tmp_path, "a.webp"))) == "null"


def test_the_comma_inside_a_scale_expression_is_escaped(tmp_path):
    """ffmpeg splits the filter chain on commas *before* parsing any argument, so an
    unescaped `min(iw,480)` was read as the end of one filter and the start of another —
    which broke every --max-width request there has ever been."""
    chain = filters(args_for(req(tmp_path, "a.webp", max_width=480)))
    assert r"min(iw\,480)" in chain
    assert "min(iw,480)" not in chain


def test_a_fit_inside_bound_never_enlarges_and_an_exact_size_is_exact(tmp_path):
    assert r"scale=min(iw\,320):-1" in filters(args_for(req(tmp_path, "a.webp", max_width=320)))
    assert r"scale=-1:min(ih\,240)" in filters(args_for(req(tmp_path, "a.webp", max_height=240)))
    assert "force_original_aspect_ratio=decrease" in filters(
        args_for(req(tmp_path, "a.webp", max_width=320, max_height=240)))
    assert "scale=100:80" in filters(args_for(req(tmp_path, "a.webp", geometry=GeometrySpec(width=100, height=80))))


def test_size_and_a_fit_inside_bound_together_are_refused(tmp_path):
    with pytest.raises(MediaError) as ei:
        args_for(req(tmp_path, "a.webp", geometry=GeometrySpec(width=100, height=80), max_width=50))
    assert ei.value.code == "animation_geometry_conflict"


def test_a_speed_of_zero_is_refused_rather_than_read_as_unchanged(tmp_path):
    """`speed or 1.0` silently turned 0 — an invalid speed worth refusing — into "leave
    it alone", which is why the field is `None`-defaulted instead."""
    with pytest.raises(MediaError) as ei:
        args_for(req(tmp_path, "a.webp", speed=0))
    assert ei.value.code == "animation_speed_invalid"
    assert "setpts" not in filters(args_for(req(tmp_path, "a.webp")))  # unset != 1.0 written out


@pytest.mark.parametrize("kw,code", [
    (dict(speed=-1), "animation_speed_invalid"),
    (dict(fps=0), "animation_fps_invalid"),
    (dict(scale_filter="swirl", max_width=10), "animation_scale_filter_unknown"),
    (dict(transparent=True, key_mode="magic"), "animation_key_mode_unknown"),
    (dict(transparent=True, similarity=0), "animation_key_similarity_invalid"),
    (dict(transparent=True, similarity=1.5), "animation_key_similarity_invalid"),
    (dict(transparent=True, blend=2), "animation_key_blend_invalid"),
    (dict(options={"max_colors": 1}), "animation_max_colors_invalid"),
    (dict(options={"dither": "swirl"}), "animation_dither_unknown"),
    (dict(options={"palette_stats_mode": "sideways"}), "animation_palette_stats_unknown"),
    (dict(loop=-1), "animation_loop_invalid"),
    (dict(loop=animation.MAX_PLAYS + 1), "animation_loop_invalid"),
])
def test_every_out_of_range_argument_has_its_own_code(tmp_path, kw, code):
    """One code per mistake, so a caller can branch on which knob to change."""
    with pytest.raises(MediaError) as ei:
        args_for(req(tmp_path, "a.gif", **kw), transparent=bool(kw.get("transparent")))
    assert ei.value.code == code
    assert ei.value.exit_code == 3


def test_an_animation_never_carries_an_audio_track(tmp_path):
    assert "-an" in args_for(req(tmp_path, "a.webp"))


# ------------------------------------------------------------------ looping


@pytest.mark.parametrize("container,forever,once", [("gif", ["-loop", "0"], ["-loop", "-1"]),
                                                   ("webp", ["-loop", "0"], ["-loop", "1"]),
                                                   ("apng", ["-plays", "0"], ["-plays", "1"])])
def test_forever_and_once_have_a_spelling_per_container(tmp_path, container, forever, once):
    assert forever == animation._loop(animation.CONTAINERS[container], 0)
    assert once == animation._loop(animation.CONTAINERS[container], 1)


def test_a_finite_gif_count_asks_ffmpeg_for_the_block_and_nothing_else():
    """ffmpeg 7 accepts `-loop 3` for a GIF and writes 0 — forever. Passing the number
    through would produce an endless animation reported as three plays, so the count is
    written into the file afterwards instead."""
    assert animation._loop(animation.CONTAINERS["gif"], 3) == ["-loop", "0"]
    assert animation._loop(animation.CONTAINERS["webp"], 3) == ["-loop", "3"]
    assert animation._loop(animation.CONTAINERS["apng"], 3) == ["-plays", "3"]


def test_setting_a_gif_loop_count_refuses_a_file_with_no_loop_block(tmp_path):
    """Silence here would mean reporting the requested count while the file plays once."""
    broken = tmp_path / "b.gif"
    broken.write_bytes(b"GIF89a" + b"\0" * 32)
    with pytest.raises(MediaError) as ei:
        animation._set_gif_loop_count(broken, 3)
    assert ei.value.code == "animation_loop_not_expressible"


# ------------------------------------------------------------------ frames in


def frames(tmp_path, *names) -> list[MediaRef]:
    out = []
    for n in names:
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        out.append(MediaRef(str(p)))
    return out


def test_a_glob_or_a_directory_is_taken_as_the_callers_own_set(tmp_path):
    (tmp_path / "seq").mkdir()
    for i in range(3):
        (tmp_path / "seq" / f"f_{i:03}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    spec, glob = animation.frames_input(req(tmp_path, frames=[MediaRef(str(tmp_path / "seq" / "*.png"))]))
    assert glob and spec.endswith("*.png")

    spec, glob = animation.frames_input(req(tmp_path, frames=[MediaRef(str(tmp_path / "seq"))]))
    assert glob and spec.endswith("*.png")


def test_a_single_still_is_read_as_a_file_not_a_pattern(tmp_path):
    spec, glob = animation.frames_input(req(tmp_path, frames=frames(tmp_path, "one.png")))
    assert not glob and spec.endswith("one.png")


def test_a_complete_list_becomes_a_glob_over_its_directory(tmp_path):
    refs = frames(tmp_path, "seq/a_001.png", "seq/a_002.png", "seq/a_003.png")
    spec, glob = animation.frames_input(req(tmp_path, frames=refs))
    assert glob and spec == str(tmp_path / "seq" / "*.png")


def test_a_partial_list_is_refused_because_the_glob_would_animate_the_rest(tmp_path):
    """ffmpeg reads a sequence as a glob, never as a list of paths. Substituting one for
    the other silently animates whatever else is in the directory."""
    all_refs = frames(tmp_path, "seq/a_001.png", "seq/a_002.png", "seq/a_003.png")
    with pytest.raises(MediaError) as ei:
        animation.frames_input(req(tmp_path, frames=all_refs[:2]))
    assert ei.value.code == "animation_frames_extra"
    assert "a_003.png" in str(ei.value)


def test_an_unpadded_sequence_is_refused_because_the_order_would_be_ignored(tmp_path):
    """The exact trap: `f_2, f_10` is the order a caller means and `f_10, f_2` is the
    order a glob produces, because `1` sorts before `2`. Animating the second while being
    handed the first is a reordered animation reported as success."""
    refs = frames(tmp_path, "seq/f_2.png", "seq/f_10.png")
    with pytest.raises(MediaError) as ei:
        animation.frames_input(req(tmp_path, frames=refs))
    assert ei.value.code == "animation_frames_unordered"
    assert "zero-pad" in str(ei.value)

    # Zero-padded, the two orders agree and nothing is refused.
    padded = frames(tmp_path, "ok/f_002.png", "ok/f_010.png")
    assert animation.frames_input(req(tmp_path, frames=padded))[1] is True


@pytest.mark.parametrize("names,code", [
    (("a/x_1.png", "b/x_2.png"), "animation_frames_scattered"),
    (("s/x_1.png", "s/x_2.jpg"), "animation_frames_mixed"),
])
def test_a_sequence_that_cannot_be_globbed_is_refused(tmp_path, names, code):
    with pytest.raises(MediaError) as ei:
        animation.frames_input(req(tmp_path, frames=frames(tmp_path, *names)))
    assert ei.value.code == code


def test_a_frame_sequence_sets_the_read_rate_and_asks_for_glob_matching(tmp_path):
    """Stills have no timebase of their own, so something has to say how fast to read
    them; `fps` in the chain then resamples from there."""
    refs = frames(tmp_path, "seq/a_001.png", "seq/a_002.png")
    argv = args_for(req(tmp_path, "a.webp", source=None, frames=refs))
    assert argv[:2] == ["-pattern_type", "glob"]
    assert argv[argv.index("-framerate") + 1] == str(animation.DEFAULT_FRAME_RATE)

    argv = args_for(req(tmp_path, "a.webp", source=None, frames=refs, fps=30))
    assert argv[argv.index("-framerate") + 1] == "30"


def test_a_missing_input_is_named_before_ffmpeg_is_reached(tmp_path):
    with pytest.raises(MediaError) as ei:
        animation.render(req(tmp_path, "a.webp"), animation.CONTAINERS["webp"], transparent=False)
    assert ei.value.code == "animation_input_not_found"
    assert "in.mp4" in str(ei.value)


# ------------------------------------------------------------------ end to end

pytestmark_media = pytest.mark.skipif(not have_media_stack(), reason="needs Pillow + ffmpeg")


def gif_plays(path: Path):
    """The NETSCAPE loop count, or None when the block is absent (= play once)."""
    raw = path.read_bytes()
    at = raw.find(b"NETSCAPE2.0\x03\x01")
    return struct.unpack("<H", raw[at + 13:at + 15])[0] if at >= 0 else None


def webp_plays(path: Path):
    raw, off = path.read_bytes(), 12
    while off + 8 <= len(raw):
        fourcc, size = raw[off:off + 4], struct.unpack("<I", raw[off + 4:off + 8])[0]
        if fourcc == b"ANIM":
            return struct.unpack("<H", raw[off + 12:off + 14])[0]
        off += 8 + size + (size & 1)
    return None


def apng_plays(path: Path):
    raw, off = path.read_bytes(), 8
    while off + 8 <= len(raw):
        size = struct.unpack(">I", raw[off:off + 4])[0]
        if raw[off + 4:off + 8] == b"acTL":
            return struct.unpack(">I", raw[off + 12:off + 16])[0]
        off += 12 + size
    return None


def gif_millis(path: Path) -> int:
    """Total playing time, from the per-frame delays actually written."""
    from PIL import Image

    total = 0
    with Image.open(path) as im:
        for i in range(im.n_frames):
            im.seek(i)
            total += im.info.get("duration", 0)
    return total


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """Two seconds of a white block crossing a flat green field, at 12 fps.

    Flat green so ``--transparent`` has something to key, and *moving* so the frames
    genuinely differ: an animation of 24 identical frames is legitimately collapsed to
    one by the WebP encoder, which looks exactly like the bug this file is watching for.
    """
    if not have_media_stack():
        pytest.skip("needs Pillow + ffmpeg")
    from media_ai.media.ffmpeg import run_ffmpeg

    out = tmp_path_factory.mktemp("anim") / "green.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=0x00FF00:s=160x120:r=12:d=2",
        "-f", "lavfi", "-i", "color=c=white:s=32x32:r=12:d=2",
        "-filter_complex", "[0][1]overlay=x='10+50*t':y=44",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ])
    return out


@pytest.fixture
def env(tmp_path):
    from media_ai.core.config import Config, UserBinding, render_config

    config = tmp_path / "config.toml"
    config.write_text(render_config(Config(
        bindings={"local/ffmpeg": UserBinding(id="local/ffmpeg")},
        defaults={"animation.from_video": "local/ffmpeg", "animation.from_frames": "local/ffmpeg"},
    )), encoding="utf-8")
    e = dict(os.environ)
    e["MEDIA_CONFIG_FILE"] = str(config)
    e["MEDIA_USAGE_LOG"] = str(tmp_path / "usage.jsonl")
    return e


def export(env, *args, expect=0) -> dict:
    proc = subprocess.run([sys.executable, "-m", "media_ai", "animation", "export", *args],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == expect, f"{args} -> {proc.returncode}: {proc.stdout}{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytestmark_media
@pytest.mark.parametrize("fmt,mime", [("gif", "image/gif"), ("webp", "image/webp"), ("apng", "image/apng")])
def test_every_container_produces_a_real_animation(clip, env, tmp_path, fmt, mime):
    """More than one frame is the whole claim, and the one thing a valid single-frame
    image cannot be distinguished from by exit code: ffmpeg returns 0 either way."""
    out = tmp_path / f"a.{fmt}"
    result = export(env, "--input", str(clip), "--output", str(out))
    assert result["ok"] and result["modality"] == "image"
    assert result["meta"]["scene"] == "animation.from_video"
    assert result["meta"]["binding"] == "local/ffmpeg"
    assert result["meta"]["frame_count"] == 24
    assert result["meta"]["size"] == [160, 120]
    assert [a["mime"] for a in result["artifacts"]] == [mime]
    assert Path(result["artifacts"][0]["path"]) == out and out.stat().st_size > 0


@pytestmark_media
@pytest.mark.parametrize("fmt", ["gif", "webp", "apng"])
def test_transparency_keys_the_background_out_in_every_container(clip, env, tmp_path, fmt):
    from PIL import Image

    out = tmp_path / f"k.{fmt}"
    result = export(env, "--input", str(clip), "--output", str(out), "--transparent")
    assert result["meta"]["transparent"] is True
    assert result["meta"]["frame_count"] == 24

    with Image.open(out) as im:
        im.seek(0)
        rgba = im.convert("RGBA")
    assert rgba.getpixel((2, 2))[3] == 0, "the flat green field should have become alpha"
    subject = rgba.getpixel((24, 60))
    assert subject[3] == 255 and min(subject[:3]) > 200, "the white block should stay opaque"


@pytestmark_media
def test_a_transparent_gif_says_its_alpha_is_one_bit(clip, env, tmp_path):
    """The caveat belongs in the result, not only in the docs: a caller comparing three
    outputs needs to know *why* the GIF's keyed edge is jagged."""
    result = export(env, "--input", str(clip), "--output", str(tmp_path / "k.gif"), "--transparent")
    notes = " ".join(result["meta"]["notes"]).lower()
    assert "1-bit" in notes and "webp" in notes


@pytestmark_media
@pytest.mark.parametrize("fmt,reader", [("gif", gif_plays), ("webp", webp_plays), ("apng", apng_plays)])
def test_the_requested_loop_count_reaches_the_file(clip, env, tmp_path, fmt, reader):
    """ffmpeg's GIF muxer takes `-loop 3` and writes "forever", so this asserts the
    bytes rather than the arguments — the difference between the two was the bug."""
    for asked, expected in ((0, 0), (3, 3), (7, 7)):
        out = tmp_path / f"l{asked}.{fmt}"
        export(env, "--input", str(clip), "--output", str(out), "--loop", str(asked))
        assert reader(out) == expected, f"{fmt} --loop {asked}"


@pytestmark_media
def test_play_once_is_expressed_by_omitting_the_gif_loop_block(clip, env, tmp_path):
    """No block is the one spelling of "play once" no decoder disagrees about."""
    out = tmp_path / "once.gif"
    export(env, "--input", str(clip), "--output", str(out), "--loop", "1")
    assert gif_plays(out) is None


@pytestmark_media
def test_a_patched_gif_is_still_a_valid_animation(clip, env, tmp_path):
    """Writing the loop count in by hand must not disturb anything else in the file."""
    from PIL import Image

    out = tmp_path / "p.gif"
    export(env, "--input", str(clip), "--output", str(out), "--loop", "5")
    with Image.open(out) as im:
        assert im.n_frames == 24
        assert im.info["loop"] == 5


@pytestmark_media
def test_bounce_appends_the_reversed_half_rather_than_truncating(clip, env, tmp_path):
    """With `-t` on the output instead of the input this came back at 24: the ping-pong
    was built and then cut back to the source's length."""
    plain = export(env, "--input", str(clip), "--output", str(tmp_path / "p.gif"))
    bounced = export(env, "--input", str(clip), "--output", str(tmp_path / "b.gif"), "--bounce")
    assert bounced["meta"]["frame_count"] == 2 * plain["meta"]["frame_count"]


@pytestmark_media
def test_bounce_survives_a_trim(clip, env, tmp_path):
    """The regression in one test: trim the source to 1s, then ping-pong it. A trim
    applied to the output would leave 12 frames instead of 24."""
    result = export(env, "--input", str(clip), "--output", str(tmp_path / "bt.gif"),
                    "--duration", "1", "--bounce")
    assert result["meta"]["frame_count"] == 24


@pytestmark_media
def test_speed_changes_how_long_the_animation_plays(clip, env, tmp_path):
    """Frame *count* is unchanged by `--speed` — the per-frame delay is what moves — so
    asserting the count would have passed no matter what happened here."""
    normal = tmp_path / "s1.gif"
    slow = tmp_path / "s2.gif"
    fast = tmp_path / "s3.gif"
    export(env, "--input", str(clip), "--output", str(normal))
    export(env, "--input", str(clip), "--output", str(slow), "--speed", "0.5")
    export(env, "--input", str(clip), "--output", str(fast), "--speed", "2")

    assert 1900 <= gif_millis(normal) <= 2100
    assert gif_millis(slow) > 1.8 * gif_millis(normal)
    assert gif_millis(fast) < 0.6 * gif_millis(normal)


@pytestmark_media
@pytest.mark.parametrize("flags,expected", [
    (["--size", "80x60"], [80, 60]),
    (["--max-width", "80"], [80, 60]),
    (["--max-height", "60"], [80, 60]),
    (["--max-width", "999"], [160, 120]),   # fit inside, never enlarge
])
def test_the_requested_geometry_is_what_comes_out(clip, env, tmp_path, flags, expected):
    result = export(env, "--input", str(clip), "--output", str(tmp_path / "g.webp"), *flags)
    assert result["meta"]["size"] == expected


@pytestmark_media
def test_trimming_takes_the_asked_for_span(clip, env, tmp_path):
    for flags, frames_out in ((["--duration", "1"], 12), (["--start", "0.5", "--end", "1.5"], 12),
                              (["--start", "1"], 12), (["--fps", "4"], 8)):
        result = export(env, "--input", str(clip), "--output", str(tmp_path / "t.gif"), *flags)
        assert result["meta"]["frame_count"] == frames_out, flags


@pytestmark_media
def test_a_frame_sequence_animates_and_reports_the_other_scene(clip, env, tmp_path):
    from media_ai.media.ffmpeg import run_ffmpeg

    seq = tmp_path / "seq"
    seq.mkdir()
    run_ffmpeg(["-i", str(clip), "-vf", "fps=6", str(seq / "f_%03d.png")])
    stills = sorted(seq.glob("*.png"))
    assert len(stills) == 12

    for source in ([str(seq)], [str(seq / "*.png")], [str(p) for p in stills]):
        result = export(env, "--frames", *source, "--output", str(tmp_path / "f.webp"), "--fps", "6")
        assert result["meta"]["scene"] == "animation.from_frames"
        assert result["meta"]["frame_count"] == 12, source
        assert result["meta"]["frames"] == source


@pytestmark_media
def test_a_partial_frame_list_is_refused_end_to_end(clip, env, tmp_path):
    from media_ai.media.ffmpeg import run_ffmpeg

    seq = tmp_path / "seq"
    seq.mkdir()
    run_ffmpeg(["-i", str(clip), "-vf", "fps=6", str(seq / "f_%03d.png")])
    stills = sorted(str(p) for p in seq.glob("*.png"))

    result = export(env, "--frames", *stills[:3], "--output", str(tmp_path / "f.gif"), expect=3)
    assert result["error"]["code"] == "animation_frames_extra"


@pytestmark_media
def test_the_ledger_attributes_the_encode_to_its_binding(clip, env, tmp_path):
    export(env, "--input", str(clip), "--output", str(tmp_path / "u.gif"))
    lines = [json.loads(x) for x in Path(env["MEDIA_USAGE_LOG"]).read_text().splitlines()]
    assert lines[-1]["binding"] == "local/ffmpeg"
    assert lines[-1]["scene"] == "animation.from_video"
    assert lines[-1]["kind"] == "animation" and lines[-1]["format"] == "gif"


@pytestmark_media
def test_naming_a_binding_that_cannot_animate_is_refused_before_any_work(clip, env, tmp_path):
    out = tmp_path / "n.gif"
    result = export(env, "--input", str(clip), "--output", str(out), "--binding", "mock/mock", expect=3)
    assert result["error"]["code"] == "scene_not_supported"
    assert not out.exists()
