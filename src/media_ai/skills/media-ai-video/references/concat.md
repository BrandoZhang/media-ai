# Joining clips into one film

`media-ai concat` runs the bundled ffmpeg on this machine: **free, offline, no
credential, no cost**. It is the last step of a video run, which is why it lives with
the video skill rather than in one of its own.

```bash
media-ai concat --inputs '["s1.mp4","s2.mp4","s3.mp4"]' --output film.mp4
media-ai concat --inputs a.mp4 b.mp4 --output film.mp4 --width 1280 --height 720
```

| flag | default | notes |
|---|---|---|
| `--inputs PATH...` | required | **ordered**; space-separated, or one JSON array string |
| `--output PATH` | required | the joined file |
| `--width N` / `--height N` | ffmpeg default | every clip is normalized to this before joining |

Order is the order you pass. Clips are re-encoded to a common size first, so mixing
resolutions works but costs a pass.

The result carries the same shape as any other: `artifacts[]`, and a `meta` recording
`binding: "local/ffmpeg"` and `scene: "video.concat"`. It is an ordinary binding —
listed by `media-ai bindings list`, always available because there is nothing to
configure.

## When it is the wrong tool

- **Trimming, cropping, speed, overlays** — not implemented. Call ffmpeg directly.
- **Joining audio** — this joins video. Use the audio your clips already carry, or
  mux separately.
- **Transitions** — this is a hard cut between clips. Anything else needs a real
  editor.
