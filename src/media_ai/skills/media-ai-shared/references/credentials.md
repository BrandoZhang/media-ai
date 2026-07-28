# Credentials — one binding, one named source

The design rule, borrowed from Anthropic's managed-agents guidance: **if you don't
want your AI agent to reveal a secret, don't give it the secret.** A raw key must
never reach an agent's context, the CLI's argv, logs, or generated metadata.

## What an agent may and may not do

| | |
|---|---|
| **Never** | pass a key as a flag — there is no `--api-key`, and argv is world-readable via `ps` |
| **Never** | echo, log, or write a key into a file the agent can read back |
| **Never** | put a raw key in `config.toml` — the CLI refuses it, because that file is the shareable one |
| Do | name a binding and let the CLI resolve its credential |
| Do | read `error.hint` when resolution fails and run it, or tell the user what to set |

## Each binding names exactly one source

```toml
# ~/.config/media-ai/config.toml — non-secret, safe to share
[bindings."volc-ark/seedance-2.0"]
credential = "env://ARK_API_KEY"
```

| reference | resolves from |
|---|---|
| `env://VAR` | an environment variable |
| `cred://<account>` | a `[<account>]` block in `~/.config/media-ai/credentials.toml` (chmod 600) |
| `keychain://<service>/<account>` | the OS keychain (needs the `keychain` extra) |
| `broker://<host>` | a credential broker — this process holds only a session token |
| `op://…`, `vault://…`, … | a registered secret-manager backend |

**There is no fallback between them.** If `env://ARK_API_KEY` is unset, the call fails
naming that reference. Setting a different provider's key, or the same key somewhere
else, will not rescue it — that is the point. "Which key did this call use?" has a
one-line answer.

## When resolution fails

```json
{"ok": false, "error": {
  "category": "auth", "code": "credential_unresolved",
  "message": "credential 'env://ARK_API_KEY' did not resolve: environment variable ARK_API_KEY is unset or empty"}}
```

Exit 4. Two distinct causes worth telling apart:

| code | meaning |
|---|---|
| `credential_unresolved` | the binding is configured; its source is empty |
| `binding_not_configured` | the binding is declared but this machine has no entry for it — `error.hint` is the `bindings add` command |
| `credential_is_raw_key` | someone put a key where a reference belongs |

## Storing a key

`media-ai bindings add <id> --credential env://VAR` writes the reference only.
`media-ai init` also offers to store the key itself in `credentials.toml`
(chmod 600, refused if group- or world-readable). Choosing the `env://` route means
**no secret file is written at all**.

## Sharing one key between bindings

Two bindings on one account each name the key. That repetition is deliberate: the
config states outright which key every call uses. If you would rather rotate in one
place, point both at the same account:

```toml
[bindings."volc-ark/seedream-4.5"]
credential = "cred://shared-ark"
[bindings."volc-ark/seedance-2.0"]
credential = "cred://shared-ark"
```

## Redaction

Every sink — stdout JSON, stderr logs, error messages — passes through a redactor that
masks live secret values and key-shaped tokens. A `Secret` is reveal-only and is not
JSON-serializable, so it cannot reach the result by accident.
