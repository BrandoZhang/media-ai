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
(`media_ai/providers/_base.py`). Credentials never pass through `media_ai/cli/`.

## Resolution chain (most-secure first, first hit wins)

Configured in `media_ai/credentials/resolver.py`; re-resolved **per invocation**
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
   `MEDIA_CREDENTIALS_FILE`). **Must be `chmod 600`** — a group/world-readable file
   is refused.
   ```toml
   [openai]
   api_key = "sk-…"
   [volc]
   api_key = "…"           # or a reference: api_key = "op://vault/volc/key"
   ```
5. **Environment** — `OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`,
   `ARK_API_KEY`/`VOLC_API_KEY`.

If nothing resolves, the CLI exits **4** (auth) with an actionable message.

## The `Secret` handle

`media_ai/credentials/secret.py`. The plaintext is accessible only via
`.reveal()`. `repr`/`str`/pickle render `***` or a source descriptor, and the
value is **not JSON-serializable** — so it cannot accidentally land in the result
JSON. On creation the value is registered with the redactor.

## Redaction (defense in depth)

`media_ai/credentials/redaction.py` masks, across **every** sink (logs, the JSON
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
