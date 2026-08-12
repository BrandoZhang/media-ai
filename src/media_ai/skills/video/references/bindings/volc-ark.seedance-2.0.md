# volc-ark/seedance-2.0 — notes

> Parameters and limits: `{{cli}} capabilities --binding volc-ark/seedance-2.0`.
> This file is only what that output cannot tell you.

## What it is good at

Cinematic clips with native synchronized audio, director-level camera language, and
lip-sync from dialogue written into the prompt. Multi-shot sequences in a single
generation. Strong at holding a character's identity across shots when given reference
images — and it accepts reference **video** and **audio** as well as stills, which is
where most of its leverage is.

## Prompting

The vendor treats it as a multimodal director: the prompt is read as a *spatial* layer
(what is in frame) and a *temporal* layer (how it changes). Write engineering
instructions, not copy.

> `[subject] + [action detail] + [setting] + [light & colour] + [camera] + [visual
> style] + [image quality] + [constraints]`

### Name your subjects, then never stop naming them

With references in play, "the woman" is ambiguous. Bind a label first, using **2–3
stable static features** (clothing, hairstyle, category — not mood or pose):

> 将`图片1`中穿红色连衣裙、戴草帽的女人定义为`主体1`

Then use that same label every time. For a quick prompt with no declared label, bind
inline instead — `张三@图片1` — and keep the `@` form on every mention. Multiple
subjects each need their own unique label; reusing or dropping one is the single
biggest cause of the model swapping faces mid-clip.

References are addressed positionally as `图片1`/`视频1`/`音频1` **in upload order**, so
the order of `--reference-image` / `--reference-video` / `--reference-audio` arguments is
part of the prompt's meaning. Put the asset that most needs to be matched precisely
*earliest* in the prompt.

### Shot list, not timecodes

Split anything with more than one beat into `镜头1` / `镜头2` / `镜头3`, ordered by
when it happens, each carrying: camera move → subject action and expression → position
change → audio.

**Do not write explicit durations.** The vendor states support for precise timing
("0–3 秒") is unstable and forcing it can corrupt the generation — let the shot list
imply the pacing. This is the opposite of `gemini/veo-3.1`, where timestamped segments
are a documented feature; a prompt does not port between the two.

### Four punctuation marks that are syntax

| what | mark | example |
|---|---|---|
| music | `（）` | `（背景中播放着快节奏的摇滚乐）` |
| sound effect | `<>` | `<远处传来狗叫声>` |
| spoken line | `{}` | `{你好，世界}` |
| on-screen subtitle | `【】` | `【第一章：启程】` |

Braces are what mark a line as *spoken* — that is the lip-sync signal. For a language
other than Chinese or English, name it: `用日语说道{こんにちは}`. Official examples also
show plain quotes for dialogue, so quotes are not wrong; the braces are what the spec
actually defines, and they separate a line from a description of a line.

### Actions: small, slow, and connected

Specify the body part plus **amplitude, speed, force** — *"缓慢抬手"*, *"用力蹬地"* — and
prefer gentle continuous movement over sprinting, leaping or tumbling, which is where
this model breaks down. Write the hand-off between actions (*"借着转身惯性顺势抬手"*).
Express emotion as physical detail rather than as the word for the emotion: not
*"悲伤"* but *"低头、肩膀微微颤抖、手指攥紧衣角"*.

### Camera: one move per shot

Standard terms land directly — 中景、特写、全景、缓慢推镜、平稳横移、固定镜头. **Ask for
only one move per shot**; stacking pan + dolly + zoom destabilises the frame. A
`--option camera_fixed=true` overrides all of it, so do not pass both.

### Constraints are prose here

**`--negative-prompt` is refused here** (exit 3) — Ark documents no such field for
Seedance. The mechanism is a constraint clause written into the prompt itself:
*"保持无字幕"*, *"避免生成任何文字或字幕"*, *"不要生成Logo"*, *"不要生成水印"*. Add one
whenever a run keeps producing stray captions, logos or watermarks; the vendor treats
these as the standard remedy rather than an escape hatch.

### Budget your references

The vendor's recommended kit is **4–5 assets total**: 1–2 character stills (one face
close-up, one full body) + 1 setting + 1 camera-reference clip + 1 audio. Filling the
declared maximum is explicitly discouraged — too many assets and the model cannot rank
which features matter, so styles collide and subjects blur.

## Traps

- **Do not write "参考" when you mean edit or extend.** The task type is decided by
  wording alone: `参考<视频1>…` is a *reference* task, while editing or extending must
  name the clip directly — `严格编辑<视频1>，将…`, `向后延长<视频1>…`. Saying "参考" for
  an edit gets it read as a reference and you get a different video at full price. This
  bites hardest here because **this binding does not serve `video.extend`** — there is no
  `--continue-from` to make the intent structural, so every one of these goes in through
  `--reference-video` and the wording is the *only* thing carrying it.
- **Multi-view character sheets cause the damage they look like they'd prevent.** A
  three-view turnaround reads as several different people: it drives both ID drift and
  the "twins" failure where one person appears twice in frame. Use a face close-up plus
  a full-body shot, from separate images.
- **Past about four referenced people the output stops being reliable** — wrong head
  count, duplicated faces. Generate the crowd as two grouped stills first, then use
  those as references.
- **Vertical framing produces unrequested subtitles more often than horizontal.** If
  stray captions are the problem and the deliverable allows it, generate 16:9 and crop.
- **Re-feeding this model its own output degrades it**, and the degradation compounds
  across rounds — faces go blotchy first.
- **Endpoint IDs are account-specific.** Configure the Ark `endpoint_id` (format `ep-…`);
  a `not_found` (exit 9) usually means "check the endpoint in the Ark console", not
  "wrong model".
- **Cancellation is real and worth using.** A blocking `--wait true` cancels the billed
  task on SIGTERM/SIGINT/timeout. Do not `kill -9` unless you mean to leave a paid task
  running.
- **Keep spoken language consistent** — mixing Chinese and English in one line degrades
  delivery (proper nouns excepted). Chinese homographs and rare characters are
  mispronounced; substituting a common homophone in the prompt is the documented
  workaround.
- With a voiceover, the tail of the clip often carries a click or a truncation artefact.
  Fade the last frames out in an editor rather than regenerating.
- Durations are model-version specific and validated by the API, so an invalid one
  surfaces as a provider error rather than a pre-flight refusal.

## Further reading

Vendor prompt guide. It describes the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever that page shows.

- 火山方舟《Doubao Seedance 2.0 系列提示词指南》 —
  <https://www.volcengine.com/docs/82379/2222480>
- 镜头语言（运镜术语表） — <https://www.volcengine.com/docs/82379/1631633>
