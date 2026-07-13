# Invoking media-ai from an Agent Skill

`media-ai` is built to be driven by an autonomous agent as a subprocess. The
contract below is deterministic so a Skill can act on results without heuristics.

> **Packaged skills:** ready-made Agent Skills that implement this contract — one per
> CLI functionality — live in [`../skills/`](../skills/) (start with `media-ai-shared`).
> This document is the underlying contract they build on.

## The output contract

- **stdout is exactly one JSON object** — for both success and failure. Parse the
  last line of stdout.
- **stderr is redacted human logs only.** Never parse it.
- **Exit code encodes the failure category** — branch on `$?` without parsing:

  | code | meaning | retry? |
  |---|---|---|
  | 0 | success | — |
  | 2 | CLI misuse (bad flags) | fix the invocation |
  | 3 | validation / unsupported option | fix the request |
  | 4 | auth / missing credentials | fix credentials |
  | 5 | rate limit / quota | retry with backoff |
  | 6 | provider / upstream error | maybe retry |
  | 7 | timeout | retry / poll |
  | 8 | safety / moderation block | change the prompt |
  | 9 | not found (job/model) | — |

Success shape:

```json
{"ok": true, "schema_version": 1, "modality": "image", "operation": "image.generate",
 "provider": "openai", "model": "gpt-image-2",
 "artifacts": [{"path": "out.png", "kind": "image", "mime": "image/png", "bytes": 12345, "role": null}],
 "usage": {"total_tokens": 1290}, "meta": {"prompt": "…"}}
```

Failure shape:

```json
{"ok": false, "error": {"category": "unsupported", "code": "unsupported",
 "message": "…", "retryable": false, "provider": "openai", "model": "gpt-image-2",
 "details": {"unsupported": [{"field": "background", "reason": "…"}]}}}
```

## Recommended flow

1. **Discover** what's possible: `media-ai capabilities --provider <p>` (or
   `--model <m>`) → JSON of operations, geometry mode, allowed ratios/sizes,
   options, async. Choose a request that fits.
2. **Generate.** Pass `--output` (and `MEDIA_USAGE_LOG`) inside a per-task
   directory so concurrent tasks don't collide. Read `artifacts[]` for paths.
3. **Async video.** `video generate --wait false` returns
   `{"status":"queued","job":{"provider","model","id"},"poll":"media-ai job query …"}`.
   Poll `media-ai job query --provider <p> --id <id> --output <path>`; when
   `status` is `succeeded`/`completed` the artifact is downloaded and finalized.
   (With `--wait true`, the CLI blocks and polls for you.)
4. **Handle unsupported deterministically.** Default `--on-unsupported error`
   exits 3 with the exact rejected fields; only pass `warn`/`ignore` if you
   deliberately want best-effort behavior.
5. **Account.** `media-ai usage` returns token/artifact totals for the run.

## Machine-friendly flags

- `--metadata-out path.json` — also write the (secret-free) result JSON to a file.
- lists accept a JSON array string: `--reference '["a.png","b.png"]'`.
- `--provider` / `--model` are explicit; a model id can imply the provider.

## Secrets

Do **not** put provider keys in the command line or in the Skill's context. Set
them in the environment (or a broker/keychain/secret-manager) per
[CREDENTIALS.md](CREDENTIALS.md). The CLI resolves and redacts them; the key never
appears in stdout, stderr, or `--metadata-out`.
