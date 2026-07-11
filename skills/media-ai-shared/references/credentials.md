# media-ai credentials — secret-safe setup

**The CLI never accepts a key as a flag.** There is no `--api-key`. Keys are
resolved lazily at HTTP-call time, held as a reveal-only `Secret`, and redacted
from stdout, stderr, and `--metadata-out`. Set them in the environment (or a
broker / keychain / secret-manager).

> Rule of thumb: if you don't want the agent to leak a secret, don't put it in the
> agent's context or argv — put it in the environment and let the CLI resolve it.

## Environment variables per provider

| provider | env var(s) (first non-empty wins) |
|---|---|
| `volc` | `ARK_API_KEY`, `VOLC_API_KEY` |
| `openai` | `OPENAI_API_KEY` (+ optional `OPENAI_ORG`, `OPENAI_PROJECT`) |
| `gemini` | `GEMINI_API_KEY`, `GOOGLE_API_KEY` |
| `mock` | none (offline default) |
| any other provider | `<PROVIDER>_API_KEY` (e.g. a custom `acme` ⇒ `ACME_API_KEY`) |

```bash
export OPENAI_API_KEY=sk-...        # then: media-ai image generate --provider openai ...
export GEMINI_API_KEY=...           # or GOOGLE_API_KEY
export ARK_API_KEY=...              # volc / Volcengine Ark
```

A missing/invalid key surfaces as **exit 4** (`auth`) — fix the environment, not the request.

## Resolution chain (most-secure first, first hit wins, re-resolved per call)

1. **Broker** — `$MEDIA_CRED_BROKER` (+ `$MEDIA_CRED_BROKER_TOKEN`): the CLI holds
   only a session token; the broker injects the real key at egress. Ideal for hosted
   agents (the key never reaches the sandbox).
2. **Secret-manager reference** — `$MEDIA_SECRET_REF_<PROVIDER>` (e.g.
   `MEDIA_SECRET_REF_OPENAI`) or a config value prefixed `op://` / `vault://` /
   `gcp-sm://` / `aws-sm://` / `arn:aws:secretsmanager:` (only `env://VAR` is built in;
   others need `register_secret_backend`).
3. **OS keychain** — with the `keychain` extra installed (`pip install -e ".[keychain]"`),
   service `media-ai`, username = provider name. Disable with `$MEDIA_DISABLE_KEYCHAIN`.
4. **Config file** — `~/.config/media-ai/credentials.toml` (override `$MEDIA_CREDENTIALS_FILE`);
   must be `chmod 600` or it is refused. Reads `[<provider>].api_key` or `.key`.
5. **Environment** — the table above.

## Profiles (multiple accounts / endpoints / tenants)

A profile binds `provider` + default `model` + optional `base_url` + a **credential
reference** (never a raw key), in `~/.config/media-ai/config.toml` (override
`$MEDIA_CONFIG_FILE`). Select with `--provider-profile <name>` or `$MEDIA_PROFILE`.

```toml
# ~/.config/media-ai/config.toml
[profiles.prod-openai]
provider = "openai"
model = "gpt-image-2"
base_url = "https://api.openai.com/v1"
credential = "env://OPENAI_API_KEY_PROD"    # a reference; a raw key here is refused (exit 4)
```

```bash
media-ai image generate --provider-profile prod-openai --prompt "..." --output o.png
```

Deeper detail (trust boundary, redaction, broker headers): `../../../docs/CREDENTIALS.md`.
