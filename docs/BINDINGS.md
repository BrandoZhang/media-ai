# Bindings

A **binding** is one callable `(provider, model)` pair. It is what you configure, what
you address, and what declares its own capabilities.

**This page lists no models.** Model lineups change faster than documentation, and a
stale list read confidently is worse than none. The manifests in
`src/media_ai/bindings/*.toml` are the source of truth, and three commands read them:

```bash
media-ai bindings list        # what this machine can call right now
media-ai bindings available   # what could be added, and the command to add it
media-ai capabilities --binding <id> --pretty
```

## Configuring one

```bash
media-ai bindings add volc-ark/seedance-2.0 --credential env://ARK_API_KEY \
  --endpoint-id ep-xxx-xxx --base-url https://ark.cn-beijing.volces.com/api/v3
media-ai config set-default video volc-ark/seedance-2.0
```

or, guided: `media-ai init`. Either writes `~/.config/media-ai/config.toml`:

```toml
schema = 2

[bindings."volc-ark/seedance-2.0"]
credential = "env://ARK_API_KEY"

[defaults]
"video.text_to_video" = "volc-ark/seedance-2.0"
```

Each binding is **self-contained**: its own endpoint, its own credential reference,
its own options. Two bindings on one account repeat the reference. That is deliberate
— the question this file must answer fastest is "what is *this* binding doing?", and
an indirection saving a line of typing costs a hop every time something breaks.

## `extends` — the one indirection

```toml
# a second account or region
[bindings."volc-ark-sg/seedance-2.0"]
extends    = "volc-ark/seedance-2.0"
base_url   = "https://ark.ap-southeast.volces.com/api/v3"
credential = "cred://volc-ark-sg"

# an opaque deployment id, whose capabilities are the real model's
[bindings."volc-ark/my-endpoint"]
extends    = "volc-ark/seedream-4.5"
endpoint_id = "ep-example-endpoint"        # sent as Ark's `model` field
credential = "env://ARK_API_KEY"
```

One mechanism covers multi-account, multi-region and deployment ids, because all three
are "the same declared capabilities, reached differently". The wire keeps the id the
API accepts; everything the CLI knows about the backing model applies.

## Per-binding options

Knobs that belong to *this integration* rather than to a call:

```toml
[bindings."volc-ark/seedance-2.0".options]
poll_interval = 5
poll_timeout  = 900
```

These used to be environment variables, which made them global to the process — two
endpoints could not want different deadlines, and no file recorded which value
applied. Adapters read no environment at all now.

Recognised keys are adapter-specific; unknown ones are ignored, so a newer config
stays readable by an older CLI. Common ones: `poll_interval`, `poll_timeout`,
`http_timeout`; Gemini `inline_max_bytes`; OpenAI `org`, `project`; ElevenLabs
`voice`.

## Addressing at call time

| | |
|---|---|
| `--binding <provider>/<model>` | exact, always unambiguous |
| `--provider P --model M` | the same in two parts |
| `--model M` | only when one configured binding serves M |
| *(nothing)* | the `[defaults]` entry for the derived scene |

`--model` also matches the wire id (`doubao-seedream-4-5-251128`), since that is the
name a vendor's own documentation uses.

## Local backends

`local/ffmpeg` (clip joining and animated-image export) and `mock/mock` (offline
placeholders) declare `auth.kind = "none"`, so they are always available and never
appear in a credential prompt. `mock` is an ordinary binding you must ask for — it is
not what an unconfigured machine falls back to.

Needing no credential is not the same as needing no *configuration*: a call that names
no binding still resolves through the scene default, so `media-ai init` writes one for
these too. It used to write them only for bindings it had asked a key for, which left
`video concat` refusing on a fresh install with `no_default_binding` — naming, in the
hint, the free binding sitting right there.

## Adding a model

See [EXTENDING.md](EXTENDING.md). Short version: a sibling model on an existing
provider is usually one manifest entry and no code.
