# media-ai machine contract — full JSON shapes

Every `media-ai` command prints **exactly one JSON object** on stdout (`schema_version: 1`).
Parse the whole of stdout as a single JSON object (it spans multiple lines under
`--pretty`); treat stderr as redacted human logs only. Branch on the exit code (see the
table in `SKILL.md`) without parsing the message.

## Success — `GenerationResult` (image generate/edit, waited video, audio speech/music/sound, concat)

```json
{
  "ok": true,
  "schema_version": 1,
  "modality": "image",
  "operation": "image.generate",
  "provider": "openai",
  "model": "gpt-image-2",
  "artifacts": [
    {"path": "out.png", "kind": "image", "mime": "image/png", "bytes": 12345, "role": null}
  ],
  "kind": "image",              // compat alias == modality
  "path": "out.png",            // compat alias == artifacts[0].path
  "bytes": 12345,               // compat alias == artifacts[0].bytes
  "extra_paths": [],            // compat alias == artifacts[1:].path
  "usage": {"total_tokens": 1290},
  "meta": {"prompt": "..."}
}
```

- `artifacts[]` is the source of truth: each has `path`, `kind`
  (`image|video|frame|audio|timestamps|metadata|plan`), `mime`, `bytes`, `role`. `--count N`
  (image), `--return-last-frame` (video), or a sidecar (`--timestamps` → a `timestamps`
  artifact, role `alignment`; music `--detailed` → a `metadata` artifact) can add more
  than one artifact; `path`/`bytes` mirror `artifacts[0]`, `extra_paths` the rest.
  Audio (speech/music/sound) is **synchronous** — it returns this `GenerationResult`, not
  a `JobHandle`.
- The `path`/`bytes`/`extra_paths`/`kind` aliases exist for one release — prefer `artifacts[]`.

## Async submit — `JobHandle` (`video generate --wait false`)

```json
{
  "ok": true, "schema_version": 1,
  "status": "queued", "kind": "video", "modality": "video",
  "provider": "gemini", "model": "veo-3.1-generate-preview",
  "job": {"provider": "gemini", "model": "veo-3.1-generate-preview", "id": "<op-id>"},
  "task_id": "<op-id>",         // compat alias == job.id
  "output": "/tmp/run/clip.mp4",
  "poll": "media-ai job query --binding gemini/nano-banana-2 --id <op-id> --output /tmp/run/clip.mp4",
  "meta": {}
}
```

> The `poll` field is a **ready-to-run command** — execute it verbatim to check
> status and finalize. Use `job.id` + `job.provider` if you build the call yourself.

## Poll / cancel — `JobStatus` (`job query` / `job cancel`)

```json
{
  "ok": true, "schema_version": 1, "op": "query",
  "provider": "gemini", "model": "veo-3.1-generate-preview",
  "id": "<op-id>", "status": "succeeded"
}
```

`status` ∈ `queued | running | succeeded | failed | cancelled | expired`. When the
job **finished and `--output` was given**, the object also merges the finalized
`kind`, `path`, `artifacts`, `extra_paths`, `usage`, `meta` (the downloaded file),
and may include a provider-specific `raw` block.

## Failure — error contract (any command)

```json
{
  "ok": false,
  "error": {
    "category": "unsupported",
    "code": "unsupported",
    "message": "background 'transparent' not supported by gpt-image-2",
    "retryable": false,
    "provider": "openai",
    "model": "gpt-image-2",
    "details": {"unsupported": [{"field": "background", "reason": "..."}]}
  }
}
```

- `category` maps 1:1 to the exit code (`cli`→2, `validation`/`unsupported`→3,
  `auth`→4, `rate_limit`→5, `provider`→6, `timeout`→7, `safety`→8, `not_found`→9;
  `io`/`unknown`→1). `retryable` is true for `rate_limit`, `timeout`, `provider`.
- `details.unsupported[]` lists exactly which request fields were rejected — the
  precise fix for an exit-3 failure.
- `io` and `unknown` both map to **exit 1** — a local failure (unreadable input, unwritable output) or an unclassified one. Neither is retryable on its own; read `error.message`.

## Machine-friendly flags

- `--metadata-out path.json` — also write the (secret-free) result JSON to a file
  (pretty). Parent dirs are created; a write failure only logs a warning.
- `--pretty` — indent the stdout JSON (human-readable; still one object).
- **List flags accept a single JSON-array string** — how agent tool layers pass lists:
  `--reference '["a.png","b.png"]'`, `--inputs '["a.mp4","b.mp4"]'`. Plain
  space-separated paths also work: `--reference a.png b.png`.
- `--option key=value` — provider-specific, capability-gated (unknown key → exit 3).
  Values are coerced: `true/false`, ints, floats (e.g. `guidance_scale=7.5`), else string.
