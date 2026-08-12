---
name: {{skill}}animation
description: >-
  Turn a video clip or a sequence of stills into an animated image — GIF, animated
  WebP, or APNG — with trimming, frame rate, speed, ping-pong looping, resizing and
  optional background removal to a transparent alpha channel. Runs on the bundled
  ffmpeg: free, offline, no credential. Use when asked to make a GIF, convert a video
  to a GIF / WebP / APNG / animated image / animated sticker, produce a looping or
  transparent animation, or embed a clip somewhere a <video> tag will not work.
version: 1.0.0
metadata:
  requires:
    bins: ["{{cli}}"]
  cliHelp: "{{cli}} capabilities --scene animation.from_video"
  install:
    tier: optional
    summary: >-
      Export an animated image from a clip or a set of stills — GIF, animated WebP or
      APNG — with trimming, speed, ping-pong looping, resizing and background keying.
      Runs locally on the bundled ffmpeg, so it needs no key and costs nothing.
---

# {{skill}}animation — clip or stills → animated image

> **Read `../{{skill}}shared/SKILL.md` first** for the machine contract, how a binding
> is named, and credentials. This group needs **no credential**: it runs the bundled
> ffmpeg on this machine, offline and free. It is **synchronous** — no `job` polling.

The last mile of a render: something that autoplays in a README, a chat message, a
changelog or a docs page, where a `<video>` tag is unwelcome or silently blocked.

One command, **`{{cli}} animation export`**, serving two scenes. As everywhere, **what
you pass decides the scene** — no flag selects one:

| you pass | scene | meaning |
|---|---|---|
| `--input clip.mp4` | `animation.from_video` | one source clip |
| `--frames frames/` | `animation.from_frames` | a sequence of stills |

Passing both is an error, not a precedence rule.

> The output modality is **`image`**, not `video` — an animated GIF/WebP/APNG is served
> as an image (`image/gif`, an `<img>` tag) whichever way it was made. That is why this
> is its own group rather than a scene under `video`.

## Pick the container

Ask for it with `--format`, or just let the `--output` extension say it
(`out.webp` → WebP). All three are always available.

| format | alpha | when |
|---|---|---|
| `webp` | full 8-bit | **the default choice.** Real alpha *and* lossy compression, so usually much the smallest. Not readable by some very old tooling. |
| `gif` | 1-bit | maximum compatibility — anywhere that has ever shown an animation. Biggest files, 256 colours per frame, and a keyed edge is hard-cut (see below). |
| `apng` | full 8-bit | lossless, so large. For when banding matters more than bytes, or the consumer cannot read animated WebP. |

## Discover first

```bash
{{cli}} capabilities --scene animation.from_video --configured
{{cli}} capabilities --binding local/ffmpeg --pretty
```

Read `constraints.output.formats` and `constraints.options[]` — the encoder knobs below
are gated on that list.

## Flags

| flag | meaning |
|---|---|
| `--input PATH` | source video (`animation.from_video`). A **local path** — this runs offline, so a URL is refused (exit 3, `animation_input_not_local`); download it first |
| `--frames PATH...` | stills instead: a **directory**, a **glob**, or an ordered list (JSON array ok). Local paths, same as `--input` |
| `--output PATH` (required) | the animated image |
| `--format {gif,webp,apng}` | container; inferred from the extension when omitted |
| `--start S` / `--end S` | take this span of the source, in seconds |
| `--duration S` | how many seconds from `--start` (use instead of `--end`, not with it) |
| `--fps N` | frame rate of the animation. **The biggest size lever after `--max-width`** |
| `--speed X` | playback multiplier; `2` = twice as fast, `0.5` = half |
| `--reverse [true\|false]` | play backwards |
| `--bounce [true\|false]` | forwards then backwards, so a short clip loops without a jump |
| `--loop N` | `0` = forever (**default**), `1` = play once, `N` = N plays |
| `--size WxH` | exact pixel size |
| `--max-width N` / `--max-height N` | fit inside, keeping the aspect ratio; **never enlarges** |
| `--scale-filter {lanczos,bicubic,bilinear,neighbor,spline}` | rescaling algorithm (default `lanczos`; `neighbor` for pixel art) |
| `--transparent [true\|false]` | key the background colour out to alpha — see below |
| `--key-color C` | the colour to key, e.g. `0x00FF00` or `green` (default green) |
| `--key-mode {chromakey,colorkey}` | `chromakey` keys on chroma alone and tolerates uneven lighting; `colorkey` on RGB |
| `--similarity 0-1` / `--blend 0-1` | how much counts as background (default `0.30`) and how soft the edge is (default `0.05`) |
| `--despill [true\|false]` | remove the colour cast the key leaves on edges (default `true`) |
| `--option key=value` | encoder knobs, gated on `constraints.options[]` — see `references/export.md` |

Boolean flags take both spellings: `--bounce` and `--bounce true` are the same thing,
and `--despill false` turns a default off.

## Quick starts

```bash
# a clip → a small looping WebP for a README
{{cli}} animation export --input demo.mp4 --output demo.webp --max-width 640 --fps 12

# a 3-second GIF from the middle of a longer clip, played twice
{{cli}} animation export --input talk.mp4 --output clip.gif \
    --start 12 --duration 3 --max-width 480 --fps 10 --loop 2

# a seamless loop out of a clip that does not loop
{{cli}} animation export --input wave.mp4 --output wave.webp --bounce --max-width 400

# a green-screen subject → a transparent animated sticker
{{cli}} animation export --input subject.mp4 --output sticker.webp \
    --transparent --key-color 0x00FF00 --max-width 320

# rendered stills → an animation (the frames are read in lexical order)
{{cli}} animation export --frames frames/ --output turntable.webp --fps 24
```

## Check what came out

The result's `meta` reports the finished file, not the request:

```json
{"meta": {"format": "webp", "frame_count": 24, "size": [640, 360], "loop": "forever"}}
```

**`frame_count` is worth reading.** A single frame means the animation collapsed — a
span that fell between two frames, an `--fps` low enough to keep one, or a source that
does not actually move. That output is a valid image, so the exit code is `0` either
way; the count is what tells them apart.

## Gotchas

- **No audio.** An animated image has no audio track. If the clip's sound matters, ship
  the video.
- **Size grows with `width × fps × duration`.** Reach for `--max-width` and `--fps`
  before the encoder knobs; `--format webp` over `gif` is usually a large win on its own.
- **GIF alpha is one bit** — a pixel is opaque or invisible. A keyed subject with soft or
  motion-blurred edges comes out jagged. Use `webp` or `apng` when the cut-out matters.
- **Keying is a chroma distance, not matting.** It needs a subject shot against a flat
  colour. Arbitrary footage has to be matted per frame by something else and fed back in
  through `--frames` — see `references/transparency.md`.
- **A frame list must be complete and in lexical order.** ffmpeg reads a sequence as a
  glob over its directory, never as a list of paths, so a partial or out-of-order list is
  refused rather than quietly animating the wrong frames. Pass the directory or a glob
  when you mean "all of these", and zero-pad names (`f_010.png`, not `f_10.png`).
- **`--loop 1` means play once**, not "loop one extra time".
- Cost is zero and nothing leaves the machine, but the call is still recorded in the
  ledger under `local/ffmpeg` — see `{{skill}}usage`.

## References

- `references/export.md` — every flag in detail, the encoder knobs, and size tuning.
- `references/transparency.md` — keying, its precondition, and the per-frame matting
  workflow for footage that was not shot on a flat colour.
