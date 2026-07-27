# Credentials

The design goal, borrowed from Anthropic's managed-agents guidance: **"if you
don't want your AI agent to reveal a secret, don't give it the secret."** A raw
provider key must never reach the Agent Skill's context, the CLI's argv, logs,
generated metadata, or any model-visible surface.

## Trust boundaries

| Component | Trust | Sees the raw key? |
|---|---|---|
| Agent Skill (decides to run the CLI) | Untrusted (prompt-injectable) | **Never** — passes only a provider name |
| CLI process | Semi-trusted | Only in-memory, at HTTP-call time, and only in single-process mode |
| Provider adapters (volc/openai/gemini) | Same process | Receive a `Secret` handle, never a logged/serialized string |
| Secret stores / broker | Trusted | Where raw secrets live |

The CLI selects a provider by **name**; the registry binds a credential resolver
to the adapter; the adapter reveals the value only inside its HTTP request builder
(`src/media_ai/providers/_base.py`). Credentials never pass through `src/media_ai/cli/`.

## Which config file do I use?

There are three config surfaces, but they are **one precedence-ordered chain**, not
competing options — so they can coexist without ambiguity. Pick the row that fits;
you do **not** need more than one for keys.

| File | Holds | Role | Committed? |
|---|---|---|---|
| `.env` (project-local) | provider env vars | quick dev / CI; **lowest** priority | no (`.env.example` is) |
| `~/.config/media-ai/credentials.toml` | **accounts** — raw keys (or references), `chmod 600` | durable per-user secrets; **outranks `.env`** | no (`credentials.toml.example` is) |
| `~/.config/media-ai/config.toml` | **non-secret** profiles (references + provider/model/base_url) | route to different endpoints/tenants/models | yes — safe to share (no secrets) |

**The model: key by *account*, not just by provider.** `credentials.toml` is a flat
list of **accounts** — each `[<name>]` block is a name you choose plus its key.
`config.toml` is a list of **profiles** (`[profiles.<name>]`) that pick a provider +
model and refer to an account by name (`credential = "cred://<name>"`). An account
named after a provider (`[openai]`) is simply that provider's default. Secrets live
only in the `chmod 600` `credentials.toml`; the shareable `config.toml` holds
references and routing, never a raw key. This is what lets one provider hold several
keys (per account / per model) and a profile pick — or fall back across — them.

**Per-modality defaults live in `[providers.<name>]`.** A profile pins a single
`model`, which can't express "images use one model and video another" on the same
provider — and those really are different models (Gemini's image, Veo, and TTS
families are disjoint; ElevenLabs splits further by operation). So `config.toml`
also takes a non-secret per-provider table:

```toml
[providers.gemini]
image_model = "gemini-3-pro-image"
video_model = "veo-3.1-fast-generate-preview"
```

Model resolution, most specific first: `--model` → the active profile's `model` →
`[providers.<name>]` → the provider's env var (`GEMINI_IMAGE_MODEL`, …) → the
adapter's built-in default. Unknown keys are ignored, so a newer config stays
readable by an older CLI.

**Precedence when the same provider is set in more than one place:** broker →
secret-manager reference → OS keychain → **`credentials.toml`** → **`.env`/env** (see
the chain below). So if a key is in *both* `credentials.toml` and `.env`,
**`credentials.toml` wins**. `config.toml` isn't a key source — it *selects* one (via
a reference) and is deliberately separate so it can be shared without holding secrets.

> **Just want to get running?** Copy [`.env.example`](../.env.example) to `.env`,
> fill in your provider's block, and `uv run --env-file .env media-ai …` (or
> `set -a && . ./.env && set +a`). For durable local keys instead, copy
> [`credentials.toml.example`](../credentials.toml.example) to
> `~/.config/media-ai/credentials.toml` and `chmod 600` it. For per-endpoint routing,
> copy [`config.toml.example`](../config.toml.example) to `~/.config/media-ai/config.toml`.

## Resolution chain (most-secure first, first hit wins)

Configured in `src/media_ai/credentials/resolver.py`; re-resolved **per invocation**
so rotation and short-lived tokens are picked up automatically.

1. **Broker** — if `MEDIA_CRED_BROKER` is set, returns a `BrokeredHandle` that
   holds only a session token (`MEDIA_CRED_BROKER_TOKEN`) + the broker endpoint —
   **no secret**. See "Brokered mode" below.
2. **Secret-manager reference** — a value like `op://…`, a Vault path, an AWS ARN,
   or `env://VARNAME` (from `MEDIA_SECRET_REF_<PROVIDER>` or the config file) is
   resolved at runtime. Register backends with
   `media_ai.credentials.stores.register_secret_backend(scheme, fn)`.
3. **OS keychain** — via the optional `keyring` extra: item `media-ai`/`<provider>`.
   Disable with `MEDIA_DISABLE_KEYCHAIN=1`.
4. **Config file** — `~/.config/media-ai/credentials.toml` (override with
   `MEDIA_CREDENTIALS_FILE`; template: [`credentials.toml.example`](../credentials.toml.example)).
   **Must be `chmod 600`** — a group/world-readable file is refused. Outranks the
   environment, so a key here beats the same key in `.env`. Each `[<name>]` block is an
   **account**; an account named after a provider is that provider's default:
   ```toml
   [openai]                       # provider default (account named after the provider)
   api_key = "sk-…"
   [volc]
   api_key = "…"                  # or a reference: api_key = "op://vault/volc/key"

   [volc_account_a]               # an extra account, referenced from a profile
   api_key = "…"                  #   as cred://volc_account_a
   ```
5. **Environment** — `OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`,
   `ARK_API_KEY`/`VOLC_API_KEY`, `ELEVENLABS_API_KEY`/`ELEVEN_API_KEY` (e.g. via `.env`).

If nothing resolves, the CLI exits **4** (auth) with an actionable message.

## Accounts (multiple keys per provider)

The plain chain resolves **one** default credential per provider (the account named
after it). When a provider needs **several** keys — different accounts, tenants, or
per model/endpoint — add more accounts and reference one from a profile:

```toml
# credentials.toml (chmod 600) — the secrets, one [<name>] block per account
[volc_account_a]
api_key = "…"
[volc_account_b]
api_key = "op://vault/volc/account-b"   # an account's key may itself be a reference
```

A profile then selects one with `credential = "cred://volc_account_a"` (see below).
`cred://<name>` looks up the `[<name>]` account and is resolved through the same lazy
machinery as any other reference, so the value is revealed only inside the adapter and
redacted everywhere else. `cred://` is a scheme (like `env://`/`op://`) rather than a
bare name so the shareable `config.toml` can be checked to contain **no raw keys** and
a fallback list can mix account/env/secret-manager references uniformly.

## The `Secret` handle

`src/media_ai/credentials/secret.py`. The plaintext is accessible only via
`.reveal()`. `repr`/`str`/pickle render `***` or a source descriptor, and the
value is **not JSON-serializable** — so it cannot accidentally land in the result
JSON. On creation the value is registered with the redactor.

## Profiles (per-endpoint / per-tenant credentials)

By default a provider resolves **one** credential per process (`volc` →
`ARK_API_KEY`). When different endpoints/projects need different keys — e.g. an
image endpoint on account A and a video endpoint on account B — bind them with a
**profile**: a named bundle of `provider` + default `model` + optional `base_url`
+ a `credential` **reference**.

Profiles live in `~/.config/media-ai/config.toml` (override `MEDIA_CONFIG_FILE`)
and are **non-secret** — `credential` is a reference (`cred://<name>` for the
`[<name>]` account in `credentials.toml`, `env://VAR`, `op://…`, a Vault path), never
a raw key, so the file is safe to share/commit:

```toml
[profiles.prod_image]
provider   = "volc"
model      = "ep-image-A"
credential = "cred://volc_account_a"     # → the [volc_account_a] account

[profiles.prod_video]
provider   = "volc"
model      = "ep-video-B"
base_url   = "https://ark.cn-beijing.volces.com/api/v3"
credential = "cred://volc_account_b"
```

Select one with `--provider-profile prod_video` (or `$MEDIA_PROFILE`); it applies
to `image`/`video`/`job`:

```bash
media-ai video generate --provider-profile prod_video --prompt "…" --output v.mp4
```

### Fallback: define more keys than you use

`credential` may be an **ordered list** of references. At call time each is tried in
order and the **first that resolves wins**, so you can provision extra keys and
degrade gracefully when the preferred one is absent (rotated out, not present on this
machine, …):

```toml
[profiles.prod_video_ha]
provider   = "volc"
model      = "ep-video-B"
credential = ["cred://volc_account_b", "cred://volc_shared", "env://ARK_API_KEY"]
```

A **single** reference is strict — an absent source is an error, never a silent jump
to a different account's key. A **list** is the explicit opt-in to fall through: an
*absent* source (unset env var, missing `[<name>]` account) is skipped, but a
genuine *misconfiguration* (unknown scheme, a `chmod`-refused credentials file, a
reference cycle) is always surfaced rather than silently swallowed. If the whole list
is exhausted, the error names every reference it tried.

Precedence: explicit `--provider`/`--model` override the profile; a profile's
`credential` reference(s) are resolved first, and a **credential-less** profile falls
back to the normal chain (so it still ends at `ARK_API_KEY`). References are resolved
lazily and the value is redacted like any other secret — putting a raw key in a
profile is refused (use `credentials.toml` for raw keys). Automatic endpoint→profile
routing is intentionally omitted: credential selection is explicit so a call can't
silently pick the wrong account's key.

## Redaction (defense in depth)

`src/media_ai/credentials/redaction.py` masks, across **every** sink (logs, the JSON
result serializer, and error messages):

- every live secret value (known-value masking — the authoritative layer), and
- credential-shaped tokens (`sk-…`, `AIza…`, `Bearer …`) as a backstop.

Sensitive object keys (`authorization`, `*_api_key`, `token`, …) are dropped from
structured output entirely.

## Never do this

- **Never** pass a key as a CLI flag — the CLI has no `--api-key`. argv is world-
  readable via `ps`/`/proc` and is captured in `CalledProcessError`.
- **Never** commit plaintext keys — commit *references* (`op://…`, ARNs) instead.
- **Never** mount a credentials file into a directory an agent can read.

## Brokered / managed mode (config-only upgrade)

Because the broker is just the top-priority resolver, moving to a hosted/managed
deployment is configuration, not a rewrite. Set:

```bash
export MEDIA_CRED_BROKER="https://cred-broker.internal"
export MEDIA_CRED_BROKER_TOKEN="<session-scoped token>"
```

The adapter then sends its request to the broker with headers
`X-Media-Provider`, `X-Media-Upstream`, `X-Media-Session` (see
`HttpProvider._auth`), and the broker injects the real provider key at egress. The
CLI/agent never holds the key. This maps onto an egress credential-injecting proxy
(Envoy `credential_injector`, LiteLLM, Infisical Agent Vault) or a vault-exchange
endpoint. Pair it with default-deny egress + a host allowlist so a prompt-injected
Skill cannot exfiltrate anywhere but the allowed provider hosts.

> The current build ships the broker **client** side (the handle + routing). The
> broker service itself is deployment-specific; the protocol above is stable.
