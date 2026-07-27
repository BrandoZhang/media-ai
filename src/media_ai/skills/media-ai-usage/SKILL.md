---
name: media-ai-usage
description: >-
  Report accumulated token/artifact/character cost from the media-ai usage ledger. Use
  when asked how much a generation run cost, to summarize token usage or spend across
  media-ai image/video/audio generations, or to account for a batch/pipeline of commands.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai usage --help"
  install:
    tier: core
    summary: >-
      Read back what a run cost — tokens, characters, artifacts — from the local
      usage ledger, per command or across a whole pipeline.
---

# media-ai-usage — account for generation cost

> Read `../media-ai-shared/SKILL.md` for the machine contract. This reads a local
> ledger — no provider or credentials needed.

Every generation appends one line to a JSONL **usage ledger**. `media-ai usage`
aggregates it into token/artifact totals for the run.

## The ledger

- Default path: `$MEDIA_USAGE_LOG`, else `./media_usage.jsonl`.
- **Isolate concurrent runs:** point `MEDIA_USAGE_LOG` (and each `--output`) at a
  per-task directory so parallel pipelines don't collide.

```bash
export MEDIA_USAGE_LOG=/tmp/run/usage.jsonl
media-ai image generate --provider gemini --prompt "..." --output /tmp/run/a.png
media-ai video generate --provider volc   --prompt "..." --output /tmp/run/b.mp4 --resolution 480p
media-ai usage                       # summarize $MEDIA_USAGE_LOG
media-ai usage --log /tmp/run/usage.jsonl --pretty   # or an explicit ledger path
```

## Command & flags

| flag | notes |
|---|---|
| `--log PATH` | ledger path (default `$MEDIA_USAGE_LOG` → `./media_usage.jsonl`) |
| `--pretty` | indent the JSON |
| `--metadata-out PATH` | also write the JSON to a file |

## Output

```json
{"ok": true, "schema_version": 1, "ledger": "/tmp/run/usage.jsonl",
 "totals": {"calls": N, "images_generated": N, "video_seconds": N,
            "speech_characters": N, "total_tokens": N,
            "by_tool": {...}, "by_provider": {...}}}
```

`totals` aggregates the per-generation usage recorded across the run: `images_generated`
(image), `video_seconds` (video), `speech_characters` (audio — speech/music/sound),
and `total_tokens`, plus `by_tool` / `by_provider` breakdowns.
