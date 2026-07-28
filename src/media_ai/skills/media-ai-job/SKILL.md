---
name: media-ai-job
description: >-
  Poll, finalize (download), or cancel an asynchronous media-ai video generation
  job. Use after `media-ai video generate --wait false` returns a JobHandle, or
  whenever asked to check the status of a running/queued generation, wait for a
  video job to finish, download a completed job's output, or cancel/stop a
  pending job to save cost.
version: 2.0.0
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

> **Read `../media-ai-shared/SKILL.md` first** for the machine contract and how a
> binding is named. This skill manages jobs created by the `media-ai-video` skill.

**Video generation is async on every real backend.** `media-ai video generate --wait
false` submits a task and returns a `JobHandle` instead of blocking; `media-ai job`
then polls, finalizes, or cancels it.

## The lifecycle

```bash
# 1. submit -> JobHandle. Keep the whole object: `job.binding`, `job.id`, and the
#    ready-to-run `poll` string.
media-ai video generate --prompt "..." --output /tmp/run/clip.mp4 \
    --wait false --metadata-out /tmp/run/job.json

# 2. poll until done. WITH --output, a succeeded job is downloaded + finalized here.
media-ai job query --binding <provider>/<model> --id <job-id> --output /tmp/run/clip.mp4

# 3. (optional) cancel a still-queued/running job to stop cost — where supported
media-ai job cancel --binding <provider>/<model> --id <job-id>
```

**The `JobHandle.poll` field *is* the exact command to run — execute it verbatim.**
It already names the right binding, which is the part that is easy to get wrong.

## Commands & flags

| command | flags | purpose |
|---|---|---|
| `media-ai job query` | `--binding` (or `--provider`+`--model`), `--id` (required), `--output PATH` | check status; with `--output`, download the finished artifact and finalize |
| `media-ai job cancel` | `--binding` (or `--provider`+`--model`), `--id` (required) | stop a queued/running job (cost control) |

**Name the binding, not just the provider.** A job id is scoped to the binding that
created it, and one provider can serve several — `--provider` alone is ambiguous
whenever it does. There is no scene default to fall back on here: a job identifies its
own binding, which is why the handle carries one.

## Reading `job query` output (`JobStatus`)

```json
{"ok": true, "op": "query", "provider": "<provider>", "id": "<job-id>", "status": "running"}
```

- `status` ∈ `queued | running | succeeded | failed | cancelled | expired`.
- Poll on `queued`/`running`. On `succeeded` **with `--output`**, the object also
  carries the finalized `modality` / `artifacts` / `usage` / `meta` (the downloaded
  file). `meta.scene` is absent — the request that implied one belonged to the process
  that submitted the job.
- `failed` → inspect the error (a safety-blocked task surfaces as **exit 8**).
- An id the backend does not recognize → **exit 9** (`not_found`).

> Poll politely — a few seconds between calls. A blocking `--wait true` uses the
> binding's own poll interval and timeout, both configurable per binding in the config
> rather than through the environment.

## Cancel is not universal

Some backends can cancel a queued task and stop the charge; others cannot cancel a
long-running operation at all, and `job cancel` there is **exit 3** (`unsupported`).
Check before you rely on it:

```bash
media-ai capabilities --binding <provider>/<model> --pretty   # constraints.supports.cancel
```

Where cancellation exists, a blocking `--wait true` run also cancels its billed task
if it is interrupted or times out. Where it does not, the only way not to pay for an
unwanted job is not to submit it.
