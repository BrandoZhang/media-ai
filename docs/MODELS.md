# The model catalogue

Every model each built-in provider knows about lives in one file:
[`src/media_ai/providers/_catalog.py`](../src/media_ai/providers/_catalog.py).

Adapters used to classify a model id with string tests scattered through their
capability methods — `"tts" in m`, `m.startswith("veo")`, `"lite" in m`. That worked
for the ids in front of the person writing it, and failed quietly for everything else:
an id matching no pattern inherited whichever tier the if-chain ended on. There was
also nowhere to say a model had been retired, or when anyone last checked one against
the live API. Those are questions about data, so they are answered by data.

## What a spec records

```python
ModelSpec(
    id="gemini-2.5-flash-image",
    status=ModelStatus.DEPRECATED,
    replacement="gemini-3.1-flash-image",
    reason="superseded by Nano Banana 2",
    verified="2026-07-11",               # last exercised against the real API
    discoverable=True,
    notes=("Nano Banana (2.5, legacy): imageSize fixed at 1K, up to 3 refs",),
    caps={...},                          # the parameters that vary between models
)
```

`caps` holds only what **differs between models of the same provider** — sizes,
durations, option names. Everything shared stays in the adapter, which assembles the
`ModelCapabilities`. The shape is provider-specific; the data is not.

## Lifecycle

| status | `models()` lists it | `capabilities()` describes it | callable |
|---|---|---|---|
| `ga` | yes | yes | yes |
| `preview` | yes | yes (`experimental: true`) | yes |
| `deprecated` | usually not (`discoverable=False`) | **yes** | yes |
| `removed` | no | no — raises, naming the replacement | no |

Deprecated deliberately still describes itself: planning a migration off a model means
inspecting it. Removed refuses, in both `capabilities()` and the generate path, so
discovery and execution can never disagree about whether a model exists.

Retiring a model is an edit here, not a new branch in a capability method — and a test
enforces that anything `deprecated` or `removed` names a `replacement`, so a retirement
can't leave callers without somewhere to go.

## Verification (`verified`)

Dates come from [LIVE_TESTS.md](LIVE_TESTS.md) and name only models that log records
being called against the real API. Nine are verified; the rest are `None`, reported as
"not verified against the live API" in `capabilities` output rather than quietly
defaulted to something reassuring.

What that immediately exposes:

- **`veo-3.1-generate-preview` — the default video model — has never been live-tested**,
  while its `lite` and `fast` variants have. The default is the least-exercised path.
- Volcengine Ark has **no** verified model at all (no key was available for that run).
- Of ElevenLabs' four TTS models only `eleven_multilingual_v2` was exercised, and the
  music/sound models not at all.

`media-ai capabilities --all-models` shows `verified` per model; the catalogue exposes
`unverified_ids()` for the same question in code.

To record a new one:

1. Exercise the model through [`tests/test_live.py`](../tests/test_live.py)
   (`MEDIA_LIVE_TESTS=1` plus that provider's key — these cost real money).
2. Set `verified="YYYY-MM-DD"` on its spec.

Do not fill these in from documentation, changelogs, or inference. A date here is a
claim that someone called the real API and it behaved as catalogued; anything else
makes the field worse than useless, because it converts "unknown" into "confirmed".

## Inspecting it

```bash
media-ai capabilities --provider gemini                 # what you should use
media-ai capabilities --provider gemini --all-models     # + deprecated and removed
```

Each model carries `status`, `replacement`, and `verified`. `--all-models` includes
retired entries; a removed one is reported as the retired entry it is rather than
dropped, because knowing a model is gone — and what replaced it — is the reason to ask.

## Unknown ids

An id nothing claims resolves through an explicitly-declared fallback spec marked
`synthetic=True`, not by falling off the end of an if-chain. Synthetic specs are kept
out of every listing: they answer "what happens to an id I don't recognise", which is a
resolution rule, not a model anyone can call.

Volcengine Ark has **no** fallback, on purpose. An Ark id is usually a custom endpoint
(`ep-…`) naming a *deployment* rather than a model, and guessing what it serves is
exactly the mistake [`Provider.backing_model`](../src/media_ai/core/provider.py) exists
to avoid — map it in `[providers.volc.endpoints]` instead (see
[CREDENTIALS.md](CREDENTIALS.md)).

## Adding a model

1. Add a `ModelSpec` to the right catalogue in `_catalog.py`.
2. Put anything that differs from its siblings in `caps`; the adapter reads it.
3. Leave `verified=None` until it has actually been exercised live.
4. `uv run pytest -q` — the contract tests parametrize over every discoverable model,
   and `tests/test_modelspec.py` checks the catalogue's own invariants.

No adapter change is needed unless the model needs a capability field that does not
exist yet.
