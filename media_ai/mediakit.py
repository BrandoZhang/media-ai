"""Shared implementation for the multimodal *creation* tools.

Single source of truth behind the CLI executables in ``cli/``
(``text2image``, ``image2image``, ``text2video``, ``image2video``,
``ref2video``, ``concat_video``, ``video_task``, ``media_usage``).

Two backends implement the same :class:`Backend` protocol:

* :class:`MockBackend` (default): fully offline. Draws placeholder images
  with Pillow (the prompt is baked into the frame) and turns them into
  short clips with ffmpeg. Deterministic given ``(prompt, seed)``.
* :class:`VolcBackend` (opt-in): calls Volcengine's **Ark** API
  (``https://ark.cn-beijing.volces.com/api/v3``) with **API-Key (Bearer)**
  auth. Image generation is synchronous (``/images/generations``); video
  generation is an async task (``/contents/generations/tasks`` create ->
  poll -> optional cancel). Covers text->image, (multi-)image reference
  ->image, group images, text->video, image->video (first / first+last
  frame), and multimodal-reference->video (images+videos+audio).

Every generation records a line to a **usage ledger** (JSONL) so cost can
be tracked and used as an evaluation metric. The mock backend synthesizes
token counts with the same formulas the real API documents, so the
cost-tracking path is exercised offline.

Refs:
- image: https://www.volcengine.com/docs/82379/1541523
- video create: https://www.volcengine.com/docs/82379/1520757
- video query:  https://www.volcengine.com/docs/82379/1521309
- video cancel: https://www.volcengine.com/docs/82379/1521720
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import shutil
import signal
import struct
import subprocess
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# constants / small helpers
# --------------------------------------------------------------------------

DEFAULT_W = 768
DEFAULT_H = 432  # 16:9
DEFAULT_VIDEO_SECONDS = 5
DEFAULT_FPS = 24
MOCK_RENDER_H = 360  # mock clips render small (fast); billed at requested resolution

ARK_BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

# Seedream 4.5/5.0 method-2 requires total pixels in [2560x1440, 4096x4096].
# Small demo defaults (e.g. 768x432) are below the floor and would be rejected
# by the real API, so the Volc image path resolves to a valid size.
_ARK_MIN_IMAGE_PIXELS = 2560 * 1440


def _volc_image_size(width: int, height: int) -> str:
    """Resolve a valid Ark image ``size``. ``$ARK_IMAGE_SIZE`` overrides; a
    below-floor W×H falls back to the ``2K`` named preset (the model then picks
    dimensions from the prompt)."""
    override = os.getenv("ARK_IMAGE_SIZE")
    if override:
        return override
    if width * height >= _ARK_MIN_IMAGE_PIXELS:
        return f"{width}x{height}"
    return "2K"


# Video resolution/ratio -> (w, h). Used for cost accounting (tokens ~ pixels).
_VIDEO_DIMS: dict[str, dict[str, tuple[int, int]]] = {
    "480p": {
        "16:9": (864, 480),
        "9:16": (480, 864),
        "1:1": (640, 640),
        "4:3": (736, 544),
        "3:4": (544, 736),
        "21:9": (960, 416),
    },
    "720p": {
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "1:1": (960, 960),
        "4:3": (1120, 832),
        "3:4": (832, 1120),
        "21:9": (1504, 640),
    },
    "1080p": {
        "16:9": (1920, 1080),
        "9:16": (1080, 1920),
        "1:1": (1440, 1440),
        "4:3": (1664, 1248),
        "3:4": (1248, 1664),
        "21:9": (2176, 928),
    },
}


class MediaError(RuntimeError):
    """Raised for any recoverable failure so the CLI can print a clean message."""


def _seed_int(prompt: str, seed: int | None) -> int:
    h = hashlib.sha256(f"{seed}:{prompt}".encode()).hexdigest()
    return int(h[:8], 16)


def _palette(prompt: str, seed: int | None) -> tuple[int, int, int]:
    n = _seed_int(prompt, seed)
    return 40 + (n & 0x7F), 40 + ((n >> 7) & 0x7F), 40 + ((n >> 14) & 0x7F)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def video_dims(resolution: str, ratio: str) -> tuple[int, int]:
    """Best-effort (w, h) for a resolution+ratio, for cost accounting.

    Resolution/ratio are normalized (case, whitespace) so a ``480P`` or
    `` 480p `` doesn't silently fall back to the 720p default and mis-bill the
    clip at ~2x its real pixel cost.
    """
    res = _VIDEO_DIMS.get((resolution or "").strip().lower(), _VIDEO_DIMS["720p"])
    ratio = (ratio or "").strip().lower()
    if ratio == "adaptive":
        ratio = "16:9"
    return res.get(ratio, res["16:9"])


# --------------------------------------------------------------------------
# usage ledger (cost tracking)
# --------------------------------------------------------------------------


def usage_log_path() -> Path:
    """Where usage lines are appended. ``$MEDIA_USAGE_LOG`` or ``./media_usage.jsonl``."""
    return Path(os.getenv("MEDIA_USAGE_LOG", "media_usage.jsonl")).expanduser()


_LEDGER_LOCK = threading.Lock()


def record_usage(entry: dict) -> None:
    """Append one usage record (JSONL). Best-effort: never raises.

    Guarded by a lock so concurrent (batch) generations don't interleave
    partial lines in the ledger.
    """
    try:
        entry = {"ts": round(time.time(), 3), **entry}
        path = usage_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with _LEDGER_LOCK, path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001 - accounting must never break generation
        pass


def summarize_usage(path: Path | None = None) -> dict:
    """Aggregate the ledger into totals (the cost metric)."""
    path = path or usage_log_path()
    totals = {
        "calls": 0,
        "images_generated": 0,
        "video_seconds": 0,
        "total_tokens": 0,
        "by_tool": {},
        "by_backend": {},
    }
    if not path.is_file():
        return totals
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        totals["calls"] += 1
        totals["images_generated"] += int(e.get("generated_images", 0) or 0)
        totals["video_seconds"] += int(e.get("seconds", 0) or 0)
        tok = int(e.get("total_tokens", 0) or 0)
        totals["total_tokens"] += tok
        totals["by_tool"][e.get("tool", "?")] = totals["by_tool"].get(e.get("tool", "?"), 0) + tok
        totals["by_backend"][e.get("backend", "?")] = totals["by_backend"].get(e.get("backend", "?"), 0) + tok
    return totals


# --------------------------------------------------------------------------
# ffmpeg + Pillow helpers
# --------------------------------------------------------------------------


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise MediaError(
            "ffmpeg not found. Install it (`apt install ffmpeg`) or `pip install imageio-ffmpeg`."
        ) from exc


def _run_ffmpeg(args: list[str]) -> None:
    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-8:]
        raise MediaError("ffmpeg failed:\n" + "\n".join(tail))


def _write_solid_png(path: Path, w: int, h: int, rgb: tuple[int, int, int]) -> None:
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


def _draw_caption_image(
    path: Path, *, title: str, prompt: str, w: int, h: int, rgb: tuple[int, int, int], base_image: Path | None = None
) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        _write_solid_png(path, w, h, rgb)
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
    draw.text((16, h - 30), f"{w}x{h} · placeholder (mock backend)", fill=(180, 180, 180), font=font(16))
    img.save(path)


def _image_to_clip(image: Path, out: Path, *, seconds: int, fps: int, w: int, h: int) -> None:
    _ensure_parent(out)
    total = max(1, seconds * fps)
    vf = f"scale={w * 2}:{h * 2},zoompan=z='min(zoom+0.0012,1.12)':d={total}:s={w}x{h}:fps={fps},format=yuv420p"
    try:
        _run_ffmpeg(
            [
                "-loop",
                "1",
                "-i",
                str(image),
                "-t",
                str(seconds),
                "-r",
                str(fps),
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(out),
            ]
        )
    except MediaError:
        _run_ffmpeg(
            [
                "-loop",
                "1",
                "-i",
                str(image),
                "-t",
                str(seconds),
                "-r",
                str(fps),
                "-vf",
                f"scale={w}:{h},format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(out),
            ]
        )


def _has_audio(path: Path) -> bool:
    """True if the media file carries an audio stream. Uses the bundled ffmpeg
    (no separate ffprobe needed): ``ffmpeg -i <file>`` prints stream info to
    stderr and exits non-zero (no output specified), which we tolerate."""
    try:
        proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    except Exception:  # noqa: BLE001 - treat probe failure as "no audio"
        return False
    return "Audio:" in (proc.stderr or "")


def concat_clips(
    inputs: list[Path], out: Path, *, w: int = DEFAULT_W, h: int = DEFAULT_H, fps: int = DEFAULT_FPS
) -> Path:
    inputs = [Path(p) for p in inputs]
    for p in inputs:
        if not p.is_file():
            raise MediaError(f"input clip not found: {p}")
    if not inputs:
        raise MediaError("concat needs at least one input clip")
    _ensure_parent(out)
    # Preserve audio when every clip has an audio track (e.g. Seedance shots
    # generated with generate_audio) — otherwise the joined film would be
    # silent. Mixed audio/no-audio inputs can't be merged without synthesizing
    # silence, so we fall back to a video-only join there (the safe default).
    keep_audio = all(_has_audio(p) for p in inputs)
    args: list[str] = []
    for p in inputs:
        args += ["-i", str(p)]
    filters, labels = [], ""
    for i in range(len(inputs)):
        filters.append(f"[{i}:v]scale={w}:{h},fps={fps},format=yuv420p,setsar=1[v{i}]")
        labels += f"[v{i}]"
        if keep_audio:
            filters.append(f"[{i}:a]aresample=async=1[a{i}]")
            labels += f"[a{i}]"
    n = len(inputs)
    fc = ";".join(filters) + (
        f";{labels}concat=n={n}:v=1:a=1[outv][outa]" if keep_audio else f";{labels}concat=n={n}:v=1:a=0[outv]"
    )
    args += ["-filter_complex", fc, "-map", "[outv]"]
    if keep_audio:
        args += ["-map", "[outa]", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)]
    _run_ffmpeg(args)
    return out


# --------------------------------------------------------------------------
# result + backend protocol
# --------------------------------------------------------------------------


@dataclass
class GenResult:
    path: Path
    backend: str
    kind: str  # image | video
    usage: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    extra_paths: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": True,
                "kind": self.kind,
                "path": str(self.path),
                "backend": self.backend,
                "bytes": self.path.stat().st_size if self.path.is_file() else 0,
                "usage": self.usage,
                "extra_paths": self.extra_paths,
                "meta": self.meta,
            },
            ensure_ascii=False,
        )


def dumps_result(res) -> str:
    """Serialize a backend result: a ``GenResult`` (sync) or a plain dict
    (async submit / ``video_task``)."""
    return res.to_json() if hasattr(res, "to_json") else json.dumps(res, ensure_ascii=False)


class Backend:
    name = "base"

    def text2image(self, *, prompt, out, width, height, seed, max_images=1, model=None): ...
    def image2image(self, *, prompt, images, out, strength, seed, max_images=1, model=None): ...
    def text2video(
        self,
        *,
        prompt,
        out,
        seconds,
        resolution,
        ratio,
        seed,
        camera_fixed,
        watermark,
        generate_audio,
        wait=True,
        model=None,
    ): ...
    def image2video(
        self,
        *,
        prompt,
        first_frame,
        last_frame,
        out,
        seconds,
        resolution,
        ratio,
        seed,
        camera_fixed,
        watermark,
        generate_audio,
        return_last_frame,
        wait=True,
        model=None,
    ): ...
    def ref2video(
        self,
        *,
        prompt,
        images,
        videos,
        audios,
        out,
        seconds,
        resolution,
        ratio,
        seed,
        watermark,
        generate_audio,
        wait=True,
        model=None,
    ): ...
    def video_task(self, *, op, task_id, output=None) -> dict: ...


# --------------------------------------------------------------------------
# Mock backend (offline, default)
# --------------------------------------------------------------------------


def _mock_image_tokens(w: int, h: int, n: int) -> dict:
    # image output_tokens = generated_images * floor(w*h/256)  (per Ark docs)
    per = (w * h) // 256
    return {"generated_images": n, "output_tokens": per * n, "total_tokens": per * n}


def _mock_video_tokens(w: int, h: int, seconds: int) -> dict:
    # synthetic: completion_tokens ~ floor(pixels * seconds / 1024)
    tok = (w * h * seconds) // 1024
    return {"completion_tokens": tok, "total_tokens": tok}


class MockBackend(Backend):
    name = "mock"

    def _img(self, title, prompt, out, w, h, seed, base=None, n=1, model=None):
        out = Path(out)
        _ensure_parent(out)
        _draw_caption_image(out, title=title, prompt=prompt, w=w, h=h, rgb=_palette(prompt, seed), base_image=base)
        extra = []
        for i in range(2, n + 1):  # group images
            p = out.with_name(f"{out.stem}_{i}{out.suffix}")
            _draw_caption_image(
                p,
                title=f"{title} ({i}/{n})",
                prompt=prompt,
                w=w,
                h=h,
                rgb=_palette(prompt + str(i), seed),
                base_image=base,
            )
            extra.append(str(p))
        usage = _mock_image_tokens(w, h, n)
        record_usage({"tool": title.split()[-1], "backend": self.name, "kind": "image", "generated_images": n, **usage})
        return GenResult(
            out,
            self.name,
            "image",
            usage=usage,
            meta={"prompt": prompt, "seed": seed, "size": [w, h], "model": model},
            extra_paths=extra,
        )

    def text2image(self, *, prompt, out, width, height, seed, max_images=1, model=None):
        return self._img("mock text2image", prompt, out, width, height, seed, n=max_images, model=model)

    def image2image(self, *, prompt, images, out, strength, seed, max_images=1, model=None):
        images = [Path(p) for p in (images or [])]
        for p in images:
            if not p.is_file():
                raise MediaError(f"reference image not found: {p}")
        base = images[0] if images else None
        r = self._img("mock image2image", prompt, out, DEFAULT_W, DEFAULT_H, seed, base=base, n=max_images, model=model)
        r.meta.update({"refs": [str(p) for p in images], "strength": strength})
        return r

    def _video(
        self, title, prompt, out, seconds, resolution, ratio, seed, base=None, return_last_frame=False, model=None
    ):
        out = Path(out)
        _ensure_parent(out)
        bw, bh = video_dims(resolution, ratio)  # billed dims
        rh = min(MOCK_RENDER_H, bh)
        rw = max(2, (bw * rh // bh) // 2 * 2)  # keep even
        with tempfile.TemporaryDirectory() as td:
            frame = Path(td) / "frame.png"
            _draw_caption_image(
                frame, title=title, prompt=prompt, w=rw, h=rh, rgb=_palette(prompt, seed), base_image=base
            )
            _image_to_clip(frame, out, seconds=seconds, fps=DEFAULT_FPS, w=rw, h=rh)
        extra = []
        if return_last_frame:
            lf = out.with_name(f"{out.stem}_lastframe.png")
            _draw_caption_image(
                lf,
                title=f"{title} · last frame",
                prompt=prompt,
                w=rw,
                h=rh,
                rgb=_palette(prompt, seed),
                base_image=base,
            )
            extra.append(str(lf))
        usage = _mock_video_tokens(bw, bh, seconds)  # bill at requested resolution
        record_usage(
            {
                "tool": title.split()[-1],
                "backend": self.name,
                "kind": "video",
                "seconds": seconds,
                "resolution": resolution,
                **usage,
            }
        )
        return GenResult(
            out,
            self.name,
            "video",
            usage=usage,
            meta={
                "prompt": prompt,
                "seconds": seconds,
                "seed": seed,
                "resolution": resolution,
                "ratio": ratio,
                "render_size": [rw, rh],
                "model": model,
            },
            extra_paths=extra,
        )

    def text2video(
        self,
        *,
        prompt,
        out,
        seconds,
        resolution,
        ratio,
        seed,
        camera_fixed,
        watermark,
        generate_audio,
        wait=True,
        model=None,
    ):
        # mock generation is synchronous; `wait` is accepted for API parity.
        return self._video("mock text2video", prompt, out, seconds, resolution, ratio, seed, model=model)

    def image2video(
        self,
        *,
        prompt,
        first_frame,
        last_frame,
        out,
        seconds,
        resolution,
        ratio,
        seed,
        camera_fixed,
        watermark,
        generate_audio,
        return_last_frame,
        wait=True,
        model=None,
    ):
        ff = Path(first_frame)
        if not ff.is_file():
            raise MediaError(f"first-frame image not found: {ff}")
        note = prompt + (f"  [+last_frame:{Path(last_frame).name}]" if last_frame else "")
        return self._video(
            "mock image2video",
            note,
            out,
            seconds,
            resolution,
            ratio,
            seed,
            base=ff,
            return_last_frame=return_last_frame,
            model=model,
        )

    def ref2video(
        self,
        *,
        prompt,
        images,
        videos,
        audios,
        out,
        seconds,
        resolution,
        ratio,
        seed,
        watermark,
        generate_audio,
        wait=True,
        model=None,
    ):
        images = [Path(p) for p in (images or [])]
        base = images[0] if images and images[0].is_file() else None
        tag = f"  [refs img:{len(images)} vid:{len(videos or [])} aud:{len(audios or [])}]"
        return self._video(
            "mock ref2video", prompt + tag, out, seconds, resolution, ratio, seed, base=base, model=model
        )

    def video_task(self, *, op, task_id, output=None) -> dict:
        return {
            "ok": True,
            "backend": self.name,
            "op": op,
            "task_id": task_id,
            "note": "mock backend generates videos synchronously; there is no async task to query/cancel.",
        }


# --------------------------------------------------------------------------
# Volcengine Ark backend (opt-in). API-Key (Bearer) auth.
# --------------------------------------------------------------------------


def _env(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return default


def _data_uri(path: Path, media: str = "image") -> str:
    """Encode a local file as a ``data:<media>/<ext>;base64,...`` URI."""
    path = Path(path)
    ext = path.suffix.lstrip(".").lower() or ("png" if media == "image" else "mp4")
    if ext == "jpg":
        ext = "jpeg"
    return f"data:{media}/{ext};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _as_url_or_datauri(ref: str, media: str) -> str:
    """Pass through http(s)/asset URIs; base64-encode local files."""
    if ref.startswith(("http://", "https://", "asset://", "data:")):
        return ref
    return _data_uri(Path(ref), media)


class VolcBackend(Backend):
    """Volcengine Ark API backend (API-Key / Bearer auth).

    Model IDs change over time and must be opened in your console; set them
    via env (``ARK_IMAGE_MODEL`` / ``ARK_VIDEO_MODEL``). The defaults are
    placeholders — override them with the exact Model IDs shown for your
    account (https://www.volcengine.com/docs/82379/1330310).
    """

    name = "volc"

    def __init__(self) -> None:
        self.api_key = _env("ARK_API_KEY")
        if not self.api_key:
            raise MediaError(
                "Volc backend needs an Ark API key: set ARK_API_KEY (long-lived key from the Volcengine console)."
            )
        self.base = ARK_BASE_URL.rstrip("/")
        # Default Model IDs (a per-call ``model=`` or ``$ARK_IMAGE_MODEL`` /
        # ``$ARK_VIDEO_MODEL`` override these). Model IDs are account-specific
        # and must be enabled in the console -- see the model list at
        # https://www.volcengine.com/docs/82379/1330310 .
        self.image_model = _env("ARK_IMAGE_MODEL", default="doubao-seedream-4-5-251128")
        self.video_model = _env("ARK_VIDEO_MODEL", default="doubao-seedance-2-0-260128")
        self.poll_interval = float(_env("ARK_POLL_INTERVAL", default="5") or 5)
        self.poll_timeout = float(_env("ARK_POLL_TIMEOUT", default="900") or 900)

    # ---- HTTP ----
    # Statuses worth retrying: rate limit (429) + transient server errors.
    _RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        """POST/GET/DELETE against Ark with retry + exponential backoff on rate
        limits (429) and transient 5xx. ``ARK_MAX_RETRIES`` / ``ARK_RETRY_BASE``
        tune it (default 4 retries, 2s base)."""
        url = f"{self.base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        max_retries = int(os.getenv("ARK_MAX_RETRIES", "4"))
        base_delay = float(os.getenv("ARK_RETRY_BASE", "2"))
        # A 429 means the request was rejected (rate limited) and NOT processed,
        # so it is always safe to retry. Transient 5xx / network errors, however,
        # may fire after the server already created a (billed) task, so retrying a
        # non-idempotent POST could double-submit. Only retry those on idempotent
        # methods (GET/DELETE).
        idempotent = method in ("GET", "DELETE")
        for attempt in range(max_retries + 1):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", f"Bearer {self.api_key}")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                retryable = e.code == 429 or (e.code in self._RETRY_STATUSES and idempotent)
                if retryable and attempt < max_retries:
                    # honor Retry-After when present, else exponential backoff + jitter
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    delay = float(retry_after) if (retry_after and retry_after.isdigit()) else base_delay * (2**attempt)
                    time.sleep(delay + random.uniform(0, 0.5))
                    continue
                detail = e.read().decode("utf-8", "replace")[:500]
                raise MediaError(f"Ark API HTTP {e.code}: {detail}") from None
            except urllib.error.URLError as exc:
                if idempotent and attempt < max_retries:
                    time.sleep(base_delay * (2**attempt) + random.uniform(0, 0.5))
                    continue
                raise MediaError(f"Ark API request failed: {exc}") from None
        raise MediaError("Ark API request failed after retries")

    @staticmethod
    def _download(url: str, out: Path) -> Path:
        _ensure_parent(out)
        with urllib.request.urlopen(url, timeout=180) as resp:  # noqa: S310
            out.write_bytes(resp.read())
        return out

    # ---- images ----
    def _images_generation(self, *, prompt, images, out, size, max_images, tool, model=None, seed=None):
        out = Path(out)
        model_id = model or self.image_model
        body: dict = {
            "model": model_id,
            "prompt": prompt,
            "size": size,
            "response_format": "url",
            "watermark": False,
        }
        # Forward the seed for reproducibility (the tool/CLI advertise --seed);
        # mirrors the video path. A negative sentinel means "unset".
        if seed is not None and seed >= 0:
            body["seed"] = seed
        if images:
            enc = [_as_url_or_datauri(str(p), "image") for p in images]
            body["image"] = enc if len(enc) > 1 else enc[0]
        if max_images and max_images > 1:
            body["sequential_image_generation"] = "auto"
            body["sequential_image_generation_options"] = {"max_images": max_images}
        else:
            body["sequential_image_generation"] = "disabled"
        data = self._request("POST", "/images/generations", body)
        items = [d for d in (data.get("data") or []) if d.get("url") or d.get("b64_json")]
        if not items:
            raise MediaError(f"Ark image response had no images: {json.dumps(data)[:400]}")
        self._save_image_item(items[0], out)
        extra = []
        for i, it in enumerate(items[1:], start=2):
            p = out.with_name(f"{out.stem}_{i}{out.suffix}")
            self._save_image_item(it, p)
            extra.append(str(p))
        usage = data.get("usage") or {}
        used_model = data.get("model") or model_id
        record_usage(
            {
                "tool": tool,
                "backend": self.name,
                "model": used_model,
                "kind": "image",
                "generated_images": usage.get("generated_images", len(items)),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        )
        return GenResult(
            out,
            self.name,
            "image",
            usage=usage,
            meta={"prompt": prompt, "model": used_model, "size": size},
            extra_paths=extra,
        )

    @staticmethod
    def _save_image_item(item: dict, out: Path) -> None:
        _ensure_parent(out)
        if item.get("b64_json"):
            out.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            VolcBackend._download(item["url"], out)

    def text2image(self, *, prompt, out, width, height, seed, max_images=1, model=None):
        return self._images_generation(
            prompt=prompt,
            images=None,
            out=out,
            size=_volc_image_size(width, height),
            max_images=max_images,
            tool="text2image",
            model=model,
            seed=seed,
        )

    def image2image(self, *, prompt, images, out, strength, seed, max_images=1, model=None):
        return self._images_generation(
            prompt=prompt,
            images=[Path(p) for p in (images or [])],
            out=out,
            size=os.getenv("ARK_IMAGE_SIZE", "2K"),
            max_images=max_images,
            tool="image2image",
            model=model,
            seed=seed,
        )

    # ---- video (async task) ----
    def _create_video_task(
        self,
        *,
        content: list[dict],
        seconds,
        resolution,
        ratio,
        seed,
        camera_fixed=False,
        watermark=False,
        generate_audio=None,
        return_last_frame=False,
        model=None,
    ) -> str:
        body: dict = {
            "model": model or self.video_model,
            "content": content,
            "resolution": resolution,
            "ratio": ratio,
            "duration": seconds,
            "camera_fixed": camera_fixed,
            "watermark": watermark,
        }
        if seed is not None and seed >= 0:
            body["seed"] = seed
        if generate_audio is not None:
            body["generate_audio"] = generate_audio
        if return_last_frame:
            body["return_last_frame"] = True
        data = self._request("POST", "/contents/generations/tasks", body)
        task_id = data.get("id")
        if not task_id:
            raise MediaError(f"Ark video create returned no task id: {json.dumps(data)[:400]}")
        return task_id

    def _finalize_video(
        self, res: dict, out: Path, *, task_id: str, tool: str, seconds: int, resolution: str
    ) -> GenResult:
        """Download a succeeded task's video (+ last frame) and record its usage."""
        out = Path(out)
        content = res.get("content") or {}
        if not content.get("video_url"):
            raise MediaError(f"Ark task {task_id} succeeded but no video_url: {json.dumps(res)[:400]}")
        self._download(content["video_url"], out)
        extra = []
        if content.get("last_frame_url"):
            lf = out.with_name(f"{out.stem}_lastframe.png")
            self._download(content["last_frame_url"], lf)
            extra.append(str(lf))
        usage = res.get("usage") or {}
        used_model = res.get("model") or self.video_model
        record_usage(
            {
                "tool": tool,
                "backend": self.name,
                "model": used_model,
                "kind": "video",
                "seconds": res.get("duration", seconds),
                "resolution": res.get("resolution", resolution),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        )
        return GenResult(
            out,
            self.name,
            "video",
            usage=usage,
            meta={
                "task_id": task_id,
                "model": used_model,
                "seconds": res.get("duration", seconds),
                "resolution": res.get("resolution", resolution),
                "ratio": res.get("ratio"),
            },
            extra_paths=extra,
        )

    def _submitted(self, task_id: str, out: Path, *, tool: str) -> dict:
        """Async-submit result: return the task id without waiting.

        Poll with ``video_task --op query --id <task_id> --output <out>``; when
        it succeeds the video is downloaded to ``out``. This lets the agent
        yield its concurrency slot instead of blocking for minutes.
        """
        return {
            "ok": True,
            "kind": "video",
            "backend": self.name,
            "status": "queued",
            "task_id": task_id,
            "tool": tool,
            "output": str(out),
            "note": "submitted; poll with `video_task --op query --id <task_id> --output <output>`.",
        }

    def _cancel_task(self, task_id: str) -> None:
        """Best-effort cancel/delete of an in-flight task (never raises)."""
        try:
            self._request("DELETE", f"/contents/generations/tasks/{task_id}")
        except Exception:  # noqa: BLE001 - cancellation is best-effort
            pass

    def _poll_video(self, task_id: str, out: Path, *, tool: str, seconds: int, resolution: str) -> GenResult:
        out = Path(out)

        # The harness may kill this (blocking) tool at its action_timeout, which
        # is typically shorter than poll_timeout. Without cancellation the Ark
        # task keeps running and billing. Cancel the task if we're signalled
        # (SIGTERM/SIGINT) or if we hit our own timeout, so a killed wait=True
        # call doesn't orphan a billed task. (SIGKILL can't be caught — prefer
        # wait=false + video_task, or set action_timeout >= ARK_POLL_TIMEOUT.)
        def _on_signal(signum, _frame):
            self._cancel_task(task_id)
            raise MediaError(f"Ark video task {task_id} interrupted (signal {signum}); task cancelled")

        prev_handlers: dict = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                prev_handlers[sig] = signal.signal(sig, _on_signal)
            except (ValueError, OSError):
                pass  # not in the main thread; skip signal handling

        try:
            deadline = time.monotonic() + self.poll_timeout
            while time.monotonic() < deadline:
                res = self._request("GET", f"/contents/generations/tasks/{task_id}")
                status = str(res.get("status", "")).lower()
                if status == "succeeded":
                    return self._finalize_video(
                        res, out, task_id=task_id, tool=tool, seconds=seconds, resolution=resolution
                    )
                if status in ("failed", "cancelled", "expired"):
                    raise MediaError(f"Ark video task {task_id} {status}: {json.dumps(res.get('error') or res)[:400]}")
                time.sleep(self.poll_interval)
            self._cancel_task(task_id)
            raise MediaError(f"Ark video task {task_id} timed out after {self.poll_timeout}s (id={task_id}; cancelled)")
        finally:
            for sig, handler in prev_handlers.items():
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):
                    pass

    def text2video(
        self,
        *,
        prompt,
        out,
        seconds,
        resolution,
        ratio,
        seed,
        camera_fixed,
        watermark,
        generate_audio,
        wait=True,
        model=None,
    ):
        content = [{"type": "text", "text": prompt}]
        tid = self._create_video_task(
            content=content,
            seconds=seconds,
            resolution=resolution,
            ratio=ratio,
            seed=seed,
            camera_fixed=camera_fixed,
            watermark=watermark,
            generate_audio=generate_audio,
            model=model,
        )
        if not wait:
            return self._submitted(tid, Path(out), tool="text2video")
        return self._poll_video(tid, out, tool="text2video", seconds=seconds, resolution=resolution)

    def image2video(
        self,
        *,
        prompt,
        first_frame,
        last_frame,
        out,
        seconds,
        resolution,
        ratio,
        seed,
        camera_fixed,
        watermark,
        generate_audio,
        return_last_frame,
        wait=True,
        model=None,
    ):
        content: list[dict] = [
            {
                "type": "image_url",
                "image_url": {"url": _as_url_or_datauri(str(first_frame), "image")},
                "role": "first_frame",
            }
        ]
        if last_frame:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _as_url_or_datauri(str(last_frame), "image")},
                    "role": "last_frame",
                }
            )
        if prompt:
            content.append({"type": "text", "text": prompt})
        tid = self._create_video_task(
            content=content,
            seconds=seconds,
            resolution=resolution,
            ratio=ratio,
            seed=seed,
            camera_fixed=camera_fixed,
            watermark=watermark,
            generate_audio=generate_audio,
            return_last_frame=return_last_frame,
            model=model,
        )
        if not wait:
            return self._submitted(tid, Path(out), tool="image2video")
        return self._poll_video(tid, out, tool="image2video", seconds=seconds, resolution=resolution)

    def ref2video(
        self,
        *,
        prompt,
        images,
        videos,
        audios,
        out,
        seconds,
        resolution,
        ratio,
        seed,
        watermark,
        generate_audio,
        wait=True,
        model=None,
    ):
        content: list[dict] = []
        for p in images or []:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _as_url_or_datauri(str(p), "image")},
                    "role": "reference_image",
                }
            )
        for p in videos or []:
            content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": _as_url_or_datauri(str(p), "video")},
                    "role": "reference_video",
                }
            )
        for p in audios or []:
            content.append(
                {
                    "type": "audio_url",
                    "audio_url": {"url": _as_url_or_datauri(str(p), "audio")},
                    "role": "reference_audio",
                }
            )
        if not content:
            raise MediaError("ref2video needs at least one reference image or video.")
        if prompt:
            content.append({"type": "text", "text": prompt})
        tid = self._create_video_task(
            content=content,
            seconds=seconds,
            resolution=resolution,
            ratio=ratio,
            seed=seed,
            watermark=watermark,
            generate_audio=generate_audio,
            model=model,
        )
        if not wait:
            return self._submitted(tid, Path(out), tool="ref2video")
        return self._poll_video(tid, out, tool="ref2video", seconds=seconds, resolution=resolution)

    def video_task(self, *, op, task_id, output=None) -> dict:
        if op == "query":
            res = self._request("GET", f"/contents/generations/tasks/{task_id}")
            status = str(res.get("status", "")).lower()
            out = {"ok": True, "backend": self.name, "op": op, **res}
            # If the caller supplied an output path and the task is done,
            # download the artifact here so an async submit can be finalized.
            if output and status == "succeeded":
                gen = self._finalize_video(
                    res,
                    Path(output),
                    task_id=task_id,
                    tool="video_task",
                    seconds=res.get("duration", 0),
                    resolution=res.get("resolution", ""),
                )
                out["path"] = str(gen.path)
                out["extra_paths"] = gen.extra_paths
                # Tag the finalized clip like a generation result so downstream
                # consumers (e.g. the reward's film discovery + footage totals)
                # recognize an async-finalized one-shot the same as a wait=True
                # clip. `**res` already carries the task `id` + `usage`.
                out["kind"] = "video"
                out["meta"] = gen.meta
            return out
        if op == "cancel":
            self._request("DELETE", f"/contents/generations/tasks/{task_id}")
            return {"ok": True, "backend": self.name, "op": op, "task_id": task_id, "note": "cancel/delete requested"}
        raise MediaError(f"unknown video_task op {op!r} (expected 'query' or 'cancel')")


# --------------------------------------------------------------------------
# backend selection
# --------------------------------------------------------------------------


def get_backend(name: str | None = None) -> Backend:
    name = (name or os.getenv("MEDIA_BACKEND") or "mock").lower()
    if name == "mock":
        return MockBackend()
    if name == "volc":
        return VolcBackend()
    raise MediaError(f"unknown backend {name!r} (expected 'mock' or 'volc')")
