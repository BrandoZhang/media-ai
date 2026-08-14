# Observability

Three signals, one switch, and one rule that outranks all of them.

| Signal | Where it goes | Who reads it |
| --- | --- | --- |
| **Structured logs** | stderr, text or JSON lines | a human at a terminal; a log shipper |
| **Traces** | an OTLP collector, or stderr | whoever is asking *why was this call slow* |
| **Metrics** | an OTLP collector, or stderr | whoever is asking *how often, how much, how expensive* |
| **Key events** | all three at once | both — an event is a log line, a span event and a counter |

The rule that outranks them: **stdout stays exactly one JSON object.** Every exporter,
every formatter and every diagnostic in this design writes to stderr or to a socket,
never to fd 1. A telemetry feature that corrupts the machine contract has broken the
thing it was added to observe. `tests/test_telemetry.py` asserts it against a live SDK
with the console exporters on.

## Why it is off by default

A CLI that exports on first run is a CLI that ships the user's prompts to a collector
nobody declared. So the default is off, and the two ways to turn it on both name an
endpoint:

```toml
# ~/.config/<brand>/config.toml
[telemetry]
enabled        = true
exporter       = "otlp"                  # otlp | console | none
endpoint       = "http://localhost:4318" # OTLP/HTTP, the collector's base URL
service        = "media-ai"              # service.name; defaults to the CLI's own name
timeout        = 5                       # seconds allowed for the flush at exit
sample_percent = 100                     # whole percent of traces kept
logs           = true                    # export log records over OTLP as well
```

`timeout` and `sample_percent` are whole integers rather than a float and a ratio
because the config writer takes no floats — a value this build reads but cannot write
back would make the next `bindings add` fail, in a command about something else. There
is no `headers` or `token` key either: a collector behind authentication is configured
through OTel's own `OTEL_EXPORTER_OTLP_HEADERS`, which the exporter reads for itself.
`config.toml` is the shareable file that refuses a raw provider key, and a bearer token
for an observability backend is the same kind of secret wearing a different hat.

```bash
MEDIA_TELEMETRY=1 MEDIA_TELEMETRY_ENDPOINT=http://localhost:4318 media-ai image generate …
```

`MEDIA_TELEMETRY` is read three-state through `core/envflag.py`, like every other
`MEDIA_*` flag here: unset lets the config decide, `MEDIA_TELEMETRY=0` overrules a
config that says `true`. That direction is the point — a shared config file turning
telemetry on for a machine that must not export it is exactly the case an override
exists for, and a two-state read could only ever force it *on*.

`[telemetry]` is a new **modelled** table, not a schema bump. `config.toml` preserves
tables it does not model and `Config` carries `[update]` the same way, so an older
build reading a file with this table ignores it and — crucially — does not eat it on
the next write. The bump is reserved for a change of *meaning*.

## Why the SDK is an extra

`opentelemetry-sdk` and an OTLP exporter pull in a dependency tree several times the
size of this CLI, which today depends on Pillow and a bundled ffmpeg and nothing else.
An installation that never exports should not carry it, and — more to the point — a
`pip install` that can't reach an index should still get a working generator.

```bash
pip install "media-ai[otel]"                                  # a pip install
uv tool install --force "media-ai[otel] @ git+https://github.com/BrandoZhang/media-ai"   # the installer's route
uv sync --extra otel                                          # a checkout
```

So the SDK is imported **lazily, once, and only when telemetry is enabled**. The three
states and what each does:

| | SDK present | SDK absent |
| --- | --- | --- |
| **telemetry off** | nothing imported, no-op facade | nothing imported, no-op facade |
| **telemetry on** | real spans, metrics, logs | no-op facade **+ a `telemetry_unavailable` notice** |

The notice is the load-bearing half of the last cell. Asking for telemetry and getting
silence is indistinguishable from asking for telemetry and getting a collector that
drops everything, and the party who can fix it is the agent or operator reading
stdout — so it rides in `notices[]` with the `pip install` command as its `action`,
the same shape `skills_stale` and `update_available` already use. Nothing *fails*: a
missing exporter is not a reason to refuse a generation.

## What is instrumented

One root span per invocation, and children only where a call can actually spend time
or fail:

```
cli.image.generate                                  (root; the whole process)
├── binding.resolve           binding, scene, provider, wire id, how it was chosen
└── provider.generate_image   binding, provider, wire id, artifacts, bytes
    ├── http.POST             provider, url (no query), status, resend count
    └── http.GET              (a download, a poll)
```

Validation and artifact accounting are **events on the span already open**, not spans of
their own: neither can block, so a start and an end timestamp would say nothing a single
point in time does not. `job.query` and `job.cancel` get their own root-level span,
carrying the job id — the only thing that joins this process to the one that submitted
the job minutes ago.

`local/ffmpeg` and the animation exporter get `subprocess.ffmpeg` spans in place of the
`http.*` ones — the same question (where did the wall clock go) about a different
mechanism. Without them a `video concat` is a minute of wall clock a trace cannot
explain.

### Span names are low cardinality, and so are attributes

`cli.image.generate`, not `cli.image.generate --prompt "a red bicycle"`. A prompt is
unbounded user content and would make every trace its own series; what goes on the span
is `prompt.chars`, an integer. The same reasoning drops output paths, reference paths
and job ids from *metric* attributes while keeping the ids on *spans*, where high
cardinality is affordable and a job id is the only way to correlate a submit with the
poll that finished it a process later.

Every string attribute goes through `redact()` on the way in, which is belt-and-braces
over the fact that no attribute is *sourced* from a credential: the same masking that
protects the logs protects an attribute some future adapter interpolates carelessly.

### The metric set

Names live under `media_ai.*` — the **import package**, deliberately not the brand.
`brand.py` already says the import package is not renamed by a white-label build, and a
dashboard that keeps working across a rebrand is worth more than one whose series names
match the executable.

| Instrument | Kind | Attributes |
| --- | --- | --- |
| `media_ai.cli.invocations` | counter | `command`, `outcome`, `error.category`, `exit_code` |
| `media_ai.cli.duration` | histogram (ms) | `command`, `outcome` |
| `media_ai.events` | counter | `event` |
| `media_ai.provider.calls` | counter | `binding`, `provider`, `scene`, `outcome`, `error.category` |
| `media_ai.provider.duration` | histogram (ms) | `binding`, `provider`, `scene`, `outcome` |
| `media_ai.http.requests` | counter | `provider`, `method`, `status`, `outcome` |
| `media_ai.http.duration` | histogram (ms) | `provider`, `method`, `outcome` |
| `media_ai.http.retries` | counter | `provider`, `status`, `reason` |
| `media_ai.artifacts` | counter | `binding`, `scene`, `kind` |
| `media_ai.artifact.bytes` | counter (By) | `binding`, `scene`, `kind` |
| `media_ai.usage.tokens` | counter | `binding`, `scene` |
| `media_ai.subprocess.duration` | histogram (ms) | `process`, `outcome` |

`error.category` is the taxonomy from `core/errors.py`, so an alert on
`error.category="auth"` fires on the same fact that exit code 4 reports. Nothing here
invents a second vocabulary for failure.

The last three come off the **usage ledger**, not off a second count kept beside it.
`core/usage.record_usage` is already the single funnel every adapter reaches through
`Adapter.record`, so metrics hang there and cannot disagree with `media-ai usage`.
The ledger stays the source of truth for cost — it survives a process that never had a
collector, and telemetry is a mirror of it rather than a replacement.

### Key events

An event is one fact worth naming, and it lands in all three signals at once: a span
event on the current span, a structured log record, and a `media_ai.events` counter
labelled with the name. `EVENTS` is a closed set, for the same reason `notices.KINDS`
is — the name is what a consumer branches on, so it has to be enumerable.

`cli.start` · `cli.finish` · `binding.resolved` · `request.validated` ·
`provider.call` · `job.submitted` · `job.polled` · `artifact.written` ·
`usage.recorded` · `error.raised` · `telemetry.degraded`

## Logs

Same records, two renderings, chosen by `--log-format` / `MEDIA_LOG_FORMAT`:

```
$ media-ai image generate … --log-level debug
binding resolved: binding=mock/mock scene=image.text_to_image …

$ media-ai image generate … --log-level debug --log-format json
{"ts":"2026-08-14T10:31:02.418Z","level":"debug","logger":"media_ai",
 "msg":"binding resolved: binding=mock/mock …",
 "trace_id":"4bf92f…","span_id":"00f067…","command":"image.generate"}
```

`trace_id`/`span_id` are present only when a trace is actually recording, which makes
a log line joinable to the span it happened inside. The JSON rendering is a *rendering*
— the same `logger.debug(...)` call sites produce both — so nothing has to be logged
twice to be machine-readable, and the choice of format changes nothing on stdout.

Redaction wraps the finished line in both formats (`_RedactingFormatter` already did
this for text), so a secret cannot escape through a field name nobody thought to add
to `_SENSITIVE_KEYS`.

When the SDK is present and telemetry is on, the same records are additionally exported
over OTLP as log signals, correlated by the same trace id.

## Not dropping the tail

A CLI process exits in a second or two, and the default OTLP exporters batch. Without a
flush, the interesting spans — the ones from the call that just failed — are the ones
lost. So `cli.common.run()` shuts telemetry down in a `finally`, which force-flushes
every provider before the process leaves.

That flush is **bounded**, and three details of how were decided by measuring rather
than by reading the SDK:

- **One budget for the whole shutdown** (`[telemetry] timeout`, default 5s), not one
  per provider. Spent per provider, a half-second `image generate` took **9.5s**
  against a refused connection: traces waited out five seconds, then metrics waited out
  five more.
- **Enforced from outside the SDK.** `force_flush` takes a timeout,
  `TracerProvider.shutdown` takes none, and both end up joining a worker thread that is
  mid-retry — so the drain runs on a daemon thread the CLI abandons when the budget is
  gone. Same command, same refused collector, after the fix: **5.3s** at the default
  and **0.33s** with `timeout = 1`.
- **Nothing of the SDK's outlives us.** Both providers are built with
  `shutdown_on_exit=False`; otherwise their own `atexit` hook would run *after* the
  deadline gave up and block the interpreter on exactly the retries that were abandoned.

Past the deadline the telemetry is dropped, a debug line says so, and the exit code is
still the one the command earned.

The other half of not-dropping is that a telemetry failure is never an exception a
caller sees. Bootstrap, span creation, attribute setting, metric recording and shutdown
are each wrapped so the worst case is a `telemetry.degraded` event, one debug line, and
a no-op facade for the rest of the process. The SDK's *own* logging is held to the same
rule: a collector that is down makes the OTLP exporter log a warning per retry, which
is the observer putting six lines about itself on the stderr a human is reading a real
failure on. Those are quieted unless `--log-level debug` — which is exactly when "why
is nothing arriving in my collector?" is the question being asked.

## Checking it without a collector

```bash
media-ai doctor                                    # reports the telemetry state, offline
MEDIA_TELEMETRY=1 MEDIA_TELEMETRY_EXPORTER=console \
  media-ai image generate --prompt x --output /tmp/x.png   # spans + metrics on stderr
media-ai image generate … 1>/dev/null              # stdout still exactly one JSON object
```

`console` is the exporter to reach for in CI and in a bug report: OTel's own
`ConsoleSpanExporter` writes to **stdout** by default, which would put a span JSON
document beside the result document and break every consumer. Both console exporters are
constructed here with `out=sys.stderr`, and a test holds that line.
