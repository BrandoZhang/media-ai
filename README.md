# media-ai

Standalone multimodal generation CLIs — **no uni-agent dependency**. Drop it
into any agent sandbox (uni-agent, Claude Code, Codex, OpenCode, OpenClaw, a
plain shell, …) and the agent can generate images and video by running
ordinary commands.

## Install

```bash
pip install -e .                      # from a clone
# or, once published:  pip install media-ai
```

Pulls in Pillow (mock image rendering) and a bundled ffmpeg via
`imageio-ffmpeg` (mock video). The real backend uses only the stdlib.

## Commands

| Command | What it does |
|---|---|
| `text2image`   | text → image (or a group via `--max_images`) |
| `image2image`  | reference image(s) + text → image |
| `text2video`   | text → clip |
| `image2video`  | first (+ optional last) frame + text → clip |
| `ref2video`    | multimodal reference (images/videos/audio) + text → clip |
| `concat_video` | join clips → final film (local ffmpeg) |
| `video_task`   | query / cancel an async video task |
| `media_usage`  | report accumulated token cost from the ledger |

Each is installed as its own console script, and all are reachable through a
single umbrella command:

```bash
media-ai text2image --prompt "a red bicycle" --output bike.png
python -m media_ai text2image --prompt "a red bicycle" --output bike.png   # equivalent
```

Every generation prints a one-line JSON result with the artifact `path` and a
`usage` block (token cost), and appends the same to a usage ledger
(`$MEDIA_USAGE_LOG`, default `./media_usage.jsonl`). Point `MEDIA_USAGE_LOG`
(and each `--output`) at a **per-task directory** when several tasks run
concurrently on a shared filesystem, so their artifacts and ledgers don't
collide — an agent harness can derive one automatically, but the CLI itself is agnostic: give it whatever
paths you want.

## Backends

- **`mock`** (default, offline): Pillow placeholder images + ffmpeg clips.
  Deterministic given `(prompt, seed)`; costs nothing. Token counts are
  synthesized with the same formulas the real API documents.
- **`volc`** (opt-in): Volcengine **Ark** API (Bearer API key). Set
  `MEDIA_BACKEND=volc` and `ARK_API_KEY`. The Model ID is chosen per call with
  `--model` (optional), else `$ARK_IMAGE_MODEL` / `$ARK_VIDEO_MODEL`, else a
  built-in default:

  ```bash
  export MEDIA_BACKEND=volc ARK_API_KEY=...
  # optional global defaults (a per-call --model overrides them):
  export ARK_IMAGE_MODEL=doubao-seedream-4-5-251128   # e.g. also 5-0-260128
  export ARK_VIDEO_MODEL=doubao-seedance-2-0-260128
  # or per call:  text2image --model doubao-seedream-5-0-260128 ...
  ```

  A model must be **enabled for your account**; the full Model ID list is at
  <https://www.volcengine.com/docs/82379/1330310>.

  Covers text/reference/group images and text/first-frame/first+last-frame/
  multimodal-reference video (async create → poll → cancel).

## Example

```bash
export MEDIA_USAGE_LOG=/tmp/run/usage.jsonl
media-ai text2image  --prompt "silver astronaut on a red dune" --output /tmp/run/ref.png --seed 7
media-ai image2video --first_frame /tmp/run/ref.png --prompt "he turns to camera" --output /tmp/run/s1.mp4 --seconds 3 --resolution 480p
media-ai text2video  --prompt "twin suns setting" --output /tmp/run/s2.mp4 --seconds 3 --resolution 480p
media-ai concat_video --inputs '["/tmp/run/s1.mp4","/tmp/run/s2.mp4"]' --output /tmp/run/final.mp4
media-ai media_usage   # -> total token cost
```
