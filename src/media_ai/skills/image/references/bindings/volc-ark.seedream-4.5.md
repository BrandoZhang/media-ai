# volc-ark/seedream-4.5 — notes

> Parameters and limits: `{{cli}} capabilities --binding volc-ark/seedream-4.5`.
> This file is only what that output cannot tell you.

## What it is good at

**Batches and knowledge-dense pictures.** The largest group budget in the Seedream set —
a storyboard, a set of product angles, a brand kit in one call — and it takes `--seed`,
which neither the Gemini nor the OpenAI image binding here does, so a run is
reproducible. It also renders diagrams, formulae and infographics as *content* rather
than as decoration.

## Prompting

**Write a coherent sentence, not a tag list.** This is the vendor's first rule and the
one with the clearest before/after:

- Good: *"一个穿着华丽服装的女孩，撑着遮阳伞走在林荫道上，莫奈油画风格"*
- Avoid: *"一个女孩，撑伞，林荫街道，油画般的细腻笔触"*

The core is **subject + action + setting**. Aesthetics — style, colour, light,
composition — are an *optional* layer on top, added only when you care.

**Concise beats ornate.** This generation does not need adjective-stacking to avoid a
washed-out look, and piling on flowery vocabulary makes results worse. Note what this
does *not* mean: enumerating many concrete objects works fine — the vendor's own
examples describe a dozen items on a desk, each with its own detail. Be brief with
*style words*, not with *content*.

There is still a ceiling: the vendor's working limit is about **300 Chinese characters
or 600 English words**. Past that, attention spreads and the model quietly drops
elements rather than refusing the request — a long prompt fails by omission, which is
hard to spot when the picture still looks good.

**Say what the picture is for.** *"设计一个游戏公司的 logo，主体是…"* outperforms
*"一张抽象图片，狗拿着游戏手柄"*. Naming the artefact type sets layout and polish in one
phrase.

**Quote text that must appear.** *标题为 "Seedream 4.5"* renders; the same words
unquoted get paraphrased.

**For diagrams, use the real terminology** and state the visual form: *"绘制一张信息图，
展示通货膨胀的成因，每条成因独立呈现，并配有简洁图标"*. Professional vocabulary is what
keeps the content correct; the layout instruction is what keeps it readable.

### Editing: name the target, and name what must not move

Be specific about the object and the change, and **say explicitly what should stay**:

- Good: *"让图中最高的那只熊猫穿上粉色的京剧服饰并戴上头饰，并保持动作不变"*
- Avoid: *"让它穿上粉色衣服"* — an ambiguous pronoun is the main cause of the wrong
  thing changing.

### Draw on the image when words cannot locate the thing

When the target is hard to describe, mark the reference itself — arrow, box, scribble —
and refer to the mark by its colour or shape in the prompt:

```bash
{{cli}} image edit --binding volc-ark/seedream-4.5 --reference marked.png \
  --output room.jpg \
  --prompt "将房间内红色涂抹位置放入电视，蓝色涂抹位置放入沙发，不改变其他布局，
            确保放入物体和整张图的原木风格一致"
```

This is the positional control available here, and it is *not* the same mechanism as
5.0 Pro's coordinates — see Traps.

> Two vendor documents disagree in a way worth knowing about. The prompt guide teaches
> mark-based editing for this model with worked examples; the capability matrix lists
> 交互编辑 — the *named feature*, coordinates and picker tooling included — as 5.0 Pro
> only. The reading that fits both: an annotated image plus prose needs no API feature
> and is taught here, while the coordinate protocol is Pro's. Treat marks as a
> documented technique rather than a guaranteed capability, and check the output.

### References: say what to keep, then say what to make

Two parts, both required: **name what to extract from the reference** (a character, a
style, a material, a garment cut) and **describe the picture you want**. With several
references, address them as `图一`/`图二`/`图三` and give each a job — *"用图一的主体替换
图二的主体"*, *"让图一人物穿上图二的服装"*.

Turning a sketch, floor plan or wireframe into a finished render has its own three
rules: supply a clean original; state the target explicitly (*"高保真 UI 界面"*, *"现代简
约风格客厅实景图"*); and name what must match (*"房间布局、家具位置完全匹配例图"*). If the
sketch carries handwritten labels, add *"遵循图中文字内容进行生成"* or they are treated as
drawing rather than instruction.

### Groups

`--count N` is the structural request. The prompt reinforces it: the vendor triggers a
set with words like *"一系列"*, *"一套"*, *"组图"*, or an explicit number — and the useful
prompt names the **axis of variation**, not the subject repeated (*"生成四张图，影视分镜，
分别对应：…"*, *"周一到周日共七张手机壁纸"*).

## Traps

- **`<bbox>`/`<point>` coordinates do not work here.** Those tags are 5.0 Pro only; this
  binding declares `interactive_edit = false` and the CLI refuses a prompt containing
  them before the call, because unrecognised they would be read as literal text.
  Drawing on the image (above) is the substitute and needs no special support.
- **Describe presence, not absence.** No Seedream binding declares `negative_prompt`, so
  `--negative-prompt` is refused (exit 3) — there is nowhere to put "no text, no
  watermark". Say what should be in the frame: *"一面空白的水泥墙"* rather than *"墙上没有
  海报"*.
- **JPEG only.** `--output x.png` writes JPEG bytes under a `.png` name. Name outputs
  `.jpg` so the file does not lie about itself.
- **There is a 2K pixel floor.** A smaller `--size` is not honoured as asked — it falls
  back to the `2K` preset. Downscale afterwards, or use 5.0, whose floor is lower.
- The documented aspect ratios are **examples, not an enum**. Only the declared ratio
  range gates, so an unusual ratio is worth trying rather than rounding to a preset.
- References and generated images share one total-image budget. A large `--count` plus
  several references can exceed it — check `constraints.output.max_total_images`.

## Further reading

Vendor prompt guide. It describes the model, not this CLI — flag names, limits and the
machine contract above are what `capabilities` says, whatever that page shows.

- 火山方舟《Seedream 提示词指南》 — <https://www.volcengine.com/docs/82379/1829186>
  (its body covers 5.0 lite, **4.5** and 4.0; this binding is one of the three it is
  actually written against)
