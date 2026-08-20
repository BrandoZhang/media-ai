# {{cli}} machine contract — full JSON shapes

Every `{{cli}}` command prints **exactly one JSON object** on stdout (`schema_version: 2`).
Parse the whole of stdout as a single JSON object (it spans multiple lines under
`--pretty`); treat stderr as redacted human logs only. Branch on the exit code (see the
table in `SKILL.md`) without parsing the message.

**Ignore keys you do not recognise.** New fields are added without bumping
`schema_version`; only a change to what an existing field *means* bumps it. A parser
that rejects unknown keys breaks on an ordinary release.

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
  "poll": "{{cli}} job query --binding <provider>/<model> --id <job-id> --output /tmp/run/clip.mp4",
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
  "schema_version": 2,
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
- Two other exit-3 codes come from the **published feed** rather than from the request,
  and both are refusals to start rather than failures of the call:
  - `binding_retired` — that binding is gone upstream. `details.alternatives[]` names
    what to use instead, and `hint` names one this machine can already call.
    `--allow-retired-binding` forces the call through; it will probably fail, and it is
    **not for an agent to set unprompted**.
  - `version_unsupported` — this build is below the published minimum. There is no
    override. `{{cli}} doctor`, `version`, `upgrade` and `uninstall` keep working, so
    the way out is always reachable.
- `io` and `unknown` both map to **exit 1** — a local failure (unreadable input, unwritable output) or an unclassified one. Neither is retryable on its own; read `error.message`.

## `notices[]` — about the installation, not the call

Any object above may carry a `notices` array, on success and on failure alike. It is
**absent when there is nothing to say**, so treat a missing key as an empty list.

```json
{
  "ok": true, "schema_version": 2, "artifacts": [],
  "notices": [
    {
      "kind": "skills_stale",
      "severity": "warn",
      "message": "Agent Skills in /home/me/.claude/skills were installed by a different {{cli}} build; this one is 0.6.0.",
      "action": "{{cli}} init --skills-only"
    }
  ]
}
```

- A notice is about the **installation**, not about the call — the call itself
  succeeded or failed on its own terms, and the exit code is unaffected.
- **`kind` is the field to branch on**, from a closed set; `message` is prose for a
  human or a model to read and must not be pattern-matched. Unknown kinds will appear
  over time — ignore the ones you do not handle.
- `severity` is `info` or `warn`.
- **`action`, when present, is a command that can be run verbatim.**

| `kind` | `severity` | what it means | what to do |
|---|---|---|---|
| `skills_stale` | `warn` | The `{{cli}}` skills installed in an agent directory were written by a different build, so these instructions may describe flags this CLI no longer has | Run the `action`, then re-read the skill |
| `update_available` | `info` | A newer `{{cli}}` release is published. Read from a cached answer — no command ever waits on the network for this | Nothing, unless upgrading is yours to decide. The `action` is the command for it, and `{{cli}} upgrade` runs it — neither is for an agent to do unprompted |
| `binding_deprecated` | `info` or `warn` | The binding this call used is on its way out, and it still worked this time. `info` — the build's own manifest declares a successor. `warn` — the published feed says it is being retired upstream, which is nearer and less certain | The `message` names what replaces it. The `action` shows you that replacement's limits, or lists what can be added when it is not configured here — check the fit, then switch |
| `telemetry_unavailable` | `warn` | Telemetry is enabled here but the OpenTelemetry SDK is not installed, so nothing is being exported. Asking for telemetry and getting silence looks exactly like a collector that drops everything, which is why it is said out loud | Nothing, unless the install is yours to change. The `action`, when present, is the command that adds the SDK |
| `env_renamed` | `warn` | A `MEDIA_`-prefixed environment variable is set that this build no longer reads — the prefix is now `MEDIA_AI_`. The old value is **ignored**, so whatever it was pointing at (a scratch config, telemetry being off) is not in effect. Only raised for the few whose loss is silent — config and credential paths, and switches whose absence turns something outbound back on | The `message` names the old and new spelling of each. There is no `action`: the fix is an edit to whatever exports them — a shell profile, a Dockerfile, a CI job — not a command to run |

`skills_stale` is worth acting on the moment you see it — especially alongside an
exit-2 "invalid command-line arguments", which is what following out-of-date skill
text usually looks like from here.

They describe a **condition, not an event**, so they repeat on every command until the
condition clears. Do not treat a repeat as new information, and do not act twice.

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
