"""``media/pillow.py`` — writing provider-returned image bytes to the caller's path.

The mime this returns travels in ``artifacts[]`` for a consumer to trust, so the tests
below are about one thing: **the reported mime describes the bytes on disk.** It is a
best-effort transcoder (a model that returns JPEG for an ``.png`` path is common), and
every path that skips the transcode has to stop claiming the extension's format.
"""

from __future__ import annotations

import io

import pytest

from media_ai.media.pillow import save_image_bytes, sniff_image_mime


def _jpeg_bytes() -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def test_jpeg_bytes_written_to_a_png_path_are_transcoded(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    out = tmp_path / "o.png"
    assert save_image_bytes(_jpeg_bytes(), out, source_mime="image/jpeg") == "image/png"
    with Image.open(out) as im:
        assert im.format == "PNG"  # the reported mime is what is actually on disk


def test_matching_format_is_written_verbatim(tmp_path):
    out = tmp_path / "o.jpg"
    raw = _jpeg_bytes()
    assert save_image_bytes(raw, out) == "image/jpeg"
    assert out.read_bytes() == raw  # no needless re-encode


def test_a_failed_decode_reports_the_bytes_not_the_extension(tmp_path):
    """The transcode did not happen, so the target format no longer applies.

    Returning it anyway was the worst of the three options: a JPEG saved to an ``.png``
    path came back as ``image/png``, and the caller has no way to notice. The bytes are
    still on disk and still sniffable, so report what they are.
    """
    out = tmp_path / "o.png"
    raw = b"\xff\xd8\xff" + b"truncated-jpeg"  # JPEG magic, undecodable
    assert save_image_bytes(raw, out) == "image/jpeg"
    assert out.read_bytes() == raw


def test_without_pillow_the_bytes_still_describe_themselves(tmp_path, monkeypatch):
    out = tmp_path / "o.png"
    raw = _jpeg_bytes()  # built while Pillow is still importable
    monkeypatch.setitem(__import__("sys").modules, "PIL", None)  # the import inside the function fails
    assert save_image_bytes(raw, out, source_mime="image/jpeg") == "image/jpeg"
    assert out.read_bytes() == raw


def test_unsniffable_bytes_fall_back_to_the_source_mime_then_octet_stream(tmp_path):
    out = tmp_path / "o.png"
    assert save_image_bytes(b"not an image at all", out, source_mime="image/avif") == "image/avif"
    assert save_image_bytes(b"not an image at all", tmp_path / "b.png") == "application/octet-stream"


def test_an_unknown_extension_is_written_verbatim_and_sniffed(tmp_path):
    raw = _jpeg_bytes()
    assert save_image_bytes(raw, tmp_path / "o.bin") == "image/jpeg"
    assert save_image_bytes(b"nope", tmp_path / "p.bin", source_mime="image/heic") == "image/heic"


@pytest.mark.parametrize("raw, mime", [
    (b"\x89PNG\r\n\x1a\n" + b"\x00", "image/png"),
    (b"\xff\xd8\xff\xe0", "image/jpeg"),
    (b"GIF89a....", "image/gif"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
    (b"RIFF\x00\x00\x00\x00WAVEfmt ", None),  # a RIFF container that is not WebP
    (b"", None),
])
def test_sniff_image_mime(raw, mime):
    assert sniff_image_mime(raw) == mime
