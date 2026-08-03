# Credentials

The design goal, borrowed from Anthropic's managed-agents guidance: **"if you don't
want your AI agent to reveal a secret, don't give it the secret."** A raw provider key
must never reach an Agent Skill's context, the CLI's argv, logs, generated metadata,
or any model-visible surface.

## Trust boundaries

| Component | Trust | Sees the raw key? |
|---|---|---|
| Agent Skill (decides to run the CLI) | Untrusted (prompt-injectable) | **Never** — names a binding |
| CLI process | Semi-trusted | Only in memory, at call time, in single-process mode |
| Adapters | Same process | Receive a reveal-only handle, never a logged or serialized string |
| Secret stores / broker | Trusted | Where raw secrets live |

## One binding, one named source

Each binding says where its key comes from, and there is no fallback:

```toml
# ~/.config/media-ai/config.toml — non-secret, safe to share
[bindings."volc-ark/seedance-2.0"]
credential = "env://ARK_API_KEY"
```

| scheme | resolves from |
|---|---|
| `env://VAR` | an environment variable |
| `cred://<account>` | a `[<account>]` block in `credentials.toml` (chmod 600) |
| `keychain://<service>/<account>` | the OS keychain (optional `keychain` extra) |
| `broker://<host>` | a credential broker — this process holds only a session token |
| `op://…`, `vault://…`, `aws-sm://…` | a registered backend (`register_secret_backend`) |

**This replaced a five-layer precedence chain** (broker → secret manager → keychain →
config file → env, first hit wins). The chain was convenient and answered the wrong
question: "where did this key come from?" required knowing the order and checking five
places, and a key present in two of them resolved to one silently. Every capability
survives as a scheme; what is gone is the implicit precedence between them.

A reference is not a secret, so it lives in the shareable `config.toml`. **A raw key
there is refused** — the file is meant to be shared.

## The two files

| File | Holds | Committed? |
|---|---|---|
| `~/.config/media-ai/config.toml` | bindings, scene defaults, credential **references** | safe to share |
| `~/.config/media-ai/credentials.toml` | **accounts** — raw keys or references, `chmod 600` | never |

`credentials.toml` is a flat namespace of accounts. The wizard names one after the
binding that uses it, so "which key did this binding use?" has a one-line answer:

```toml
["volc-ark/seedance-2.0"]
api_key = "..."

[shared-ark]                        # or share deliberately, by pointing two
api_key = "op://vault/volc/key"     # bindings at the same account
```

A group- or world-readable `credentials.toml` is **refused**, not silently trusted.
Choosing `env://` at setup means **no secret file is written at all**.

## Moving a setup to another machine

`media-ai init` is a conversation, and a production instance cannot have one. A
**bundle** is that conversation's outcome as a file:

```bash
# on a machine that is already configured
media-ai config export --output setup.toml                          # references only, 0644
media-ai config export --output setup.toml --include-credentials    # + the accounts, 0600

# on the target — no wizard, no terminal needed
media-ai config import --input setup.toml
curl -fsSL https://internal/setup.toml | media-ai config import --input -
```

The rules are the same trust boundary, written down for a file that travels:

| Rule | Why |
|---|---|
| **Export never resolves a credential** | A reference stays a reference; `env://ARK_API_KEY` is not materialised into a literal. Otherwise the target's answer to "where does this key come from?" would differ from the source's, silently. A bundle is a *move*, not a transformation. |
| **It carries the accounts its bindings name, and no others** — `cred://` chains followed | A key that travels for no reason is a key in one more place. Accounts left behind are reported as `omitted_credentials`, so nothing is dropped silently. |
| **`--include-credentials` is the only way a key leaves** | Without it the accounts file is not even read, and the bundle is as shareable as `config.toml`. Accounts an exported binding names but the bundle does not carry are reported as `missing_credentials` — the target must supply those itself. |
| **A secret-bearing bundle is written 0600** | Same standard as `credentials.toml`. Move it over a private channel and delete it once imported. |
| **The result JSON carries account *names*, never values** | stdout is read by an agent, printed in CI logs, and pasted into issues. |

An import is refused — before anything is written — if the bundle names a binding this
build does not declare, or defaults to one this machine cannot reach; both would leave
a config in which *every* later command fails, including the one an operator would run
to find out why. `--skip-unknown` drops those entries instead, and says which went.
`--dry-run` reports the whole plan and writes nothing.

A bundle is also the one document in the project that **migrates instead of refusing**.
It exists to be read on another machine, which in a fleet means an older or newer
release; `core/migrate.py` upgrades an older envelope or config payload step by step,
and refuses a newer one with "upgrade media-ai" rather than guessing. Files on disk
still do not migrate — an install is a fresh start.

Provisioning in one command:

```bash
curl -fsSL https://raw.githubusercontent.com/BrandoZhang/media-ai/main/install/install.sh \
  | bash -s -- --config-bundle https://internal/setup.toml --skills-dest ~/.claude/skills
```

## When resolution fails

Exit 4, with a code that distinguishes the causes:

| code | meaning |
|---|---|
| `credential_unresolved` | the binding is configured; its named source is empty |
| `binding_not_configured` | declared, but this machine has no entry — `hint` is the `bindings add` command |
| `credential_is_raw_key` | a key was written where a reference belongs |
| `credential_scheme_unknown` | no backend registered for that scheme |
| `credentials_file_permissions` | `chmod 600` it |

Nothing is tried elsewhere. Setting a different key will not rescue the call, which is
the point.

## The `Secret` handle

`credentials/secret.py`. Plaintext is reachable only via `.reveal()`; `repr`/`str`/
pickle render `***`, and the value is **not JSON-serializable**, so it cannot land in
the result by accident. On creation it is registered with the redactor.

## Redaction

`credentials/redaction.py` masks, across **every** sink (logs, the JSON serializer,
error messages): every live secret value, plus credential-shaped tokens (`sk-…`,
`AIza…`, `Bearer …`) as a backstop. Sensitive object keys (`authorization`,
`*_api_key`, `token`, …) are dropped from structured output entirely.

## Never do this

- **Never** pass a key as a flag — there is no `--api-key`; argv is world-readable via
  `ps`/`/proc` and is captured in `CalledProcessError`.
- **Never** commit plaintext keys — commit *references* instead.
- **Never** mount a credentials file into a directory an agent can read.

## Brokered / managed mode

Because the broker is just another scheme, moving to a hosted deployment is
configuration, not a rewrite:

```toml
credential = "broker://cred-broker.internal"
```

The adapter sends its request to the broker with `X-Media-Provider`,
`X-Media-Upstream` and `X-Media-Session` headers, and the broker injects the real key
at egress. The CLI never holds it. This maps onto an egress credential-injecting proxy
(Envoy `credential_injector`, LiteLLM, Infisical Agent Vault). Pair it with
default-deny egress and a host allowlist so a prompt-injected Skill cannot exfiltrate
anywhere but the allowed hosts.

> This build ships the broker **client** side. The service is deployment-specific; the
> protocol above is stable.
