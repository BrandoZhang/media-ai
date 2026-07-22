# Adding a custom provider

A provider is a self-contained adapter: it declares a **capability schema** per
model and implements the operations it supports. Adding one requires **no changes
to core** — register it in-process or ship it as a package. The CLI, capability
discovery, validation, structured errors, credentials, redaction, and the usage
ledger all work for a custom provider automatically.

## 1. Write the adapter

Subclass `Provider` (or `HttpProvider` for HTTP backends — it gives you the shared
retry/idempotency client and credential/broker routing). Declare a
`ModelCapabilities` for each model; that *is* your schema, and it drives both
`media-ai capabilities` and pre-flight validation.

```python
from media_ai import (
    HttpProvider, ModelCapabilities, ImageCaps, Operation, Modality,
    GenerationResult, Artifact,
)

class AcmeProvider(HttpProvider):
    name = "acme"
    auth_scheme = "bearer"                 # Authorization: Bearer <key>
    model_hints = ("acme-",)               # routes `--model acme-fast` here

    def __init__(self, *, credentials=None, config=None):
        super().__init__(credentials=credentials, config=config)
        self.base_url = "https://api.acme.example/v1"

    def models(self):
        return ["acme-fast", "acme-pro"]

    def default_model(self, modality):
        return "acme-pro"

    def capabilities(self, model=None):
        return ModelCapabilities(
            provider=self.name, model=model or "acme-pro",
            modalities=frozenset({Modality.IMAGE}),
            image=ImageCaps(
                operations=frozenset({Operation.IMAGE_GENERATE}),
                max_count=4, supports_seed=True,
                options=("sticker",),          # <-- a provider-specific function
            ),
            notes=("Acme image API",),
        )

    def generate_image(self, req):
        client, headers = self._prepare()     # credential revealed here, never logged
        body = {"model": req.model, "prompt": req.prompt, "n": req.count}
        if req.seed is not None:
            body["seed"] = req.seed
        if "sticker" in req.options:           # gated: only valid because caps list it
            body["sticker_pack"] = req.options["sticker"]
        data = client.request_json("POST", "/images", body=body, headers=headers)
        out = req.output
        out.parent.mkdir(parents=True, exist_ok=True)
        import base64
        out.write_bytes(base64.b64decode(data["images"][0]["b64"]))
        return GenerationResult(modality="image", operation=req.operation.value,
                                provider=self.name, model=req.model,
                                artifacts=[Artifact.from_path(out, "image", mime="image/png")],
                                usage=data.get("usage", {}), meta={"prompt": req.prompt})
```

**Provider-specific functions** ride on `options` (`--option key=value`), gated by
the model's `ImageCaps.options` / `VideoCaps.options`. If a caller passes an option
your model doesn't declare, the CLI rejects it with exit 3 before any network call.
Cross-provider concepts (`--seed`, `--negative-prompt`, `--duration`, …) are
first-class fields — read them off the request.

**Credentials** come from the same chain as built-ins. `self._prepare()` resolves
`acme`'s key (env `ACME_API_KEY`, keychain, secret-manager, broker, …) and returns
an HTTP client + auth headers; you never see or log the raw key. Add your env var
mapping in `src/media_ai/credentials/stores.py::ENV_VARS` (or just rely on the default
`ACME_API_KEY`).

## 2. Register it

**In-process** (embedding, tests, a private codebase):

```python
from media_ai import register_provider
register_provider("acme", lambda **kw: AcmeProvider(**kw), model_hints=("acme-",))
```

**As an installed package** — expose an entry point; no import needed by the host:

```toml
# the plugin package's pyproject.toml
[project.entry-points."media_ai.providers"]
acme = "acme_media:AcmeProvider"
```

The entry-point *name* is the provider name; the class's `model_hints` route bare
model ids. Entry points are discovered lazily; a broken plugin is skipped (logged),
never fatal.

## 3. Use it

```bash
media-ai capabilities --provider acme
media-ai image generate --provider acme --prompt "a fox" --output fox.png --option sticker=holiday
media-ai image generate --model acme-fast --prompt "a fox" --output fox.png   # provider inferred
```

## Non-HTTP / RPC providers

`HttpProvider` is only a convenience for REST backends. The `Provider` interface
itself has **no transport assumptions** — subclass it directly for a gRPC stub, a
JSON-RPC endpoint, a vendor Python SDK, a local subprocess, or a message queue.
You keep everything else (registry, CLI, capability discovery + validation,
credentials resolution, redaction, usage ledger, result contract) and just supply
the calls.

```python
from media_ai import (
    Provider, ModelCapabilities, ImageCaps, Operation, Modality,
    GenerationResult, Artifact, MediaError, ErrorCategory, retry,
)
import acme_rpc   # your gRPC / SDK / RPC client library

class AcmeRpcProvider(Provider):
    name = "acme"
    model_hints = ("acme-",)

    def __init__(self, *, credentials=None, config=None):
        super().__init__(credentials=credentials, config=config)
        self._stub = acme_rpc.Client(endpoint=(config or {}).get("endpoint", "localhost:50051"))

    def models(self): return ["acme-pro"]
    def default_model(self, modality): return "acme-pro"
    def capabilities(self, model=None):
        return ModelCapabilities(provider=self.name, model=model or "acme-pro",
            modalities=frozenset({Modality.IMAGE}),
            image=ImageCaps(operations=frozenset({Operation.IMAGE_GENERATE}), supports_seed=True))

    def generate_image(self, req):
        key = self.credential().reveal()          # resolved from env/keychain/secret-manager/broker
        def call():
            return self._stub.Generate(            # your RPC — inject the key however your transport wants
                acme_rpc.Req(prompt=req.prompt, seed=req.seed or 0, api_key=key))
        try:
            # idempotency-aware retry for a read-only/deterministic RPC:
            resp = retry(call, retryable=lambda e: isinstance(e, acme_rpc.Transient))
        except acme_rpc.Unauthorized as e:
            raise MediaError(str(e), category=ErrorCategory.AUTH, provider=self.name) from e
        except acme_rpc.RpcError as e:             # map transport errors -> the shared taxonomy
            raise MediaError(str(e), category=ErrorCategory.PROVIDER, provider=self.name) from e
        req.output.parent.mkdir(parents=True, exist_ok=True)
        req.output.write_bytes(resp.image_bytes)
        return GenerationResult(modality="image", operation=req.operation.value, provider=self.name,
                                model=req.model, artifacts=[Artifact.from_path(req.output, "image")], usage={})
```

What an RPC provider is responsible for (that `HttpProvider` would otherwise give
you for free):

- **Credential injection.** Call `self.credential().reveal()` and pass the value
  into your transport (gRPC metadata, an SDK client, RPC field). The value is still
  redacted from all logs/output. For **brokered mode** (`MEDIA_CRED_BROKER` set),
  `credential()` returns a `BrokeredHandle` with no local secret — an RPC provider
  performs the vault-exchange itself (call the broker for a short-lived token, then
  use it in the RPC); the HTTP proxy-injection path doesn't apply to non-HTTP.
- **Error mapping.** Translate your transport's failures into `MediaError` with the
  right `ErrorCategory` (auth/rate_limit/timeout/safety/…) so exit codes stay
  deterministic.
- **Retry** (optional). Use `media_ai.retry(fn, retryable=…)` for
  idempotency-aware exponential backoff, or your transport's own policy.

Everything above the adapter — the one-JSON-line contract, capability gating,
`--option` functions, the usage ledger — is identical to an HTTP provider.

## What's pluggable vs not

- **Pluggable without touching core:** new providers, new models, per-model
  capability schemas, provider-specific options/functions, model→provider routing,
  and (via `_prepare`) credential/broker handling.
- **Needs a small core change:** a brand-new *modality* or *operation* (e.g. audio
  generation, upscaling) — add an `Operation` enum value and a CLI command group.
  The provider/capability/validation machinery then extends to it unchanged.
