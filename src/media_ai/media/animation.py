"""Animated-image encoding — GIF, animated WebP, APNG — on the bundled ffmpeg.

Three things about these containers are not portable, and hiding them is most of why
this is a command rather than a documented incantation:

* **"Loop N times" has three spellings, and one of them does not work.** APNG wants
  ``-plays``, WebP wants ``-loop``, and GIF wants ``-loop`` but *silently ignores any
  positive value* — ffmpeg 7's GIF muxer accepts ``-loop 3`` without complaint and
  writes 0, forever. Only ``0`` (forever) and ``-1`` (omit the block, play once) reach
  the file, so a finite count is written into the GIF afterwards by
  :func:`_set_gif_loop_count`.
* **GIF needs two passes to look like the source.** A single pass quantises to a fixed
  web palette and bands badly; generating a palette from the actual frames and then
  applying it is the whole difference.
* **GIF alpha is one bit.** ``palettegen`` has to reserve an entry for it and
  ``paletteuse`` has to be given a threshold, or the keyed region comes out **black**
  rather than transparent. WebP and APNG carry a real alpha channel and need neither.

Filter order is load-bearing: speed rewrites the timestamps that ``fps`` then resamples,
scaling precedes keying so the key runs on fewer pixels, and ``reverse``/ping-pong come
last because they buffer whole frames.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import ErrorCategory, MediaError
from .ffmpeg import LOCAL_ONLY_INPUT, ensure_parent, run_ffmpeg

#: ``scheme://`` — the shape of a thing ffmpeg would open over a network.
_URL_SCHEME = re.compile(r"[a-z][a-z0-9+.\-]*://", re.I)


@dataclass(frozen=True)
class Container:
    """One animated-image container and how ffmpeg is asked for it."""

    name: str
    extensions: tuple[str, ...]
    alpha: str
    """``full`` (8-bit channel) or ``binary`` (1-bit, GIF)."""
    needs_palette: bool = False
    encoder: tuple[str, ...] = ()
    loop_flag: tuple[str, str, str] = ("-loop", "0", "-1")
    """``(flag, forever, once)`` — see the module docstring."""
    pix_fmt: str | None = None
    alpha_pix_fmt: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


CONTAINERS: dict[str, Container] = {
    "gif": Container(
        name="gif", extensions=(".gif",), alpha="binary", needs_palette=True,
        loop_flag=("-loop", "0", "-1"),
        notes=("1-bit alpha: a pixel is opaque or invisible, so keyed edges look hard. "
               "webp or apng keep the soft edge.",),
    ),
    "webp": Container(
        name="webp", extensions=(".webp",), alpha="full",
        encoder=("-c:v", "libwebp_anim"), loop_flag=("-loop", "0", "1"),
        pix_fmt="yuv420p", alpha_pix_fmt="yuva420p",
        notes=("The only one of the three with both a real alpha channel and lossy "
               "compression, so usually the smallest.",),
    ),
    "apng": Container(
        name="apng", extensions=(".apng", ".png"), alpha="full",
        encoder=("-f", "apng"), loop_flag=("-plays", "0", "1"),
        pix_fmt="rgb24", alpha_pix_fmt="rgba",
        notes=("Lossless, so files are large. For when banding matters more than bytes, "
               "or the consumer cannot read animated WebP.",),
    ),
}

CONTAINER_NAMES = tuple(CONTAINERS)
SCALE_FILTERS = ("lanczos", "bicubic", "bilinear", "neighbor", "spline")
DITHERS = ("none", "bayer", "floyd_steinberg", "sierra2", "sierra2_4a", "sierra3", "heckbert")
PALETTE_STATS = ("full", "diff", "single")
KEY_MODES = ("chromakey", "colorkey")

#: How fast a *frame sequence* is read when the caller names no rate. A source video
#: carries its own timebase and gets no default — leaving `fps` unset there means "keep
#: whatever the footage has", which is the right answer and not a number we can invent.
DEFAULT_FRAME_RATE = 12

#: Ceiling on ``--loop``. GIF and WebP both store the play count in a ``uint16``; APNG
#: has room for more, but one shared limit beats a per-container surprise, and a request
#: for 65 536 plays means "forever" anyway (``--loop 0``).
MAX_PLAYS = 0xFFFF

_DEFAULT_DITHER = {"opaque": "sierra2_4a", "transparent": "bayer"}
_GIF_LOOP_MARKER = b"NETSCAPE2.0\x03\x01"


def _fail(message: str, *, code: str) -> MediaError:
    return MediaError(message, category=ErrorCategory.VALIDATION, code=code, provider="local")


def container_for(output: Path, explicit: str | None = None) -> Container:
    """Pick the container: an explicit format wins, else the output extension.

    Defaulting from the extension is what makes ``--output out.webp`` mean what it looks
    like. Naming something unsupported fails here rather than as an ffmpeg muxer error.
    """
    if explicit:
        found = CONTAINERS.get(explicit.lower())
        if found is None:
            raise _fail(
                f"unsupported animation format {explicit!r}; supported: {', '.join(CONTAINER_NAMES)}",
                code="animation_format_unsupported",
            )
        return found

    suffix = output.suffix.lower()
    for candidate in CONTAINERS.values():
        if suffix in candidate.extensions:
            return candidate
    known = ", ".join(e for c in CONTAINERS.values() for e in c.extensions)
    raise _fail(
        f"cannot tell the animation format from {output.name!r}; pass --format "
        f"({', '.join(CONTAINER_NAMES)}) or use a known extension ({known})",
        code="animation_format_unknown",
    )


def _scale(req) -> str | None:
    """One ``scale=`` clause, or None when no size was asked for."""
    flags = req.scale_filter or "lanczos"
    if flags not in SCALE_FILTERS:
        raise _fail(f"unknown scale filter {flags!r}; supported: {', '.join(SCALE_FILTERS)}",
                    code="animation_scale_filter_unknown")

    geo = req.geometry
    exact_w = geo.width if geo else None
    exact_h = geo.height if geo else None

    if req.max_width or req.max_height:
        if exact_w or exact_h:
            raise _fail("give either --size or --max-width/--max-height, not both",
                        code="animation_geometry_conflict")
        if req.max_width and req.max_height:
            return (f"scale={int(req.max_width)}:{int(req.max_height)}"
                    f":force_original_aspect_ratio=decrease:flags={flags}")
        # Fit inside, never enlarge. The comma inside min() is escaped because ffmpeg
        # splits the filter chain on commas before it parses any argument.
        if req.max_width:
            return rf"scale=min(iw\,{int(req.max_width)}):-1:flags={flags}"
        return rf"scale=-1:min(ih\,{int(req.max_height)}):flags={flags}"

    if exact_w or exact_h:
        return f"scale={int(exact_w or -1)}:{int(exact_h or -1)}:flags={flags}"
    return None


def _key(req) -> str:
    """The fragment that turns a solid-colour background into alpha."""
    mode = req.key_mode or "chromakey"
    if mode not in KEY_MODES:
        raise _fail(f"unknown key mode {mode!r}; supported: {', '.join(KEY_MODES)}",
                    code="animation_key_mode_unknown")

    colour = req.key_color or "0x00FF00"
    similarity = 0.30 if req.similarity is None else float(req.similarity)
    blend = 0.05 if req.blend is None else float(req.blend)
    if not 0 < similarity <= 1:
        raise _fail("key similarity must be greater than 0 and at most 1",
                    code="animation_key_similarity_invalid")
    if not 0 <= blend <= 1:
        raise _fail("key blend must be between 0 and 1", code="animation_key_blend_invalid")

    parts = [f"{mode}=color={colour}:similarity={similarity}:blend={blend}"]
    if mode == "chromakey" and req.despill:
        # Removes the colour cast the key leaves on edges — the difference between a
        # cut-out and one that looks glued on.
        parts.append("despill=type=green" if "00ff00" in colour.lower().replace("#", "").replace("0x", "")
                     or colour.lower() == "green" else "despill=type=blue")
    # Everything downstream needs a real alpha plane to read; without this the first
    # filter that assumes no alpha flattens the key away.
    parts.append("format=rgba")
    return ",".join(parts)


def _filters(req, *, transparent: bool) -> str:
    chain: list[str] = []

    speed = 1.0 if req.speed is None else float(req.speed)
    if speed <= 0:
        raise _fail("speed must be greater than 0", code="animation_speed_invalid")
    if speed != 1.0:
        chain.append(f"setpts=PTS/{speed}")

    if req.fps is not None:
        if float(req.fps) <= 0:
            raise _fail("fps must be greater than 0", code="animation_fps_invalid")
        chain.append(f"fps={req.fps}")

    scale = _scale(req)
    if scale:
        chain.append(scale)

    if transparent:
        chain.append(_key(req))

    if req.reverse:
        chain.append("reverse")

    if req.bounce:
        inner = ",".join(chain) if chain else "null"
        return f"{inner},split[fwd][tmp];[tmp]reverse[bwd];[fwd][bwd]concat=n=2:v=1:a=0"

    return ",".join(chain) if chain else "null"


def _palette(req, *, transparent: bool) -> str:
    max_colors = int(req.options.get("max_colors", 256))
    if not 2 <= max_colors <= 256:
        raise _fail("max_colors must be between 2 and 256", code="animation_max_colors_invalid")
    if transparent:
        # One entry has to hold "transparent"; keeping 256 would make palettegen drop a
        # colour of its own choosing instead.
        max_colors = min(max_colors, 255)

    stats = str(req.options.get("palette_stats_mode", "diff"))
    if stats not in PALETTE_STATS:
        raise _fail(f"unknown palette_stats_mode {stats!r}; supported: {', '.join(PALETTE_STATS)}",
                    code="animation_palette_stats_unknown")

    dither = str(req.options.get("dither", _DEFAULT_DITHER["transparent" if transparent else "opaque"]))
    if dither not in DITHERS:
        raise _fail(f"unknown dither {dither!r}; supported: {', '.join(DITHERS)}",
                    code="animation_dither_unknown")

    gen = f"palettegen=max_colors={max_colors}:stats_mode={stats}"
    use = f"paletteuse=dither={dither}"
    if dither == "bayer":
        use += f":bayer_scale={int(req.options.get('bayer_scale', 2))}"
    if transparent:
        gen += ":reserve_transparent=1"
        use += f":alpha_threshold={int(req.options.get('alpha_threshold', 128))}"
    else:
        use += f":diff_mode={req.options.get('diff_mode', 'rectangle')}"
    return f"split[pg_a][pg_b];[pg_a]{gen}[pg_p];[pg_b][pg_p]{use}"


def _loop(container: Container, loop: int) -> list[str]:
    if loop < 0 or loop > MAX_PLAYS:
        raise _fail(f"--loop must be between 0 (play forever) and {MAX_PLAYS}",
                    code="animation_loop_invalid")
    flag, forever, once = container.loop_flag
    if not loop:
        return [flag, forever]
    if loop == 1:
        return [flag, once]
    if container.name == "gif":
        # Ask for the loop block and nothing else; the count is written in afterwards,
        # because ffmpeg would replace whatever is asked for here with "forever".
        return [flag, forever]
    return [flag, str(loop)]


def _set_gif_loop_count(out: Path, plays: int) -> None:
    """Write a finite play count into a GIF's NETSCAPE block, which ffmpeg will not.

    The count is a little-endian ``uint16`` directly after the block's ``03 01``
    sub-block header. Both Pillow and the browsers read that number as *plays*, so it
    goes in verbatim — the earlier ``loop - 1`` here was a repeats-vs-plays correction
    for an ffmpeg option that turned out never to reach the file at all.

    ``--loop 1`` does not come through here: it is encoded by *omitting* the block
    (``-loop -1``), which is the one spelling of "play once" no decoder disagrees about.

    Refuses if the block is missing rather than leaving the file as it is. The whole
    point is that a wrong loop count came back as success.
    """
    raw = out.read_bytes()
    at = raw.find(_GIF_LOOP_MARKER)
    if at < 0:
        raise _fail(
            f"could not set the loop count on {out.name}: the GIF has no loop block to write it "
            f"into; re-run with --loop 0 to play forever, or use --format webp",
            code="animation_loop_not_expressible",
        )
    at += len(_GIF_LOOP_MARKER)
    out.write_bytes(raw[:at] + struct.pack("<H", plays) + raw[at + 2:])


def build_args(req, container: Container, *, transparent: bool) -> list[str]:
    """ffmpeg arguments, without the executable.

    Writes nothing; the frame-sequence path reads the source directory to check the
    sequence it is about to animate (see :func:`frames_input`).
    """
    if transparent and container.alpha == "none":  # pragma: no cover - no such container today
        raise _fail(f"{container.name} cannot carry transparency", code="animation_no_alpha")

    args: list[str] = []

    # Both trim flags go *before* -i, so they bound how much source is read rather than
    # how long the output may be. On the output they would truncate after the filters
    # ran: `--bounce` would lose its reversed half and `--speed 0.5` would be cut back to
    # the source length — the request honoured and then undone.
    if req.end_seconds is not None and req.duration_seconds is not None:
        raise _fail("give either --end or --duration, not both", code="animation_trim_conflict")
    if req.start_seconds is not None:
        args += ["-ss", str(req.start_seconds)]
    if req.duration_seconds is not None:
        args += ["-t", str(req.duration_seconds)]
    elif req.end_seconds is not None:
        start = float(req.start_seconds or 0)
        if float(req.end_seconds) <= start:
            raise _fail("--end must be greater than --start", code="animation_trim_invalid")
        args += ["-t", str(float(req.end_seconds) - start)]

    if req.frames:
        # A frame sequence has no timebase of its own; -framerate sets how fast the
        # stills are read, and fps in the chain resamples from there.
        spec, is_glob = frames_input(req)
        if is_glob:
            args = ["-pattern_type", "glob", *args]
        args += ["-framerate", str(req.fps or DEFAULT_FRAME_RATE), *LOCAL_ONLY_INPUT, "-i", spec]
    else:
        args += [*LOCAL_ONLY_INPUT, "-i", str(req.source.raw)]

    filters = _filters(req, transparent=transparent)
    if container.needs_palette:
        filters = f"{filters},{_palette(req, transparent=transparent)}"
    args += ["-vf", filters, *container.encoder]

    if container.name == "webp":
        lossless = 1 if req.options.get("lossless") else 0
        args += ["-lossless", str(lossless)]
        if not lossless:
            args += ["-q:v", str(int(req.options.get("quality", 75)))]
        args += ["-compression_level", str(int(req.options.get("compression_level", 4)))]

    pix_fmt = req.options.get("pix_fmt") or (container.alpha_pix_fmt if transparent else container.pix_fmt)
    if pix_fmt:
        args += ["-pix_fmt", pix_fmt]

    args += _loop(container, int(req.loop or 0))
    args += ["-an"]  # an animated image has no audio track
    args.append(str(req.output))
    return args


def frames_input(req) -> tuple[str, bool]:
    """The ffmpeg input for a frame sequence: ``(spec, needs_pattern_type_glob)``.

    ffmpeg reads a sequence as a **glob or a printf pattern**, never as a list of paths,
    so an explicit list has to become ``dir/*.ext``. That substitution is where the
    silent failure lives: the glob is whatever is in the directory *now*, which need not
    be what the caller listed, and it is ordered lexically, which need not be the order
    they passed. Either way the animation would come out of frames nobody asked for and
    still report success — so both are checked here and refused with the fix named.

    A caller who really means "everything in here" says so directly, by passing a glob
    or a directory; then there is nothing to disagree with.
    """
    raws = [str(f.raw) for f in req.frames]
    first = Path(raws[0])

    if len(raws) == 1:
        if any(ch in first.name for ch in "*?["):
            return str(first), True            # an explicit glob: the caller's own set
        if first.is_dir():
            return str(first / f"*{_sole_suffix(sorted(p for p in first.iterdir() if p.is_file()))}"), True
        return str(first), False               # one still — a one-frame animation

    parents = {Path(r).parent for r in raws}
    if len(parents) != 1:
        raise _fail(
            "a frame sequence must live in one directory (ffmpeg reads it as a glob, "
            "not as a list of paths)",
            code="animation_frames_scattered",
        )
    suffix = _sole_suffix([Path(r) for r in raws])
    if raws != sorted(raws):
        raise _fail(
            "frames must be listed in lexical order, because ffmpeg reads the sequence "
            "in that order and would ignore the order given here; zero-pad the numbers "
            "(frame_010.png, not frame10.png) so the two agree",
            code="animation_frames_unordered",
        )

    directory = parents.pop()
    extra = sorted(p.name for p in directory.glob(f"*{suffix}") if str(p) not in set(raws))
    if extra:
        raise _fail(
            f"{directory} holds {len(extra)} more {suffix} file(s) than the {len(raws)} listed "
            f"({', '.join(extra[:4])}{', …' if len(extra) > 4 else ''}); the sequence is read as "
            f"a glob over the directory, so those would be animated too — move the frames you "
            f"want into their own directory, or pass that directory (or a glob) as --frames",
            code="animation_frames_extra",
        )
    return str(directory / f"*{suffix}"), True


def _sole_suffix(paths: list[Path]) -> str:
    suffixes = {p.suffix.lower() for p in paths if p.suffix}
    if not suffixes:
        raise _fail("frame files need an extension so the sequence can be globbed",
                    code="animation_frames_unextended")
    if len(suffixes) != 1:
        raise _fail(
            f"a frame sequence must share one file extension, found {', '.join(sorted(suffixes))}",
            code="animation_frames_mixed",
        )
    return suffixes.pop()


def _names_no_local_file(ref) -> bool:
    """Whether a ref names something other than a path on this filesystem.

    ``MediaRef.is_remote`` knows the schemes *providers* accept; ffmpeg opens many more
    (``rtmp://``, ``tcp://``, ``file://``), so this matches the shape rather than a list.
    Anything it still misses is refused by the "must exist" check below, which no
    protocol string passes — this only decides which refusal explains it.
    """
    return ref.is_remote or bool(_URL_SCHEME.match(ref.raw))


def _check_inputs(req) -> None:
    """Name a bad input before ffmpeg gets a chance to.

    Two refusals, and the first is a boundary rather than a courtesy:

    * **Not a local file.** This is the offline binding — no credential, no cost, no
      network — and ffmpeg does not share that assumption: handed ``http://…`` it
      *fetches* it, so a command documented as local made a request to whatever host the
      URL named. The check used to require ``ref.is_local`` to even consider a ref, which
      meant a URL skipped validation entirely and reached the command line verbatim. It
      is refused here rather than in ``core/validate.py`` because ``--on-unsupported
      ignore`` must not be able to switch a boundary off.
    * **A missing local file.** ffmpeg's own answer is ``Error opening input: No such
      file or directory``, which does not say *which* path — and a frame sequence hands
      it dozens.
    """
    refs = [ref for ref in [req.source, *req.frames] if ref is not None]
    remote = [ref.raw for ref in refs if _names_no_local_file(ref)]
    if remote:
        raise MediaError(
            f"input is not a local file: {', '.join(remote[:5])}{', …' if len(remote) > 5 else ''}",
            category=ErrorCategory.UNSUPPORTED, code="animation_input_not_local", provider="local",
            hint="download it first and pass the local path; this binding runs the bundled "
                 "ffmpeg and reaches no network",
        )
    missing = [
        ref.raw for ref in refs
        if not any(ch in Path(ref.raw).name for ch in "*?[")  # a glob is resolved later
        and not Path(ref.raw).exists()
    ]
    if missing:
        raise MediaError(
            f"input not found: {', '.join(missing[:5])}{', …' if len(missing) > 5 else ''}",
            category=ErrorCategory.IO, code="animation_input_not_found", provider="local",
        )


def render(req, container: Container, *, transparent: bool) -> Path:
    """Encode and return the output path."""
    out = Path(req.output)
    _check_inputs(req)
    ensure_parent(out)
    plays = int(req.loop or 0)
    run_ffmpeg(build_args(req, container, transparent=transparent))
    if not out.is_file() or out.stat().st_size == 0:
        raise MediaError(
            f"ffmpeg reported success but wrote nothing to {out}",
            category=ErrorCategory.IO, code="animation_empty_output", provider="local",
        )
    if container.name == "gif" and plays > 1:
        _set_gif_loop_count(out, plays)
    return out


def probe(out: Path) -> dict:
    """What actually came out: frame count and pixel size.

    Worth a read of the finished file rather than a restatement of the request, because
    the one failure these filter graphs have is producing a *single frame* — a trimmed
    range that fell between two frames, an ``fps`` low enough to keep one, a palette pass
    that swallowed the animation. That output is a valid image, so ffmpeg exits 0 and
    every field derived from the request still looks right. Best-effort: an unreadable
    file returns nothing rather than failing a render that succeeded.
    """
    try:
        from PIL import Image

        with Image.open(out) as im:
            return {"frame_count": getattr(im, "n_frames", 1), "size": [im.width, im.height]}
    except Exception:  # noqa: BLE001 - a probe may not fail the call it is describing
        return {}


def describe(container: Container) -> list[str]:
    """Caveats worth returning with the result, not just documenting."""
    return list(container.notes)


__all__ = [
    "CONTAINERS", "CONTAINER_NAMES", "DEFAULT_FRAME_RATE", "DITHERS", "KEY_MODES",
    "MAX_PLAYS", "PALETTE_STATS", "SCALE_FILTERS", "Container", "build_args",
    "container_for", "describe", "frames_input", "probe", "render",
]
