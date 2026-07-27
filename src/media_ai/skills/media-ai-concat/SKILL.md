---
name: media-ai-concat
description: >-
  Concatenate/join/stitch multiple video clips into one file with the media-ai CLI
  using local ffmpeg (no provider or credentials needed). Use when asked to combine,
  merge, join, stitch, or concatenate video clips / shots / segments into a single
  final video or film on the command line.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai concat --help"
---

# media-ai-concat — join clips into one film

> Optionally read `../media-ai-shared/SKILL.md` for the machine contract. This
> command is **local** (bundled ffmpeg) — no provider, no network, no credentials.

Joins ordered clips into a single output video. Typically the last step after
generating per-shot clips with the `media-ai-video` skill.

## Command & flags

```bash
media-ai concat --inputs '["s1.mp4","s2.mp4","s3.mp4"]' --output film.mp4
media-ai concat --inputs a.mp4 b.mp4 --output film.mp4 --width 1280 --height 720
```

| flag | default | notes |
|---|---|---|
| `--inputs PATH...` | — (required) | **ordered** clip paths; space-separated or a JSON array string |
| `--output PATH` | — (required) | final video path |
| `--width N` | ffmpeg default | normalize each clip to this width before joining |
| `--height N` | ffmpeg default | normalize each clip to this height |

Clips are normalized to `--width`×`--height` so mismatched inputs concatenate cleanly.

## Output

Reports `provider: "local"`, `operation: "video.concat"`, the output `path`/`bytes`,
a `clips` count, and an `artifacts[]` entry:

```json
{"ok": true, "modality": "video", "operation": "video.concat", "provider": "local",
 "path": "film.mp4", "bytes": 234567, "clips": 3,
 "artifacts": [{"path": "film.mp4", "kind": "video", "mime": "video/mp4", "bytes": 234567, "role": null}]}
```

## End-to-end example

```bash
export MEDIA_USAGE_LOG=/tmp/run/usage.jsonl
media-ai video generate --provider volc --prompt "shot 1" --output /tmp/run/s1.mp4 --duration 3 --resolution 480p
media-ai video generate --provider volc --prompt "shot 2" --output /tmp/run/s2.mp4 --duration 3 --resolution 480p
media-ai concat --inputs '["/tmp/run/s1.mp4","/tmp/run/s2.mp4"]' --output /tmp/run/final.mp4
```
