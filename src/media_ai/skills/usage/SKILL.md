---
name: {{skill}}usage
description: >-
  Report accumulated token/artifact/character cost from the {{cli}} usage ledger. Use
  when asked how much a generation run cost, to summarize token usage or spend across
  {{cli}} image/video/audio generations, or to account for a batch/pipeline of commands.
version: 2.0.0
metadata:
  requires:
    bins: ["{{cli}}"]
  cliHelp: "{{cli}} usage --help"
  install:
    tier: core
    summary: >-
      Read back what a run cost — tokens, characters, artifacts — from the local
      usage ledger, per command or across a whole pipeline.
---

# {{skill}}usage — account for generation cost

> Read `../{{skill}}shared/SKILL.md` for the machine contract. This reads a local
> ledger — no provider or credentials needed.

Every generation appends one line to a JSONL **usage ledger**. `{{cli}} usage`
aggregates it into token/artifact totals for the run.

## The ledger

- Default path: `$MEDIA_AI_USAGE_LOG`, else `./media_usage.jsonl`.
- **Isolate concurrent runs:** point `MEDIA_AI_USAGE_LOG` (and each `--output`) at a
  per-task directory so parallel pipelines don't collide.

```bash
export MEDIA_AI_USAGE_LOG=/tmp/run/usage.jsonl
{{cli}} image generate --prompt "..." --output /tmp/run/a.png
{{cli}} video generate --prompt "..." --output /tmp/run/b.mp4 --resolution 480p
{{cli}} usage                       # summarize $MEDIA_AI_USAGE_LOG
{{cli}} usage --log /tmp/run/usage.jsonl --pretty   # or an explicit ledger path
```

## Command & flags

| flag | notes |
|---|---|
| `--log PATH` | ledger path (default `$MEDIA_AI_USAGE_LOG` → `./media_usage.jsonl`) |
| `--pretty` | indent the JSON |
| `--metadata-out PATH` | also write the JSON to a file |

## Output

```json
{"ok": true, "schema_version": 2, "ledger": "/tmp/run/usage.jsonl",
 "totals": {"calls": N, "images_generated": N, "video_seconds": N,
            "speech_characters": N, "total_tokens": N,
            "by_binding": {"<provider>/<model>": N},
            "by_scene": {"image.text_to_image": N}}}
```

`totals` aggregates the per-generation usage recorded across the run:
`images_generated` (image), `video_seconds` (video), `speech_characters` (audio —
speech/music/sound) and `total_tokens`.

The two breakdowns are the ones that answer a cost question:

- **`by_binding`** — per `<provider>/<model>`, not per provider. Two models behind one
  provider cost different amounts, so a provider total cannot tell you which one to
  stop calling.
- **`by_scene`** — per generation scene, so "extending clips is what this run spent on"
  is answerable without correlating with your own logs.

A line whose binding or scene is unknown lands under `"?"` rather than being dropped —
finalizing a job submitted by an earlier process is real cost with no scene attached
to it.
