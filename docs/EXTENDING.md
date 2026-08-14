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

## Provisioning a machine without the wizard

An internal distribution usually does not want `media-ai init`: which bindings a
machine gets and which key each one uses are decided centrally, and the person at the
keyboard has nothing to contribute to either. The shape that works is **authenticate,
fetch, write the two files** — after which nothing else in the CLI knows or cares that
it was provisioned.

Only the last step is this project's business. It is two calls:

```python
from media_ai.core.config import Config, UserBinding, save_config
from media_ai.credentials.stores import save_accounts

payload = fetch_my_org_config(authenticate())        # entirely yours

save_accounts({"acme/fast": payload["key"]}, replace=True)
save_config(Config(
    bindings={"acme/fast": UserBinding(id="acme/fast",
                                       base_url=payload["base_url"],
                                       credential="cred://acme/fast")},
    defaults={"image.text_to_image": "acme/fast"},
    exists=True,
))
```

Reach for those rather than writing the TOML yourself. `save_accounts` carries four
rules that are invisible from the file format and silent when got wrong: the schema is
checked *before* a merge and stamped *after* it (merging into a file this build reads
wrongly rewrites it in the older shape and takes the keys with it), the file lands 0600
inside a 0700 directory or the resolver refuses to read it at all, an identical re-run
leaves no second backup — a copy of every key, under a name nobody will remember to
delete — and a file whose mode has drifted stays repairable by rewriting.

`replace=True` makes the given accounts the whole file. That is usually what
role-based provisioning wants: an entitlement being *withdrawn* has to remove the
account it granted, and a merge can only ever add or overwrite. It is not the default,
because for the wizard and for `bindings add` it would discard keys nobody asked about.

Neither writer imports `media_ai.cli`, so a provisioner is a small script rather than
a CLI command — which is the right shape anyway, since the authentication step is
yours and does not belong in this project's argument parser.

### For services, prefer keeping the key out of the file

A container does not need `credentials.toml` at all. Provision (or bake in) a
`config.toml` whose bindings use `env://`, and let the orchestrator inject the value:

```toml
[bindings."acme/fast"]
base_url   = "https://gateway.internal/v1"
credential = "env://ACME_GATEWAY_KEY"
```

Nothing secret is on disk, the config can go into the image, and revocation is the
orchestrator's job rather than a file's. One trap if you do mount a file instead:
Kubernetes `Secret` volumes default to mode 0644, which the resolver refuses — set
`defaultMode: 0600`.

### Recording that you wrote it

Only worth doing if you intend to **merge**. A wholesale replace needs no bookkeeping
and gets withdrawal right for free; a merge has to know which entries are yours, or it
can only ever add and overwrite.

If you do, `config.toml` models a `[managed]` table for exactly this:

```toml
[managed]
source   = "https://config.internal/media-ai/team-vision"
revision = "2026-08-14T02:00:00Z"
bindings = ["acme/fast"]
defaults = ["image.text_to_image"]
```

`source` is required — an ownership claim with no owner cannot be acted on. `revision`
is opaque; nothing here compares two of them. The listed names are not checked against
the catalog, because this records what was written rather than declaring it again.
`config show` reports the table, and every command that rewrites the file preserves it.

Nothing in this project writes `[managed]`, and nothing reads it to decide anything —
it is a place for a provisioner to keep its own answer. If your semantics differ, any
table this build does not model is preserved verbatim too, so `[acme]` with whatever
shape you want works just as well.

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
