---
name: {{skill}}shared
description: >-
  Foundation for driving the {{cli}} generation CLI (images, video, audio —
  speech/music/sound — clip joining, async jobs) from an agent. Read this FIRST before
  using any {{skill}}* skill. Covers the machine contract (one JSON object on stdout,
  category exit codes, a stable error code and an executable hint), how a binding is
  addressed, the ask-the-CLI-what-exists rule, and the secret-safe credential model.
  Use when running any `{{cli}}` command, wiring up credentials, interpreting its
  JSON output or exit codes, or deciding which binding to send work to.
version: 2.0.0
metadata:
  requires:
    bins: ["{{cli}}"]
  cliHelp: "{{cli}} bindings list"
  install:
    tier: core
    summary: >-
      The contract every other {{cli}} skill builds on: one JSON object on stdout,
      exit codes by category, how a binding is named, and how credentials stay out of
      the agent's context.
---

# {{skill}}shared — foundation for the {{cli}} CLI

`{{cli}}` generates images, video and audio through one normalized interface.
**Read this skill first** — every `{{skill}}*` skill relies on the contract below.

## The unit you address is a **binding**

A binding is one callable `(provider, model)` pair, addressed as `<provider>/<model>`.
The same model reached through two providers is **two bindings**, each with its own
endpoint, credential, scenes and limits — so "which one am I billing?" is answered by
the id alone. Run `{{cli}} bindings list` for the ones this machine has.

## Two rules that prevent most failures

> **1. Ask what exists; never assume.** Model lineups change faster than any document.
> Run `{{cli}} bindings list` for what this machine can call right now, and
> `{{cli}} capabilities --binding <id>` for what one of them accepts. **Do not
> hardcode model ids from memory** — the manifests are the only source of truth, and
> this skill deliberately does not list them.

> **2. Nothing falls back.** A missing, ambiguous or unsuitable binding raises; the
> CLI never quietly substitutes another. Choosing again after a failure is *your*
> decision — every refusal hands you the candidates and a command that fixes it.

## Machine contract (never break these assumptions)

- **stdout is exactly one JSON object** — for success *and* failure. Parse the whole
  of stdout as one object (it spans lines under `--pretty`); do **not** parse stderr.
- **stderr is redacted human logs only.**
- **Exit code encodes the failure category** — branch on `$?` without parsing:

  | code | meaning | what to do |
  |---|---|---|
  | 0 | success | read `artifacts[]` |
  | 2 | CLI misuse, or nothing configured for this scene | read `error.hint` and run it |
  | 3 | the request does not fit the binding | read `error.details.unsupported[]` |
  | 4 | auth: no credential, or the binding is not configured | read `error.hint` |
  | 5 | rate limit / quota | retry with backoff |
  | 6 | provider / upstream error | maybe retry |
  | 7 | timeout | retry / poll the job |
  | 8 | safety / moderation block | change the prompt |
  | 9 | not found (unknown binding / job) | run `{{cli}} bindings available` |

Success carries `artifacts[]` + `usage` + `meta`, and `meta` records **which binding
ran and what scene it was asked for** — keep it, and "what produced this file?" stays
answerable later.

Failure carries a stable `error.code`, an `error.hint` that is usually a command you
can run verbatim, and `error.details` naming candidates or alternatives:

```json
{"ok": false, "error": {
  "category": "cli", "code": "ambiguous_model",
  "message": "model '<model>' is served by 2 configured bindings; say which one",
  "hint": "re-run with --binding <provider-a>/<model>",
  "details": {"candidates": ["<provider-a>/<model>", "<provider-b>/<model>"]}}}
```

Either object may also carry `notices[]` — facts about the *installation* rather than
about this call, each with a closed-set `kind` and usually a runnable `action`. It is
absent when there is nothing to say. `kind: "skills_stale"` means these instructions
were written by a different build of `{{cli}}`: run the `action` and re-read the skill,
especially if the call just failed with exit 2.

**Ignore keys you do not recognise** — new fields arrive without a `schema_version`
bump; only a change to an existing field's meaning bumps it.

Full JSON shapes → `references/machine-contract.md`.

## Naming a binding

| Flag | Meaning |
|---|---|
| `--binding <provider>/<model>` | exact, and always unambiguous — prefer this when you know it |
| `--provider P` `--model M` | the same thing in two parts |
| `--model M` | only when one configured binding serves M; otherwise `ambiguous_model` |
| *(omit all three)* | use the configured default for the scene this request implies |

**A call with no binding flags is the normal case.** Setup names a default per scene,
so `{{cli}} video generate --prompt "…" --output clip.mp4` works. Reach for
`--binding` when you need a *specific* one — a second account, a different model, a
region.

Other global flags: `--on-unsupported {error,warn,ignore}` (default `error`),
`--pretty`, `--metadata-out <path>`, `--log-level`, `--verbose` (redacted HTTP diagnostics on stderr).

## Scenes

The CLI derives a **scene** from the inputs you pass — no flag selects one:

| what you pass | scene |
|---|---|
| just `--prompt` | `image.text_to_image` / `video.text_to_video` |
| `--reference` (image) | `image.image_to_image` |
| `--first-frame` | `video.image_to_video` |
| `--first-frame` + `--last-frame` | `video.keyframe_to_video` |
| `--reference-image/-video/-audio` | `video.reference_to_video` |
| `--continue-from` | `video.extend` |

A binding declares which scenes it serves, and one it does not is refused *before the
call*, naming alternatives you have configured.

> **`--reference-video` and `--continue-from` are different requests.** A reference is
> material the model draws on; continue-from is a clip it carries on from the final
> frame of. Passing the wrong one either changes the scene or gets refused.

## Credentials — keys never touch argv

There is **no `--api-key` flag**. Each binding names one source in the config and the
CLI resolves it at call time:

```toml
credential = "env://ARK_API_KEY"      # or cred:// keychain:// broker:// op:// …
```

There is no fallback between sources: if that one does not resolve, the call fails
saying which reference failed. Setting some *other* provider's key will not help.
Full rules → `references/credentials.md`.

## Recommended flow

```bash
export MEDIA_USAGE_LOG=/tmp/run/usage.jsonl   # per-task, so concurrent runs don't mix

{{cli}} bindings list                                    # 1. what can I call?
{{cli}} capabilities --scene video.image_to_video        # 2. what accepts this work?
{{cli}} image generate --prompt "…" --output /tmp/run/ref.png          # 3. generate
{{cli}} video generate --first-frame /tmp/run/ref.png --prompt "…" \
    --output /tmp/run/clip.mp4 --wait false                             # 4. submit
# poll the `poll` string it returned until status == succeeded
{{cli}} usage                                            # 5. what did it cost?
```

## If nothing is configured

`{{cli}} bindings available` lists what could be added and prints the exact
`{{cli}} bindings add …` command for each. `{{cli}} init` is the guided version.

**Do not reach for a `placeholder` binding to make a command succeed.** Bindings whose
`capabilities` entry has `"placeholder": true` fabricate output — the offline mock
draws the prompt as text on a coloured rectangle — and hand it back as `ok: true`,
exit 0. A placeholder delivered as a deliverable is worse than a failure, because
nothing downstream can tell. The CLI never suggests one for this reason: a refusal
lists it under `available` but never in the `hint`. Use one only when the task you were
given is explicitly to exercise the pipeline offline.

## References

- `references/machine-contract.md` — success / JobHandle / JobStatus / error shapes, exit codes.
- `references/credentials.md` — reference schemes, per-binding config, secret-safety rules.
- `references/bindings.md` — how to read `bindings list` / `capabilities` output.
