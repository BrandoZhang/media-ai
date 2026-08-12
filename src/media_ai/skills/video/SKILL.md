---
name: {{skill}}video
description: >-
  Generate video from a text prompt, a first/last frame image, multimodal references
  (images/videos/audio), or by continuing an existing clip — and join finished clips
  into one film. Handles the async job flow (blocking or --wait false + poll). Use
  when asked to create, generate, make, animate, extend, stitch, concatenate or join
  a video / clip / movie / animation on the command line.
version: 2.0.0
metadata:
  requires:
    bins: ["{{cli}}"]
  cliHelp: "{{cli}} capabilities --scene video.text_to_video"
  install:
    tier: optional
    # Generation is asynchronous on every real backend, so the poll/finalize/cancel
    # skill is part of using this one, not a separate decision.
    needs: ["{{skill}}job"]
    summary: >-
      Turn a prompt, a first/last frame, reference media, or an existing clip into
      video — and join the results into one film. Generation is asynchronous, so this
      covers both waiting and polling.
---

# {{skill}}video — generate video

> **Read `../{{skill}}shared/SKILL.md` first** for the machine contract, how a
> binding is named, and credentials. **Video generation is asynchronous on every real
> backend** — see `--wait` below and the `{{skill}}job` skill for polling/cancelling.

`{{cli}} video generate` covers five scenes through one normalized request. **What
you pass decides the scene** — no flag selects one:

| you pass | scene | meaning |
|---|---|---|
| just `--prompt` | `video.text_to_video` | generate from nothing but words |
| `--first-frame` | `video.image_to_video` | animate a still |
| `--first-frame` + `--last-frame` | `video.keyframe_to_video` | move between two stills |
| `--reference-image/-video/-audio` | `video.reference_to_video` | material to draw on |
| `--continue-from <uri>` | `video.extend` | carry on from a clip's final frame |

> **`--reference-video` is not `--continue-from`.** A reference is *material the model
> draws on* — content, style, a character to keep. Continue-from is *a clip it carries
> on from*. They are different scenes and rarely served by the same binding. Some
> backends also require the continue-from clip to be a URI they produced earlier, not
> a local file.

`{{cli}} video concat` joins finished clips locally — see `references/concat.md`.

## Ask before you submit

Durations, resolutions, audio and frame support differ sharply between bindings, and
this skill deliberately does not list them:

```bash
{{cli}} capabilities --scene video.image_to_video   # who can do this at all?
{{cli}} capabilities --binding <id> --pretty        # what does that one accept?
```

**Prompt syntax differs too, and `capabilities` cannot express it.** How a model wants
a shot described — whether timing is stated or implied, how a spoken line is marked,
how a reference is addressed — is per binding, and the conventions genuinely conflict:
a construct one model documents is one another documents against. Nothing validates
prose, so a prompt written for the wrong binding is accepted, billed, and comes back
subtly wrong. Read `references/bindings/<provider>.<model>.md` before writing the
prompt, and re-read it before moving a prompt to a different binding.

## Async: `--wait`

- `--wait true` (**default**) — the CLI blocks, polls the task to completion, and
  downloads the video. Returns a `GenerationResult`.
- `--wait false` — submit and return a **`JobHandle`** immediately with a ready-to-run
  `poll` command. Finalize with the `{{skill}}job` skill (`{{cli}} job query --output`).

```json
{"status":"queued","job":{"binding":"<provider>/<model>","id":"<job-id>"},
 "poll":"{{cli}} job query --binding <provider>/<model> --id <job-id> --output clip.mp4"}
```

> **Run the `poll` string verbatim** — it names the binding that submitted the job,
> which matters because one provider can serve several bindings.

> **Cancellation is per binding**: read `constraints.supports.cancel`. Where it is
> supported, a blocking `--wait true` also cancels the *billed* task if the process is
> killed (SIGTERM/SIGINT/timeout), so a killed call leaves nothing running.

## Core flags

| flag | meaning |
|---|---|
| `--output` (required) | output video path |
| `--prompt` | text prompt (default empty; required in practice for text→video) |
| `--first-frame PATH` / `--last-frame PATH` | animate from/to a still |
| `--reference-image` / `--reference-video` / `--reference-audio PATH...` | multimodal references (JSON array ok) |
| `--duration N` / `--seconds N` | clip length in seconds (model-dependent set) |
| `--resolution {480p,720p,1080p,...}` + `--aspect-ratio R` | geometry (or `--size WxH`) |
| `--seed N` | reproducibility (where supported) |
| `--audio {true,false}` | request generated audio (where supported) |
| `--watermark {true,false}` | watermark control; omitted unless set — effective behavior is no watermark (where supported) |
| `--negative-prompt TEXT` | what to avoid |
| `--return-last-frame {true,false}` | also return the final frame as an artifact |
| `--wait {true,false}` | block+poll (default) vs async submit |
| `--continue-from URI` | carry on from this clip's final frame (`video.extend`) |
| `--option key=value` | binding-specific; only keys in its `constraints.options` are accepted |

## Quick starts

No binding flag: the configured default for the scene runs. Add `--binding <id>` only
when you need a *specific* one.

```bash
# text → video, blocking
{{cli}} video generate \
    --prompt "a paper boat sailing down a rain gutter, cinematic" \
    --resolution 720p --aspect-ratio 16:9 --duration 5 --output boat.mp4

# image → video with audio, async submit
{{cli}} video generate --first-frame hero.png --prompt "she turns and smiles" \
    --resolution 1080p --duration 6 --audio true --wait false --output hero.mp4

# references → video, on a binding you name (see `{{cli}} capabilities --scene
# video.reference_to_video` for which ones serve it)
{{cli}} video generate --binding <provider>/<model> \
    --prompt "keep this character in a neon night market" \
    --reference-image '["char1.png","char2.png"]' \
    --resolution 720p --duration 5 --output market.mp4

# join the finished shots
{{cli}} video concat --inputs '["s1.mp4","s2.mp4","s3.mp4"]' --output film.mp4
```

## References

- `references/generate.md` — full flag semantics and the five scenes with examples.
- `references/concat.md` — joining clips locally (free, offline, no credential).
- `references/bindings/` — per-binding notes: what each one is good at, and its traps.
