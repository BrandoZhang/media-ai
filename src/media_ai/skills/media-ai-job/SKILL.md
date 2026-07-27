---
name: media-ai-job
description: >-
  Poll, finalize (download), or cancel an asynchronous media-ai video generation
  job. Use after `media-ai video generate --wait false` returns a JobHandle, or
  whenever asked to check the status of a running/queued generation, wait for a
  video job to finish, download a completed job's output, or cancel/stop a
  pending job to save cost.
version: 1.0.0
metadata:
  requires:
    bins: ["media-ai"]
  cliHelp: "media-ai job query --help"
  install:
    tier: dependency
    summary: >-
      Poll, download, or cancel a queued generation. Installed automatically with any
      skill that can hand back a job instead of a finished file.
---

# media-ai-job — async job lifecycle

> **Read `../media-ai-shared/SKILL.md` first** for the machine contract and provider
> selection. This skill manages jobs created by the `media-ai-video` skill.

Real-provider **video is async**. `media-ai video generate --wait false` submits a
task and returns a `JobHandle` instead of blocking. `media-ai job` then polls,
finalizes, or cancels it.

## The lifecycle

```bash
# 1. submit async -> JobHandle (note the job.id, provider, and the ready-to-run `poll` string)
media-ai video generate --provider gemini --model veo-3.1-generate-preview \
    --prompt "..." --output /tmp/run/clip.mp4 --wait false --metadata-out /tmp/run/job.json

# 2. poll until done. WITH --output, a succeeded job is downloaded + finalized here.
media-ai job query --provider gemini --id <op-id> --output /tmp/run/clip.mp4

# 3. (optional) cancel a still-queued/running job to stop cost
media-ai job cancel --provider volc --id <task-id>
```

The `JobHandle.poll` field **is** the exact `media-ai job query` command to run — execute it
verbatim.

## Commands & flags

| command | flags | purpose |
|---|---|---|
| `media-ai job query` | `--provider`, `--id` (required), `--model`, `--output PATH` | check status; with `--output`, download the finished artifact and finalize |
| `media-ai job cancel` | `--provider`, `--id` (required), `--model` | stop a queued/running job (cost control) |

`--provider` (and `--model`, when the id needs it) must match the submitting call —
the id is provider-scoped.

## Reading `job query` output (`JobStatus`)

```json
{"ok": true, "op": "query", "provider": "gemini", "id": "<op>", "status": "running"}
```

- `status` ∈ `queued | running | succeeded | failed | cancelled | expired`.
- Poll on `queued`/`running`. On `succeeded` **with `--output`**, the object also
  carries the finalized `path` / `artifacts` / `usage` / `meta` (the downloaded file).
- `failed` → inspect the error (a safety-blocked task surfaces as **exit 8**).

> Poll politely (a few seconds between calls). Providers already enforce their own
> poll timeouts (`ARK_POLL_TIMEOUT` 900s, `GEMINI_POLL_TIMEOUT` 1200s) when you use
> `--wait true` instead.

## Cancel support (differs by provider)

| provider | `job cancel` |
|---|---|
| `volc` (Seedance) | ✓ (also auto-cancels a killed `--wait true` task) |
| `gemini` (Veo) | ✗ — cannot cancel; `job cancel` returns **exit 3** (`unsupported`) |
| `openai` | n/a — no video jobs (Sora retired); any `job` op returns **exit 3** |
| `mock` | ✓ (simulated) |

For Veo, if you must not pay for an unwanted job, avoid submitting it — there is no
cancel. A `--id` the provider doesn't recognize → **exit 9** (`not_found`).
