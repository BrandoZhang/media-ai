# Architecture

`media-ai` is a provider- and model-agnostic multimodal generation CLI. A thin CLI
front end turns argv into a **normalized request**, a registry resolves a
**provider adapter**, and the adapter maps the request to one backend's wire format
and returns a **normalized result**. Everything above the adapter is shared; each
backend's idiosyncrasies live only inside its adapter.

## Layered structure

```mermaid
flowchart TB
    subgraph CLI["cli/ — thin front end (argparse + machine contract)"]
        C1["image · video · concat · job · capabilities · usage"]
        C2["legacy shims: text2image, image2video, video_task, …"]
        C3["common.py: run() · emit_result/emit_error · one JSON line + exit code"]
    end

    subgraph CORE["core/ — provider-agnostic (never imports providers/)"]
        R["registry.py — register_provider · build() · model→provider routing"]
        P["provider.py — Provider interface (transport-agnostic)"]
        T["types.py — ImageRequest / VideoRequest / MediaRef / GeometrySpec"]
        CAP["capabilities.py — ModelCapabilities + validate_request()"]
        ERR["errors.py — MediaError · category → exit code"]
        RES["result.py — GenerationResult · Artifact · JobHandle · JobStatus"]
        GEO["geometry.py"]
        USG["usage.py — JSONL cost ledger"]
        LOG["logging.py — stderr, redacted"]
    end

    subgraph CRED["credentials/ — secrets never reach argv/logs/output"]
        RSV["resolver.py — chain: broker→secret-mgr→keychain→file→env"]
        SEC["secret.py — Secret (reveal-only) · BrokeredHandle"]
        RED["redaction.py — redact() over every sink"]
    end

    subgraph PROV["providers/ — adapters (HTTP-specific code lives ONLY here)"]
        BASE["_base.py HttpProvider · _http.py HttpClient (retry/idempotency)"]
        MOCK["mock (offline default)"]
        VOLC["volc (Ark)"]
        OAI["openai (GPT Image)"]
        GEM["gemini (Nano Banana/Veo)"]
        EXT["custom plugins (entry point / register_provider)"]
    end

    MEDIA["media/ — ffmpeg + Pillow (mock render + concat)"]
    EXTAPI["External provider APIs · broker/egress proxy"]

    CLI --> R
    CLI --> CAP
    R --> P
    R -.discovers.-> EXT
    P --> PROV
    PROV --> BASE
    PROV --> CORE
    PROV --> CRED
    BASE --> EXTAPI
    MOCK --> MEDIA
    CLI --> MEDIA
    CORE --> CRED
```

**The dependency rule:** arrows only point down/inward. `core/` never imports
`providers/`; the CLI never imports a concrete provider (it goes through the
registry). This is what keeps the core provider-agnostic — and what a change must
not violate.

## Request lifecycle

```mermaid
sequenceDiagram
    participant Skill as Agent Skill / shell
    participant CLI as cli and common.run
    participant Reg as core.registry
    participant Cap as validate_request
    participant Adp as Provider adapter
    participant Cred as credentials chain
    participant API as backend (HTTP/RPC) or ffmpeg

    Skill->>CLI: media-ai image generate ...
    CLI->>CLI: parse argv → ImageRequest (normalized)
    CLI->>Reg: build(provider?, model?, modality)
    Reg-->>CLI: (adapter, resolved model id)
    CLI->>Cap: validate_request(req, adapter.capabilities(model))
    Note over Cap: unsupported → MediaError(UNSUPPORTED) → exit 3, no network
    CLI->>Adp: generate_image(req)
    Adp->>Cred: credential() (lazy, per call)
    Cred-->>Adp: Secret (reveal-only) or BrokeredHandle
    Adp->>API: build request from req and options, reveal key only here
    API-->>Adp: bytes / job id / base64
    Adp->>Adp: write artifact(s), record usage (redacted)
    Adp-->>CLI: GenerationResult, or JobHandle if async
    CLI-->>Skill: one JSON line on stdout, exit 0
```

Failures anywhere become a categorized `MediaError`; `common.run()` prints
`{"ok": false, "error": {…}}` and returns the category's exit code. Async video
(`--wait false`) returns a `JobHandle`; `media-ai job query --output` later polls
and finalizes (downloads) via the same adapter.

## Module responsibilities

| Area | What it owns |
|---|---|
| `core/types.py` | Normalized `ImageRequest`/`VideoRequest`, `MediaRef` (any input source), `GeometrySpec` |
| `core/capabilities.py` | Per-model `ModelCapabilities` schema + `validate_request()` — drives discovery **and** gating |
| `core/registry.py` | Dynamic provider registry, `register_provider()`, entry-point discovery, model→provider routing |
| `core/provider.py` | The `Provider` interface (no transport assumptions) |
| `core/{errors,result}.py` | Error taxonomy→exit codes; result/job types + the stdout JSON contract (`schema_version`) |
| `core/{geometry,usage,logging}.py` | Geometry primitives; JSONL cost ledger; redacted stderr logging |
| `credentials/` | Resolver chain, reveal-only `Secret`/`BrokeredHandle`, `redact()` |
| `providers/_base.py`,`_http.py` | Optional HTTP layer: auth/broker header building, retry + idempotency |
| `providers/{mock,volc,openai,gemini}.py` | One adapter each: wire mapping, capability declarations, error mapping |
| `media/` | ffmpeg (clip/concat) + Pillow (placeholder render) — mock backend + `concat` |
| `cli/` | argparse front ends; `common.py` owns the machine contract |

## Cross-cutting concerns

- **Capability gating** — the same `ModelCapabilities` powers `media-ai
  capabilities` (discovery) and `validate_request` (pre-flight). Provider-specific
  functions ride on `--option key=value`, allowed only if the model declares them.
- **Credential trust boundary** — the CLI holds no secret; the adapter resolves one
  lazily and reveals it only at request-build time. In brokered mode
  (`MEDIA_CRED_BROKER`) the adapter holds only a session token and the broker
  injects the key at egress. Every sink is redacted.
- **Machine contract** — one JSON object on stdout (success or failure), redacted
  logs on stderr, category-specific exit codes. Built once in `cli/common.run()`.
- **Async jobs** — a `JobHandle`/`JobStatus` abstraction over per-provider ids
  (Volc task-id, Gemini `operations/…`), with a finalize/download step and
  signal-driven cancel for blocking Volc polls. (OpenAI is image-only/synchronous.)

## Extension points

- **Custom provider** — subclass `Provider` (or `HttpProvider`), declare
  `ModelCapabilities`, register via `register_provider()` or a `media_ai.providers`
  entry point. Non-HTTP (gRPC/RPC/SDK) providers subclass `Provider` directly and
  use `media_ai.retry()`. See [EXTENDING.md](EXTENDING.md).
- **New credential source** — add a resolver to the chain in
  `credentials/resolver.py` / `stores.py`.
- **New modality/operation** (audio, upscaling, …) — the one change that *does*
  touch core: add an `Operation` enum value + a CLI command group; the
  capability/validation/registry machinery then extends to it unchanged.
