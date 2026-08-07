# volc-ark/seedance-2.0-fast — notes

> Parameters and limits: `media-ai capabilities --binding volc-ark/seedance-2.0-fast`.
> This file is only what that output cannot tell you.

## What it is good at

**Iteration.** It serves the same four scenes as Seedance 2.0 and reads the same prompt
language, but turns around faster and cheaper — so it is where a shot description gets
argued into shape before the final render. Prompts written here carry over to
`volc-ark/seedance-2.0` unchanged.

## Prompting

Same structure as Seedance 2.0 — see `volc-ark.seedance-2.0.md` for the full form:

> `[subject] + [environment] + [action] + [camera move] + [beat-by-beat timing] +
> [transitions] + [sound] + [style]`

Two habits specific to using this as the draft pass:

- **Change one layer at a time.** Because it is cheap, the temptation is to rewrite the
  whole prompt each attempt; then nothing is learnt. Fix the subject and environment,
  vary only the camera move or only the lighting.
- **Draft with `--seed` set.** Holding the seed while you edit one layer is what makes
  the comparison mean anything. Carry the finished prompt *and* the seed to the full
  model — the result will not be identical, but the framing usually survives.

## Traps

- **It is a distinct model, not a speed setting.** Output differs from Seedance 2.0 at
  the same prompt; approving a draft here is not approval of the final render. Re-check
  the shot after switching.
- **Preview lifecycle, beta wire id.** The vendor labels it beta in the model id itself.
  Expect the wire id to change and do not build a pipeline that assumes it is stable —
  `bindings list` is the authority.
- **Endpoint IDs are account-specific.** Configure the Ark `endpoint_id` (format `ep-…`);
  a `not_found` (exit 9) usually means "check the endpoint in the Ark console", not
  "wrong model".
- **Cancellation is real and worth using.** A blocking `--wait true` cancels the billed
  task on SIGTERM/SIGINT/timeout. Do not `kill -9` unless you mean to leave a paid task
  running.
- Durations are model-version specific and validated by the API, so an invalid one
  surfaces as a provider error rather than a pre-flight refusal.

## Further reading

Vendor prompt guides. They describe the model, not this CLI — flag names, limits and
the machine contract above are what `capabilities` says, whatever these pages show.

- 火山方舟 视频生成文档（含《Doubao Seedance 2.0 系列提示词指南》） —
  <https://www.volcengine.com/docs/82379/1366799>
- 《Seedance 2.0 文生视频提示词编写指南》 — <https://www.volcengine.com/article/40840>
