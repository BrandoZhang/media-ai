# media-ai machine contract — full JSON shapes

Every `media-ai` command prints **exactly one JSON object** on stdout (`schema_version: 2`).
Parse the whole of stdout as a single JSON object (it spans multiple lines under
`--pretty`); treat stderr as redacted human logs only. Branch on the exit code (see the
table in `SKILL.md`) without parsing the message.

## Success — `GenerationResult` (image, waited video, video concat, audio speech/music/sound)

```json
{
  "ok": true,
  "schema_version": 2,
  "modality": "image",
  "provider": "openai",
  "model": "gpt-image-2",
  "artifacts": [
    {"path": "out.png", "kind": "image", "mime": "image/png", "bytes": 12345, "role": null}
  ],
  "usage": {"total_tokens": 1290},
  "meta": {"prompt": "...", "binding": "openai/gpt-image-2", "scene": "image.text_to_image"}
}
```

- **`artifacts[]` is where every produced file is, and the only place it is.** Each
  entry has `path`, `kind` (`image|video|frame|audio|timestamps|metadata|plan`),
  `mime`, `bytes` and `role`. There is no flat `path`/`bytes`/`extra_paths` beside it:
  one call can produce several files — `--count N` (image), `--return-last-frame`
  (video), `--timestamps` (a `timestamps` artifact, role `alignment`), music
  `--detailed` (a `metadata` artifact) — and a short alias that showed only the first
  made the rest easy to drop silently. Read `artifacts[0].path` for the primary file
  and iterate for the others.
- **`meta.binding` and `meta.scene` say what ran and what it was asked for.** Keep
  them with the file and "what produced this?" stays answerable — which matters most
  when you passed no binding flags and the CLI used the scene default.
- Audio (speech/music/sound) and image are **synchronous** — they return this
  `GenerationResult`, never a `JobHandle`.

## Async submit — `JobHandle` (`video generate --wait false`)

```json
{
  "ok": true, "schema_version": 2,
  "status": "queued", "modality": "video",
  "provider": "gemini", "model": "<wire model id>",
  "job": {"provider": "gemini", "model": "<wire model id>", "id": "<job-id>",
          "binding": "<provider>/<model>"},
  "output": "/tmp/run/clip.mp4",
  "poll": "media-ai job query --binding <provider>/<model> --id <job-id> --output /tmp/run/clip.mp4",
  "meta": {}
}
```

> The `poll` field is a **ready-to-run command** — execute it verbatim to check status
> and finalize. It names the *binding*, not just the provider, because one provider can
> serve several bindings and only the binding identifies the job's owner.

## Poll / cancel — `JobStatus` (`job query` / `job cancel`)

```json
{
  "ok": true, "schema_version": 2, "op": "query",
  "provider": "gemini", "model": "<wire model id>",
  "id": "<job-id>", "status": "succeeded"
}
```

`status` ∈ `queued | running | succeeded | failed | cancelled | expired`. When the
job **finished and `--output` was given**, the object also merges the finalized
`modality`, `artifacts`, `usage` and `meta` (the downloaded file), and may include a
provider-specific `raw` block. `meta.scene` is absent there: the request that implied
a scene belonged to the earlier process that submitted the job.

## Failure — error contract (any command)

```json
{
  "ok": false,
  "error": {
    "category": "unsupported",
    "code": "unsupported",
    "message": "background 'transparent' not supported by <model>",
    "retryable": false,
    "provider": "openai",
    "model": "<model>",
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
- `--verbose` — print redacted binding and HTTP request diagnostics to stderr; stdout
  stays machine-readable.
- **List flags accept a single JSON-array string** — how agent tool layers pass lists:
  `--reference '["a.png","b.png"]'`, `--inputs '["a.mp4","b.mp4"]'`. Plain
  space-separated paths also work: `--reference a.png b.png`.
- `--option key=value` — provider-specific, capability-gated (unknown key → exit 3).
  Values are coerced: `true/false`, ints, floats (e.g. `guidance_scale=7.5`), else string.
