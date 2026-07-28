# gemini/veo-3.1 — notes

> Parameters and limits: `media-ai capabilities --binding gemini/veo-3.1`.
> This file is only what that output cannot tell you.

## What it is good at

Photoreal motion and natural lighting; strong first-frame adherence. Audio is native
to Veo 3.x rather than a separate pass.

## Prompting

- Describe the **shot**, not the edit: one continuous take reads better than a list of
  cuts.
- Audio comes with the generation. The explicit `--audio` flag is unreliable on the
  Developer API — expect sound whether or not you ask, and do not treat its absence as
  a failed request.

## Traps

- **`--continue-from` takes a URI of a clip this API produced.** A local file is
  refused outright. Extension is also duration-locked — the constraint block says
  which value.
- **Jobs cannot be cancelled** on the Developer API. `media-ai job cancel` exits 3.
  Submitting is committing; think before `--wait false` on an expensive request.
- **SynthID watermarking is unconditional.** There is no flag to disable it, and
  `watermark_control` is declared false for exactly that reason.
- Several combinations (extension, reference images, 1080p, 4K) force a specific
  duration. If a request is refused on duration, that is usually why.
