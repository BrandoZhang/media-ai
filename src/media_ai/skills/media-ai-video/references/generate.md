# `media-ai video generate` — full reference

Text / frames / references → video. Async on every real provider; `--wait true`
(default) blocks and downloads, `--wait false` returns a `JobHandle` to poll with the
`media-ai-job` skill.

## All flags

| flag | default | notes |
|---|---|---|
| `--output` | — (required) | output path |
| `--prompt` | `""` | text prompt |
| `--first-frame PATH` | none | starting still (`role=first_frame`); needs `supports_first_frame` |
| `--last-frame PATH` | none | ending still (`role=last_frame`); needs `constraints.supports.last_frame` |
| `--reference-image PATH...` | `[]` | subject/style refs; JSON array ok |
| `--reference-video PATH...` | `[]` | video refs (Veo extension refs must be a URI, not a local file) |
| `--reference-audio PATH...` | `[]` | audio refs |
| `--duration N` / `--seconds N` | model default (often 5) | must be in the model's `durations` set |
| `--size WxH` | none | pixel geometry |
| `--aspect-ratio R` / `--ratio R` | none | e.g. `16:9`, `9:16` |
| `--resolution TIER` | none | `480p\|720p\|1080p` (+ `4k` on some Veo tiers) |
| `--seed N` | none | where `constraints.supports.seed` is true |
| `--audio {true,false}` | none | request generated audio; where `supports_audio` |
| `--watermark {true,false}` | none | where `supports_watermark_control` (default behavior = no watermark) |
| `--negative-prompt TEXT` | none | where `constraints.supports.negative_prompt` is true |
| `--return-last-frame {true,false}` | `false` | also emit the final frame as an artifact |
| `--wait {true,false}` | `true` | block+poll vs async submit |
| `--option key=value ...` | `[]` | provider-specific, capability-gated |
| global | | `--binding`, `--provider`, `--model`, `--on-unsupported`, `--pretty`, `--metadata-out`, `--log-level`, `--verbose` |

## The input modes

### 1. Text → video

```bash
media-ai video generate --binding <provider>/<model> \
    --prompt "twin suns setting over a desert, slow push in" \
    --resolution 720p --aspect-ratio 16:9 --duration 5 --output suns.mp4
```

### 2. Image → video (first / last frame)

```bash
# animate a single still
media-ai video generate --binding <provider>/<model> \
    --first-frame ref.png --prompt "he turns to camera" \
    --resolution 1080p --duration 6 --audio true --output turn.mp4

# interpolate between two stills (needs constraints.supports.last_frame)
media-ai video generate --binding <provider>/<model> \
    --first-frame a.png --last-frame b.png --prompt "smooth morph" \
    --resolution 720p --duration 4 --output morph.mp4
```

### 3. References → video

```bash
media-ai video generate --binding <provider>/<model> \
    --prompt "keep this character, new scene: a night market" \
    --reference-image '["char1.png","char2.png"]' \
    --resolution 720p --duration 5 --output market.mp4 --option camera_fixed=true
```

## Async submit + poll (the deterministic pattern)

```bash
# 1. submit
media-ai video generate --binding <provider>/<model> \
    --prompt "..." --output /tmp/run/clip.mp4 --wait false --metadata-out /tmp/run/job.json
# 2. read job.json -> run its `poll` string until status == succeeded (downloads the file)
media-ai job query --binding <provider>/<model> --id <job-id> --output /tmp/run/clip.mp4
```

See the `media-ai-job` skill for the full lifecycle and cancel semantics. A duration
or resolution outside the model's set, or an unsupported frame/reference/option,
fails with **exit 3** naming the field (or `--on-unsupported warn` for best-effort).
