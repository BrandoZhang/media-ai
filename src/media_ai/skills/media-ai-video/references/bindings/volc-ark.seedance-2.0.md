# volc-ark/seedance-2.0 — notes

> Parameters and limits: `media-ai capabilities --binding volc-ark/seedance-2.0`.
> This file is only what that output cannot tell you.

## What it is good at

Cinematic clips with native synchronized audio, director-level camera language, and
lip-sync from dialogue quoted in the prompt. Multi-shot sequences in a single
generation. Strong at holding a character's identity across shots when given
reference images.

## Prompting

It reads a **layered brief**. Stack the layers in this order and stop wherever you have
said enough — none of them is mandatory, but out-of-order tends to blur the subject:

> `[subject] + [environment] + [action] + [camera move] + [beat-by-beat timing] +
> [transitions] + [sound] + [style & mood]`

```bash
media-ai video generate --binding volc-ark/seedance-2.0 --aspect-ratio 21:9 \
  --resolution 1080p --seed 7 --output cafe.mp4 \
  --prompt "穿米色针织衫的女孩坐在临窗的咖啡桌前；雨天午后的老城咖啡馆，窗外霓虹在雨幕里晕开。
            她放下杯子，抬头望向窗外。镜头从特写缓慢后拉成中景，轻微手持晃动。
            前 2 秒停在杯口的热气，后 3 秒转到她的侧脸。
            环境音：雨声、瓷器轻碰、远处爵士乐。写实电影感，暖调，浅景深"
```

- **Camera moves belong in the prompt**, in film language: "slow dolly in", "handheld
  whip pan", "locked-off wide". The model reads them; a `--option camera_fixed=true`
  overrides them entirely, so do not pass both.
- **Quoted dialogue drives lip-sync.** Write the line in quotes inside the prompt and
  the mouth follows it. Un-quoted narration does not.
- **Describe sound explicitly** — ambience, effects, music — because audio is generated
  jointly with the picture rather than laid on afterwards. A prompt silent about sound
  gets whatever the model infers.
- **Split the clip into beats** when more than one thing has to happen. Saying which
  half of the shot each action belongs to is what stops both from being attempted at
  once.
- References are *material*, not a storyboard: it draws on their content and style
  rather than reproducing them frame for frame.
- `--negative-prompt` is supported; put unwanted artefacts there rather than phrasing
  the main prompt in the negative.

## Traps

- **Endpoint IDs are account-specific.** Configure the Ark `endpoint_id` in the wizard
  (format `ep-…`); it is sent as the API's `model` field. A `not_found` (exit 9) usually
  means "check the endpoint in the Ark console", not "wrong model".
- **Cancellation is real and worth using.** A blocking `--wait true` cancels the billed
  task on SIGTERM/SIGINT/timeout. Do not kill the process with `-9` unless you mean to
  leave a paid task running.
- Durations are model-version specific and left to the API to validate, so an invalid
  one surfaces as a provider error rather than a pre-flight refusal.

## Further reading

Vendor prompt guides. They describe the model, not this CLI — flag names, limits and
the machine contract above are what `capabilities` says, whatever these pages show.

- 火山方舟 视频生成文档（含《Doubao Seedance 2.0 系列提示词指南》） —
  <https://www.volcengine.com/docs/82379/1366799>
- 《Seedance 2.0 文生视频提示词编写指南》 — <https://www.volcengine.com/article/40840>
