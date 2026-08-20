# Invoking media-ai from an Agent Skill

`media-ai` is built to be driven by an autonomous agent as a subprocess. The
contract below is deterministic so a Skill can act on results without heuristics.

> **Packaged skills:** ready-made Agent Skills that implement this contract — one per
> capability area — live in [`../src/media_ai/skills/`](../src/media_ai/skills/) (start with `shared/`, installed as `media-ai-shared`).
> This document is the underlying contract they build on.

## The output contract

- **stdout is exactly one JSON object** — for both success and failure. Parse the
  whole of stdout as a single JSON object (it spans multiple lines under `--pretty`).
- **stderr is redacted human logs only.** Never parse it.
- **Exit code encodes the failure category** — branch on `$?` without parsing:

  | code | meaning | retry? |
  |---|---|---|
  | 0 | success | — |
  | 2 | CLI misuse, or nothing configured for this scene | run `error.hint` |
  | 3 | the request does not fit the binding | read `error.details.unsupported[]` |
  | 4 | auth: no credential, or the binding is not configured | run `error.hint` |
  | 5 | rate limit / quota | retry with backoff |
  | 6 | provider / upstream error | maybe retry |
  | 7 | timeout | retry / poll |
  | 8 | safety / moderation block | change the prompt |
  | 9 | not found (unknown binding / job) | `media-ai bindings available` |

Success shape:

```json
{"ok": true, "schema_version": 2, "modality": "image",
 "provider": "openai", "model": "gpt-image-2",
 "artifacts": [{"path": "out.png", "kind": "image", "mime": "image/png", "bytes": 12345, "role": null}],
 "usage": {"total_tokens": 1290},
 "meta": {"prompt": "…", "binding": "openai/gpt-image-2", "scene": "image.text_to_image"}}
```

**Every produced file is in `artifacts[]`** — there is no flat `path` beside it. Read
`artifacts[0].path` for the primary one and iterate for the rest; `--count 3`, a
timestamps sidecar and a returned last frame all arrive there, each with a `role`.

`meta.binding` and `meta.scene` record **what actually ran and what it was asked
for** — keep them, and "what produced this file?" stays answerable later. They matter
most exactly when the CLI chose the binding itself, from the scene default.

Failure shape:

```json
{"ok": false, "error": {"category": "unsupported", "code": "request_not_supported",
 "message": "…", "retryable": false, "provider": "openai", "model": "gpt-image-2",
 "hint": "media-ai capabilities --binding openai/gpt-image-2",
 "details": {"binding": "openai/gpt-image-2", "scene": "image.text_to_image",
             "unsupported": [{"field": "background", "reason": "…"}]}}}
```

**`error.code` is stable and `error.hint` is usually runnable verbatim.** Because
nothing falls back, a refusal owes the caller a next step: the codes worth branching
on are `no_default_binding`, `binding_not_configured`, `ambiguous_model`,
`scene_not_supported`, `credential_unresolved` and `request_not_supported`, and each
carries the candidates or alternatives in `details`.

## Recommended flow

1. **Ask what exists.** `media-ai bindings list` (what this machine can call) and
   `media-ai capabilities --scene <scene>` or `--binding <id>` (what one accepts).
   **Never hardcode model ids** — the manifests are the source of truth and lineups
   change. Choose a request that fits the declared constraints.
2. **Generate.** Pass `--output` (and `MEDIA_AI_USAGE_LOG`) inside a per-task
   directory so concurrent tasks don't collide. Read `artifacts[]` for paths.
3. **Async video.** `video generate --wait false` returns
   `{"status":"queued","job":{"binding","model","id"},"poll":"media-ai job query …"}`.
   **Run the `poll` string verbatim** — it names the binding that submitted the job,
   which matters because one provider can serve several. When `status` is
   `succeeded`/`completed` the artifact is downloaded and finalized. (With
   `--wait true`, the CLI blocks and polls for you.)
4. **Handle unsupported deterministically.** Default `--on-unsupported error`
   exits 3 with the exact rejected fields; only pass `warn`/`ignore` if you
   deliberately want best-effort behavior.
5. **Account.** `media-ai usage` returns token/artifact totals for the run.

## Machine-friendly flags

- `--metadata-out path.json` — also write the (secret-free) result JSON to a file.
- `--verbose` — print redacted binding and HTTP request diagnostics to stderr; stdout
  remains the one JSON result.
- lists accept a JSON array string: `--reference '["a.png","b.png"]'`.
- `--binding <provider>/<model>` names one exactly; `--provider`+`--model` is the same
  in two parts; `--model` alone works only when one configured binding serves it.
- **Omitting all three is normal** — the configured default for the derived scene runs.
  That is the only automatic choice the CLI makes.

## Secrets

Do **not** put provider keys in the command line or in the Skill's context. Each
binding names one source (`env://`, `cred://`, `keychain://`, `broker://`, …) in the
config; see [CREDENTIALS.md](CREDENTIALS.md). The CLI resolves and redacts them, and
the key never appears in stdout, stderr, or `--metadata-out`.

There is **no fallback between sources**: if the named one does not resolve the call
fails saying which reference failed. Do not try another provider's key to work around
it, and do not fall back to `--binding mock/mock` to make a command succeed — mock
draws placeholders, and a placeholder returned as a deliverable is worse than a
failure.
