# volc-ark/seedance-2.0 — notes

> Parameters and limits: `media-ai capabilities --binding volc-ark/seedance-2.0`.
> This file is only what that output cannot tell you.

## What it is good at

Cinematic clips with native synchronized audio, director-level camera language, and
lip-sync from dialogue quoted in the prompt. Multi-shot sequences in a single
generation. Strong at holding a character's identity across shots when given
reference images.

## Prompting

- **Camera moves belong in the prompt**, in film language: "slow dolly in", "handheld
  whip pan", "locked-off wide". The model reads them; a `--option camera_fixed=true`
  overrides them entirely, so do not pass both.
- **Quoted dialogue drives lip-sync.** Write the line in quotes inside the prompt and
  the mouth follows it. Un-quoted narration does not.
- References are *material*, not a storyboard: it draws on their content and style
  rather than reproducing them frame for frame.

## Traps

- **Model ids are account-specific.** The shipped `model_id` may simply not be enabled
  on the account behind the key. A `not_found` (exit 9) usually means "enable it in the
  Ark console", not "wrong id".
- **A custom endpoint id (`ep-…`) names a deployment, not a model.** Configure it with
  `extends` so its capabilities come from the model it actually serves — otherwise
  nothing can answer what it supports.
- **Cancellation is real and worth using.** A blocking `--wait true` cancels the billed
  task on SIGTERM/SIGINT/timeout. Do not kill the process with `-9` unless you mean to
  leave a paid task running.
- Durations are model-version specific and left to the API to validate, so an invalid
  one surfaces as a provider error rather than a pre-flight refusal.
