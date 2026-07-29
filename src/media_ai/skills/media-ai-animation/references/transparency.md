# Transparent animations

`--transparent` turns a background colour into alpha. Two things about it are worth
knowing before you reach for it, because both change what you should ask for.

## It is keying, not matting

The implementation measures each pixel's distance from one colour and makes the close
ones transparent. That works beautifully on a subject shot or rendered against a **flat,
evenly lit colour**, and not at all on arbitrary footage — a person in a kitchen has no
single background colour, so there is nothing to key, and the result is either untouched
or full of holes.

There is no error for this: keying footage with no flat background succeeds and produces
something wrong. If the source was not made against a solid colour, use the matting
workflow at the bottom of this page instead.

```bash
# rendered or shot against a flat colour: keying is the right tool
media-ai animation export --input subject.mp4 --output sticker.webp \
    --transparent --key-color 0x00FF00 --max-width 320
```

## The container decides how the edge looks

| format | alpha | the keyed edge |
|---|---|---|
| `webp` | 8-bit | soft, anti-aliased. **Use this** unless something forbids it |
| `apng` | 8-bit | the same, lossless and much larger |
| `gif` | **1 bit** | every pixel is fully opaque or fully invisible — a hard, jagged cut |

GIF's one-bit alpha is the single biggest surprise here. A subject with soft or
motion-blurred edges will look pixelated no matter how the key is tuned, because the
format cannot store a partially transparent pixel. `--option alpha_threshold=N` (0–255)
moves *where* the cut falls — lower keeps more of the semi-transparent fringe, higher
removes more of the subject with it — but it cannot make the cut gradual.

The result's `meta.notes` says this back to you whenever you key a GIF, so a caller
comparing outputs sees why.

## Tuning the key

| flag | default | raise it when | lower it when |
|---|---|---|---|
| `--similarity` | `0.30` | background colour survives in patches | the subject is being eaten |
| `--blend` | `0.05` | the edge is harsh (webp/apng only) | the edge looks smeared or ghosted |
| `--key-mode` | `chromakey` | — | `colorkey` for a *perfectly* uniform digital background: exact RGB matching, no chroma tolerance |
| `--despill` | `true` | — | `--despill false` if the subject genuinely contains the key colour |

`chromakey` compares chroma only, so it tolerates uneven lighting on the backdrop —
which is why it is the default. `colorkey` compares RGB and is the better choice for
synthetic sources where the background is exactly one value.

**Despill** removes the colour cast a key leaves around the edges — the difference
between a cut-out and one that looks glued on. It is on by default, and only worth
turning off when the subject is itself close to the key colour.

Green (`0x00FF00`) is the default because it is furthest from skin tones. Blue
(`0x0000FF`) is the usual alternative when the subject contains green.

Work at full resolution while tuning, then add `--max-width` for the final export:
scaling before the key means the key runs on already-blended edge pixels.

## Footage that was not shot on a flat colour

Key per-frame matting out of this tool and back into it:

1. Export the frames — `ffmpeg -i clip.mp4 -vf fps=12 frames/f_%04d.png`.
2. Matte each frame with something that does segmentation (a background-removal model,
   a rotoscoping tool, a manual mask). Write RGBA PNGs to a directory of their own.
3. Bring them back as a sequence — the alpha is already there, so **do not** pass
   `--transparent`:

```bash
media-ai animation export --frames matted/ --output out.webp --fps 12
```

That is exactly what `animation.from_frames` is a separate scene for: a set of stills is
a genuinely different input role from one clip, and it is the seam where work this tool
does not do comes back in.

Keep the frame names zero-padded (`f_0001.png`) — the sequence is read in lexical order,
and an unpadded one is refused rather than silently reordered.

## Checking the result

Alpha is easy to get wrong and easy to verify:

```bash
python -c "
from PIL import Image
im = Image.open('sticker.webp'); im.seek(0)
px = im.convert('RGBA')
print('frames', im.n_frames)
print('corner', px.getpixel((2, 2)))      # expect alpha 0
print('centre', px.getpixel((px.width // 2, px.height // 2)))
"
```

A corner alpha of `255` means nothing was keyed — usually the wrong `--key-color`, or a
source with no flat background. A `frames` of `1` means the animation collapsed
regardless of the alpha; see `export.md`.
