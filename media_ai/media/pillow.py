"""Pillow-based placeholder image rendering for the offline mock provider.

Draws a caption card (the prompt baked into the frame) so mock artifacts are
visually distinguishable and deterministic given ``(prompt, seed)``. Falls back to
a solid-color PNG written with the stdlib if Pillow is unavailable.
"""

from __future__ import annotations

import hashlib
import struct
import textwrap
import zlib
from pathlib import Path


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
        img = Image.open(base_image).convert("RGB")
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
