# Bindings — asking what exists, and reading the answer

**There is no list of models in this file, on purpose.** Model lineups change faster
than any document, and a stale list read confidently is worse than no list. The
manifests shipped with the CLI are the only source of truth; three commands read them.

## What can this machine call right now?

```bash
{{cli}} bindings list
```

```json
{"ok": true, "bindings": [
  {"binding": "volc-ark/seedance-2.0", "provider": "volc-ark", "model": "seedance-2.0",
   "model_id": "doubao-seedance-2-0-260128",
   "scenes": ["video.image_to_video", "video.keyframe_to_video",
              "video.reference_to_video", "video.text_to_video"],
   "configured": true, "needs_credential": true, "credential": "env://ARK_API_KEY"}],
 "defaults": {"video.text_to_video": "volc-ark/seedance-2.0"},
 "config": "/home/you/.config/{{cli}}/config.toml"}
```

- `scenes` is what to send it. Anything else is refused before the call.
- `defaults` is what runs when a command names no binding — this is why a bare
  `{{cli}} video generate` works at all.
- A binding with `needs_credential: false` (`local/ffmpeg`, `mock/mock`) is always
  present: there is nothing to configure.

## What could be added?

```bash
{{cli}} bindings available
```

Everything declared but not configured here, each with the command that adds it:

```json
{"bindings": [{"binding": "gemini/veo-3.1", "scenes": ["video.extend", "…"],
               "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
               "setup_hint": "Create a key at https://aistudio.google.com/apikey",
               "add": "{{cli}} bindings add gemini/veo-3.1 --credential env://GEMINI_API_KEY"}]}
```

## What does one accept?

```bash
{{cli}} capabilities --binding <provider>/<model> --pretty
{{cli}} capabilities --scene video.extend        # who serves this at all?
{{cli}} capabilities --configured                # only what is reachable now
```

The `constraints` block is what pre-flight validation enforces, so it is exactly what
a request must fit — no second copy exists to disagree with it:

```json
{"binding": "volc-ark/seedream-5.0-pro", "lifecycle": "ga", "verified": null,
 "available": true, "configured": true,
 "constraints": {
   "supports": {"interactive_edit": true, "seed": true},
   "options": ["watermark", "optimize_prompt_mode"],
   "geometry": {"mode": "both", "named_sizes": ["1K", "2K"],
                "pixel_total_min": 921600, "pixel_total_max": 4624220},
   "output": {"formats": ["png", "jpeg"], "max_count": 1},
   "references": {"max": 10, "max_bytes": 31457280}},
 "notes": ["interactive editing: …"]}
```

Fields worth reading before composing a request:

| field | why it matters |
|---|---|
| `scenes` | send anything else and it is refused |
| `constraints.supports` | `--seed`, `--audio`, `--negative-prompt`, `--timestamps` … are each gated |
| `constraints.options` | the only `--option key=value` keys this binding accepts |
| `constraints.geometry` | which of `--size` / `--aspect-ratio` / `--resolution` it takes, and the limits |
| `constraints.output.max_total_images` | where inputs and outputs share one budget |
| `constraints.references` | how many, which formats, how large — checked on **local** files before upload |
| `verified` | `null` means *never exercised against the live API*. Not a defect, but worth knowing |
| `lifecycle` | `preview` can change without notice; `deprecated` names a `replacement` |

## Two bindings for one model

Once the same model is reachable through two providers, `--model seedance-2.0` is
ambiguous and is refused with both candidates listed. Use `--binding`. The two are
**not interchangeable**: they differ in scenes, limits and options, which is why they
are separate entries rather than one with a switch.

## Undeclared capabilities

A binding defined locally without `extends` reports `"capabilities": "undeclared"`.
Validation is skipped and the API becomes the authority — the CLI says so rather than
guessing on your behalf.
