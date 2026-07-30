"""Pillow-based image helpers.

Placeholder rendering for the offline mock provider (a caption card baked into the
frame, deterministic given ``(prompt, seed)``), plus :func:`save_image_bytes`, which
writes provider-returned image bytes to disk in the format the output path asks for.
Falls back to stdlib behavior when Pillow is unavailable.
"""

from __future__ import annotations

import hashlib
import io
import struct
import textwrap
import zlib
from pathlib import Path

# Output extension -> (Pillow format, mime). Drives :func:`save_image_bytes`.
_SUFFIX_FMT = {
    ".png": ("PNG", "image/png"), ".jpg": ("JPEG", "image/jpeg"), ".jpeg": ("JPEG", "image/jpeg"),
    ".webp": ("WEBP", "image/webp"), ".gif": ("GIF", "image/gif"),
}

# Leading bytes -> mime, for reporting what was actually written when no transcode ran.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


def sniff_image_mime(raw: bytes) -> str | None:
    """The mime of ``raw`` read from its magic bytes, or ``None`` if unrecognized."""
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in _MAGIC:
        if raw.startswith(magic):
            return mime
    return None


def save_image_bytes(raw: bytes, out: Path, *, source_mime: str | None = None) -> str:
    """Write ``raw`` image bytes to ``out``, converting to the format implied by
    ``out``'s extension when the source differs (e.g. a model that returns JPEG for
    an ``.png`` path). Returns the mime actually written.

    Best-effort: if the extension is unknown, Pillow is missing, or decoding fails, the
    bytes are written verbatim. The reported mime then describes **the bytes on disk** —
    sniffed from them, else the source mime — and never the target format, which no
    longer applies once the transcode did not happen. Reporting the extension's mime
    there was worse than saying nothing: a JPEG saved to an ``.png`` path came back as
    ``image/png``, and that mime travels in ``artifacts[]`` for a consumer to trust.
    """
    ensure_parent(out)
    target = _SUFFIX_FMT.get(out.suffix.lower())
    if target is None:  # unknown/absent extension — write verbatim, report the bytes
        out.write_bytes(raw)
        return (sniff_image_mime(raw) or source_mime or "application/octet-stream").lower()
    target_fmt, target_mime = target
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            if (im.format or "").upper() == target_fmt:
                out.write_bytes(raw)  # already the requested format — no re-encode
                return target_mime
            if target_fmt == "JPEG" and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")  # JPEG can't hold alpha/palette
            im.save(out, format=target_fmt)
        return target_mime
    except Exception:  # noqa: BLE001 - never fail a generation over a transcode hiccup
        out.write_bytes(raw)
        return (sniff_image_mime(raw) or source_mime or "application/octet-stream").lower()


def seed_int(prompt: str, seed: int | None) -> int:
    h = hashlib.sha256(f"{seed}:{prompt}".encode()).hexdigest()
    return int(h[:8], 16)


def palette(prompt: str, seed: int | None) -> tuple[int, int, int]:
    n = seed_int(prompt, seed)
    return 40 + (n & 0x7F), 40 + ((n >> 7) & 0x7F), 40 + ((n >> 14) & 0x7F)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_solid_png(path: Path, w: int, h: int, rgb: tuple[int, int, int]) -> None:
    r, g, b = rgb
    raw = bytearray()
    row = bytes([r, g, b]) * w
    for _ in range(h):
        raw.append(0)
        raw.extend(row)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    )


def draw_caption_image(
    path: Path, *, title: str, prompt: str, w: int, h: int, rgb: tuple[int, int, int], base_image: Path | None = None
) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        write_solid_png(path, w, h, rgb)
        return

    if base_image is not None and Path(base_image).is_file():
        with Image.open(base_image) as base:  # close the file handle promptly (batch runs)
            img = base.convert("RGB")
        sw, sh = img.size
        scale = max(w / sw, h / sh)
        img = img.resize((max(1, int(sw * scale)), max(1, int(sh * scale))))
        left, top = (img.size[0] - w) // 2, (img.size[1] - h) // 2
        img = img.crop((left, top, left + w, top + h))
        img = Image.blend(img, Image.new("RGB", (w, h), (10, 10, 15)), 0.35)
    else:
        img = Image.new("RGB", (w, h), rgb)

    draw = ImageDraw.Draw(img)

    def font(size: int):
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    draw.rectangle([0, 0, w, 46], fill=(0, 0, 0))
    draw.text((16, 12), title, fill=(255, 220, 120), font=font(24))
    body = textwrap.fill(prompt.strip(), width=max(20, w // 12))[:600]
    draw.multiline_text((16, 64), body, fill=(240, 240, 240), font=font(22), spacing=6)
    draw.text((16, h - 30), f"{w}x{h} · placeholder (mock provider)", fill=(180, 180, 180), font=font(16))
    img.save(path)
