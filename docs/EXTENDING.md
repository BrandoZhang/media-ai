# Adding a model, or a whole backend

A backend is two things: a **manifest** declaring what it can do, and an **adapter**
implementing how to call it. Neither requires editing anything under `core/`.

## The rule for adding a model

Every new model arrives with four answers, because they are the manifest's required
fields:

1. **which provider** it is reached through (`provider`)
2. **which model**, and the id that goes on the wire (`model`, `model_id`)
3. **what it supports** — scenes and constraints
4. **how to authenticate** — `[provider.auth]` and `[provider.base_url]`, or
   `transport = "rpc"` for something that is not HTTP

`tests/test_manifests.py` enforces all four, plus that the named adapter imports and
implements every scene declared. What used to be a paragraph in a contributing guide
is a set of assertions.

## A sibling model on an existing provider

Usually **one manifest entry and no code**, because the wire is already implemented:

```toml
[[binding]]
id       = "volc-ark/seedream-5.0"
model    = "seedream-5.0"
model_id = "doubao-seedream-5-0-260128"
title    = "Seedream 5.0"
scenes   = ["image.text_to_image", "image.image_to_image"]

[binding.constraints]
options = ["watermark"]

[binding.constraints.supports]
seed = true

[binding.constraints.geometry]
mode        = "both"
named_sizes = ["2K", "3K", "4K"]

[binding.constraints.output]
formats          = ["png", "jpeg"]
max_count        = 15
max_total_images = 15
```

The exception is a genuinely new **wire field**. Seedream 5.0 pro's
`optimize_prompt_options.mode` is one: declare it in `constraints.options` and pass it
through in the adapter — one manifest entry plus one line.

Two rules for the data itself:

- **`placeholder = true` only if the binding fabricates output.** It removes the binding
  from every *recommendation* — a hint after a refusal, a suggested scene default — while
  leaving it fully callable by name. Only the offline mock carries it, and a real backend
  that did would quietly stop being offered anywhere.
- **`verified` is a date or absent.** It records a run against the live API. Filling it
  in to make a table look complete turns "unknown" into "confirmed", which is the one
  thing that field must never do.
- **An absent limit is an absent field**, never `0` or `-1`. A sentinel does not make a
  careless comparison fail — it makes it quietly do the opposite.

## A new provider

Add a manifest and an `Adapter`:

```python
from media_ai import Adapter, Artifact, GenerationResult, Scene, derive_scene

class AcmeAdapter(Adapter):
    def supported_scenes(self):
        return frozenset({Scene.IMAGE_TEXT_TO_IMAGE})

    def generate_image(self, req):
        client, headers = self._prepare()          # HttpAdapter only
        ...
        self.record(derive_scene(req), kind="image", total_tokens=0)   # usage ledger
        return GenerationResult(modality="image", provider=self.name, model=req.model,
                                artifacts=[Artifact.from_path(req.output, "image")],
                                usage={}, meta={})
```

The adapter is constructed from a `ResolvedBinding` and nothing else:

| you want | you read |
|---|---|
| the id for the wire | `self.model_id` |
| the endpoint | `self.base_url` |
| declared limits | `self.constraints` |
| a per-binding knob | `self.option("poll_interval", 5)` |
| the credential | `self.credential()` — resolved per call, revealed only in the request builder |
| the usage ledger | `self.record(scene, kind=…, total_tokens=…)` — binding/provider/model filled in for you |

**Do not read the environment.** A binding's behaviour must be fully described by the
config entry that names it. Subclass `HttpAdapter` for REST (auth headers come from
the manifest) or `Adapter` directly for anything else.

Map the transport's failures onto `MediaError` categories so exit codes stay
meaningful, and use `media_ai.retry()` for non-HTTP retry.

## Shipping it

```toml
# pyproject.toml of your package
[project.entry-points."media_ai.bindings"]
acme = "acme_media:MANIFEST"
```

The entry point resolves to manifest text, a path, or a callable returning either.
The manifest's `adapter` field is an import path, so the code can live anywhere — a
broken plugin is logged and skipped rather than breaking the CLI.

For tests and embedding: `media_ai.register_manifest(TOML_TEXT)`.

## Non-HTTP backends

`transport = "rpc"` means the framework assumes nothing — no base URL, no HTTP client,
no status-code mapping:

```toml
[provider]
name      = "internal-platform"
transport = "rpc"
adapter   = "your_pkg.media_ai_adapter:PlatformAdapter"

[provider.auth]
kind = "custom"          # the adapter interprets the credential itself
env  = ["PLATFORM_TOKEN"]
```

Everything else still applies: scene checks, pre-flight validation, credential
injection, retry, the result shape and the exit-code taxonomy. This is the case a
declarative-only design could never serve, and the reason wire mapping stayed in code.
See `tests/test_extensions.py::test_an_rpc_backend_is_first_class`.

## Testing

Adapters are exercised offline against a `FakeClient` that records request bodies and
returns canned responses. `tests/conftest.py` builds one from a binding id:

```python
def test_it(fake_provider, tmp_path):
    adapter, fake = fake_provider("acme/acme-pro", [{"data": [...]}])
    adapter.generate_image(...)
    assert fake.calls[0]["body"]["model"] == "acme-pro-2026"
```

New bindings are automatically covered by `tests/test_contract.py`, which checks every
declared scene accepts a minimal request and every undeclared option is refused.
