# `{{cli}} animation export` in detail

Everything here runs the bundled ffmpeg locally: free, offline, no credential, and
nothing leaves the machine.

## The two scenes

| scene | input | notes |
|---|---|---|
| `animation.from_video` | `--input clip.mp4` | one clip; its own frame rate is kept unless `--fps` says otherwise |
| `animation.from_frames` | `--frames …` | stills have no timebase, so they are read at `--fps` (default 12) |

Both write the same containers and take the same timing, geometry and transparency
flags. Passing `--input` and `--frames` together is `animation_input_conflict`, not a
precedence rule — there is no reading of it that is obviously right.

### Ways to name a frame sequence

```bash
--frames frames/                      # a directory: every image in it
--frames 'frames/*.png'               # a glob: quote it so the shell does not expand it
--frames f_001.png f_002.png f_003.png    # an explicit, complete, lexically ordered list
--frames '["f_001.png","f_002.png"]'  # the same as one JSON array
```

ffmpeg reads a sequence as a **glob or a pattern, never as a list of paths**, so an
explicit list is turned into `dir/*.ext`. That substitution is checked rather than
assumed:

| refusal | cause | fix |
|---|---|---|
| `animation_frames_extra` | the directory holds more matching files than you listed | pass the directory or a glob, or move the frames you want somewhere of their own |
| `animation_frames_unordered` | your order is not the lexical order the glob would use (`f_2` before `f_10`) | zero-pad the numbers |
| `animation_frames_scattered` | the frames are in different directories | put them in one |
| `animation_frames_mixed` | mixed extensions in one sequence | convert to one format first |

Each of these would otherwise be an animation built from frames nobody asked for, in an
order nobody asked for, reported as success.

## Trimming

`--start` / `--end` / `--duration` are seconds and may be fractional. `--end` and
`--duration` are two ways to say the same thing and cannot be combined.

```bash
--start 12 --duration 3      # three seconds beginning at 0:12
--start 12 --end 15          # identical
--end 3                      # the first three seconds
```

The trim bounds **how much source is read**, so `--bounce` and `--speed 0.5` extend the
result past it rather than being cut back to it.

## Timing

| flag | effect |
|---|---|
| `--fps N` | resamples to N frames per second. Fewer frames is the second-biggest size saving there is |
| `--speed X` | rewrites the timestamps: `2` plays twice as fast, `0.5` half. Frame **count** is unchanged unless `--fps` also resamples — what moves is the per-frame delay |
| `--reverse` | plays backwards |
| `--bounce` | forwards then backwards; doubles the frame count and makes a non-looping clip loop cleanly |
| `--loop N` | `0` forever (default), `1` once, `N` for N plays |

`--speed 0` is refused rather than read as "unchanged".

## Geometry

Pixels only — nothing here is choosing a shape, the source already has one, so there is
no `--aspect-ratio` or `--resolution` on this command.

| form | behaviour |
|---|---|
| `--size 480x270` | exact, aspect ratio not preserved |
| `--max-width 480` | fit inside; **never enlarges** a smaller source |
| `--max-height 270` | the same, bounded by height |
| both `--max-*` | fit inside the box, ratio preserved |

`--size` together with either `--max-*` is `animation_geometry_conflict`.

`--scale-filter` defaults to `lanczos`. Use `neighbor` for pixel art or anything where
crisp edges matter more than smoothness.

## Encoder knobs (`--option key=value`)

Gated on `constraints.options[]`; an unlisted key is refused as a typo rather than
silently ignored. Reach for these only after `--max-width`, `--fps` and `--format`.

### GIF

| option | default | meaning |
|---|---|---|
| `max_colors` | `256` (`255` with `--transparent`) | palette size, 2–256. Dropping to 64 or 32 saves a lot on flat graphics |
| `palette_stats_mode` | `diff` | `diff` optimises the palette for what *moves*; `full` for the whole frame; `single` builds one per frame |
| `dither` | `sierra2_4a` (`bayer` with `--transparent`) | `none` for flat graphics and screencasts — much smaller and sharper; error-diffusion dithers for photographic content |
| `bayer_scale` | `2` | `bayer` only: coarseness of the pattern |
| `diff_mode` | `rectangle` | restrict rewrites to the changed rectangle |
| `alpha_threshold` | `128` | with `--transparent`: where the 1-bit alpha cuts, 0–255 |

### WebP

| option | default | meaning |
|---|---|---|
| `lossless` | `false` | lossless costs bytes; useful for flat graphics and text |
| `quality` | `75` | 0–100, lossy only |
| `compression_level` | `4` | 0–6; higher is slower and smaller |

### Any container

| option | meaning |
|---|---|
| `pix_fmt` | override the container's pixel format. Rarely needed — the right one is already chosen from whether `--transparent` was asked for |

Some ffmpeg builds have no animated-WebP encoder (it comes from libwebp, and whether the
bundled binary carries it depends on the platform). `--format webp` still works there —
the frames are encoded from a lossless intermediate instead, with the same size, timing
and loop count — but `--option pix_fmt` is **refused** rather than ignored, because it is
a setting on the encoder that is missing. `meta.notes` says when that route was taken;
`{{cli}} doctor` says whether this machine takes it.

## Getting the size down

In the order worth trying:

1. `--max-width` — cost scales with the pixel count.
2. `--fps 10` or `12` — most screencasts read fine at 10.
3. `--format webp` — usually a large win over GIF at the same visual quality.
4. Trim to the part that matters (`--start` / `--duration`).
5. GIF only: `--option dither=none max_colors=64` on flat, non-photographic content.

## What the result says

```json
{"ok": true, "modality": "image", "provider": "local", "model": "ffmpeg",
 "artifacts": [{"path": "demo.webp", "kind": "image", "mime": "image/webp", "bytes": 50022}],
 "meta": {"format": "webp", "transparent": false, "loop": "forever",
          "frame_count": 24, "size": [640, 360], "source": "demo.mp4",
          "binding": "local/ffmpeg", "scene": "animation.from_video"}}
```

`frame_count` and `size` are read off the **finished file**, not restated from the
request. Check `frame_count`: a `1` means the animation collapsed to a still, which is a
valid image and therefore exit `0`.

## When this is the wrong tool

- **Keeping the audio** — an animated image has none. Ship the video.
- **Editing** — transitions, overlays, captions, cropping, concatenating. `{{cli}}
  video concat` joins clips; anything more is a real editor or ffmpeg directly.
- **A still frame** — this always writes an animation container. Extract a frame with
  ffmpeg.
- **Matting arbitrary footage** — keying needs a flat-coloured background; see
  `transparency.md`.
