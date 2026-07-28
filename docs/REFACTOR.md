# 重构设计：以 Binding = (Provider, Model) 为接入单元

> 状态：**设计稿，待评审**。本文档是重构的唯一设计依据；实施 PR 逐条引用这里的编号。
> 讨论轮次见文末「决策记录」。

## 0. 这次重构要解决的一件事

`provider` 这一个名字在现有代码里承担了五个身份：适配器代码、凭据命名空间、配置命名空间、
路由键、CLI 用户面。只要「同一个模型有多个 Provider」成立，这五重耦合就必须拆开。

具体到 Seedance：`core/registry.py` 用 `model_hints=("doubao","seedream","seedance")` 做子串
路由，`seedance` 永远落到 volc；`[providers.volc]` 每个 modality 只有一个 model 槽；能力声明
按 provider 分组，装不下「同一模型在两家网关上的差异」。**Provider 与 Model 现在是「一对多且
方向写死」，而现实是多对多。** 其余问题都是它的推论。

## 1. 决策记录

| # | 决策 | 理由 |
|---|---|---|
| **D1** | 接入单元 = **binding**，每个 binding 自带完整的 `base_url` + 凭据引用，**不共享** | 越直白越好定位问题。代价（同 provider 多模型重复填 key）已知并接受 |
| **D2** | CLI **不做**任何能力回退。只提供「省略 `--provider`/`--model` 时走默认链路」的语法糖 | 回退属于 Agent 的编排职责。`--model`/`--provider` 是模型不一定学得进去的参数，无参调用必须能跑通 |
| **D3** | **声明能力，编码 wire**：元数据用 manifest 声明，wire 映射用代码写 | 纯 DSL 表达不了 Volc 的 create→poll→cancel+计费取消、Veo 的 LRO+带 key 下载、OpenAI 的 multipart+base64、ElevenLabs 的 sidecar 端点；也表达不了内部 Thrift/gRPC。DSL 会长成一门蹩脚的编程语言 |
| **D4** | Skill = 能力域主入口 + 每 binding 一份 reference 片段 | 片段按已配置的 binding 安装 |
| **D5** | 不迁移旧配置；确认淘汰的模型直接删干净 | 保持代码与文档干净 |
| **D6** | 保留 usage 记录；**不做** budget 门禁 | 只需要记录开销 |
| **D7** | 主要使用者是 Claude Code 这类 Agent | 错误必须面向 Agent 可读、可执行 |

D2 有一个直接推论，写在这里免得被当成独立设计：**凭据解析链一并收敛为显式引用**。现有的
「broker → secret-manager → keychain → 配置文件 → 环境变量」五层隐式优先级，本身就是一种隐式
回退——「key 到底从哪来的」需要推理才能回答，与 D1「好定位」和 D2「无隐式回退」都冲突。

## 2. 术语

| 术语 | 定义 | 例子 |
|---|---|---|
| **Provider** | 实际调用的那个 API 面。拥有 transport、鉴权方式、错误映射、重试与幂等策略 | `volc-ark`、`gemini`、`openai`、`elevenlabs`、`heygen`、`mock` |
| **Model** | 跨 provider 的同一份创作能力的名字 | `seedance-2.0`、`veo-3.1`、`nano-banana-2` |
| **Binding** | **接入单元** = 一个可调用的 (provider, model) 组合，带自己的端点与凭据。能力声明、场景集合、wire 映射、计价都挂在它身上 | `volc-ark/seedance-2.0`、`heygen/seedance-2.0` |
| **Scene** | 细粒度的生成场景。binding 声明支持哪些 | `video.text_to_video`、`video.image_to_video` |
| **Adapter** | 实现某个 provider wire 协议的代码 | `providers/volc_ark.py` |
| **Manifest** | 随项目发布的、声明式的 binding 定义（纯数据） | `bindings/volc-ark.toml` |

「同一个模型两个 Provider」= **两个 binding**，各自独立的能力声明、凭据、wire 映射。

## 3. Scene 分类

现有 `Operation` 只有 8 个粗粒度值，装不下你 2 号诉求里的场景映射。新增 `Scene`：

```
image.text_to_image        仅 prompt
image.image_to_image       prompt + 参考图（编辑 / 合成 / 风格迁移）

video.text_to_video        仅 prompt
video.image_to_video       + first_frame
video.keyframe_to_video    + first_frame + last_frame
video.reference_to_video   + reference_images / videos / audios（把素材当参考）
video.extend               + continue_from（从一段已有视频的结尾继续生成）
video.concat               多段视频 → 一段（本地 ffmpeg，见 §5.3）

speech.text_to_speech      单音色
speech.dialogue            多音色对话

music.text_to_music        prompt → 曲子
music.plan_to_music        composition plan → 曲子
music.plan                 prompt → composition plan（免费）

sound.text_to_sound        prompt → 音效
```

**Scene 由 CLI 从「哪些输入存在」推导**：`video generate --first-frame a.png` →
`video.image_to_video`。推导出的 scene 与 binding 声明的 `scenes` 比对，不支持就在联网前报错。
CLI 命令面（`media-ai <group> <op>`）与 stdout 机器契约**保持不变**，成功结果的 `meta` 里带上
推导出的 `scene`，便于 Agent 复盘与日志排查。

**Scene 由输入的「语义角色」决定，不由文件类型决定。** 这条规则暴露了现有 CLI 的一处歧义：
`--reference-video` 今天有两个互不相干的含义——传给 Volc 是「把这段视频当参考素材」，传给
Gemini 是「从这段 Veo 产物的结尾继续生成」（且必须是 URI，本地文件被 API 拒绝）。靠 provider
不同来隐式区分，正是 scene 拆分该消掉的那类歧义。拆成两个参数：

| 参数 | 语义 | scene |
|---|---|---|
| `--reference-video a.mp4` | 把这段视频当参考素材 | `video.reference_to_video` |
| `--continue-from <uri>` | 从这段视频的结尾继续 | `video.extend` |

**不设 `image.inpaint`。** 局部重绘（原图 + 蒙版，只重画蒙版区域）目前只有 gpt-image 支持，且
无实际需求；现有的 `--mask` / `ImageRequest.mask` / `ImageCaps.supports_mask` / OpenAI
`images/edits` 的 multipart 蒙版路径一并删除（见 §10）。要加回来是一个 scene + adapter 一段
代码的事。

### 3.1 什么不该升格成 scene

Scene 是**输入的语义角色组合**。一个 binding 独有的能力，如果不改变输入角色，就不是新 scene ——
它属于 §9 的 binding reference 片段（provider 特有的用法与技巧）。

判例：**Seedream 5.0 pro 的「交互编辑」**。它支持用 `<bbox>179 283 796 986</bbox>` / `<point>`
坐标标签，或在图上手绘标记，精确指定编辑区域。但它的 wire 输入与普通图生图**完全相同**——一张
待编辑图 + 一段 prompt，差别只在 prompt 怎么写。CLI 无法在不解析 prompt 的情况下把它跟
`image.image_to_image` 区分开，所以：

- **不设** `image.interactive_edit` scene
- **设** `constraints.supports.interactive_edit = true`
- 怎么写坐标标签，写在 `references/bindings/volc-ark.seedream-5.0-pro.md` 里

这个标志有一处实打实的用途：prompt 里出现 `<bbox>` / `<point>` 标签、而 binding 未声明
`interactive_edit` 时，**联网前报错**。否则模型会把标签当普通文字读进去，生成一张悄悄错掉的图，
既不报错也不易察觉——正是预校验该拦下的那类失败。

> **Scene 不强制拆成 adapter 方法。** wire 层面很多 scene 共用一个端点（Volc 视频的 t2v/i2v/
> ref2v 都是同一个 create-task，只是 content 数组不同）。Scene 的价值在**声明、校验、skill
> 粒度**三处，不在代码分派——按 scene 拆方法是过度设计。

## 4. 配置文件

两个文件，职责与现在一致：`config.toml` 非机密可分享，`credentials.toml` 机密且 `chmod 600`。
**结构全变**（D5：不迁移）。

### 4.1 `~/.config/media-ai/config.toml`

```toml
schema = 2

# ---- 接入单元：每条自带完整端点与凭据引用（D1） ----

[bindings."volc-ark/seedream-4.5"]
model_id   = "doubao-seedream-4-5-251128"                  # 发到线上的真实 id
base_url   = "https://ark.cn-beijing.volces.com/api/v3"
credential = "cred://volc-ark/seedream-4.5"                # 指向 credentials.toml

[bindings."volc-ark/seedance-2.0"]
model_id   = "doubao-seedance-2-0-260128"
base_url   = "https://ark.cn-beijing.volces.com/api/v3"
credential = "env://ARK_API_KEY"                           # 也可以直接指环境变量
options    = { poll_interval = 5, poll_timeout = 900 }     # 原先散在 adapter 里的 env

[bindings."gemini/veo-3.1"]
model_id   = "veo-3.1-generate-preview"
credential = "cred://gemini/veo-3.1"                       # base_url 省略 = 用 manifest 默认

[bindings."openai/gpt-image-2"]
model_id   = "gpt-image-2"
credential = "keychain://media-ai/openai"

[bindings."elevenlabs/eleven-multilingual-v2"]
model_id   = "eleven_multilingual_v2"
credential = "cred://elevenlabs"

# ---- 默认链路：省略 --provider/--model 时用（D2） ----

[defaults]
"image.text_to_image"   = "volc-ark/seedream-4.5"
"image.image_to_image"  = "volc-ark/seedream-4.5"
"video.text_to_video"   = "volc-ark/seedance-2.0"
"video.image_to_video"  = "volc-ark/seedance-2.0"
"speech.text_to_speech" = "elevenlabs/eleven-multilingual-v2"
```

默认值**按 scene 存、按能力域问**：向导只问「图片生成用哪个」，写入时展开到该域的所有 scene。
以后要精细到「生成用 A、编辑用 B」不必改 schema。

### 4.2 `extends`：一个机制覆盖三个用例

binding id 默认是 `<provider>/<model>`，但**用户可以取任意名字**，用 `extends` 继承一条已知
binding 的 provider、adapter 与能力声明：

```toml
# 用例 1 —— 第二个账号 / 第二个区域（多账号多区域，你留的 TODO）
[bindings."volc-ark-sg/seedance-2.0"]
extends    = "volc-ark/seedance-2.0"
base_url   = "https://ark.ap-southeast.volces.com/api/v3"
credential = "cred://volc-ark-sg"

# 用例 2 —— Ark 自定义推理接入点（取代现有的 backing_model / [providers.volc.endpoints]）
[bindings."volc-ark/my-image-endpoint"]
extends    = "volc-ark/seedream-4.5"       # 能力按它背后真正的模型
model_id   = "ep-example-endpoint"          # account-specific endpoint id used on the wire
credential = "cred://volc-ark/my-image-endpoint"

# 用例 3 —— 同一模型换一把 key 做对照实验
[bindings."volc-ark-test/seedream-4.5"]
extends    = "volc-ark/seedream-4.5"
credential = "env://ARK_TEST_KEY"
```

没有 `extends` 又不在任何 manifest 里的 binding：**能力标记为 `undeclared`**，跳过预校验，
并在 `capabilities` 输出和结果 `meta` 里如实写 `"capabilities": "undeclared"`。不猜，但也
不挡路——披露代替沉默。

### 4.3 `~/.config/media-ai/credentials.toml`（`chmod 600`）

扁平的账号命名空间，向导默认**按 binding id 建账号**（D1 的直白性）：

```toml
["volc-ark/seedream-4.5"]
api_key = "..."

["volc-ark/seedance-2.0"]
api_key = "..."

["gemini/veo-3.1"]
api_key = "..."

# 想共享也可以，取个自己的名字，让多个 binding 都指 cred://shared-ark —— 这是逃生舱，不是默认
[shared-ark]
api_key = "op://vault/volc/key"    # 账号的值本身也可以是引用
```

### 4.4 凭据引用（链收敛）

`credential` 字段是一个**显式引用**，没有隐式优先级：

| 引用 | 含义 |
|---|---|
| `env://ARK_API_KEY` | 读环境变量 |
| `cred://<name>` | `credentials.toml` 的 `[<name>]` 账号 |
| `keychain://<service>/<account>` | OS keychain（可选 `keyring` extra） |
| `broker://` | 券商模式：本进程只持会话令牌，密钥由 egress 注入 |
| `op://…` `vault://…` `aws-sm://…` | 可插拔后端，`register_secret_backend()` 注册 |

信任边界不变：CLI 只传引用；值只在 adapter 的 request builder 里 `reveal()`；`Secret` 仍是
只读句柄、不可 JSON 序列化；所有输出/日志/错误经 `redact()`。**config.toml 里出现裸 key 直接
拒绝**（现有行为，保留）。

## 5. Binding Manifest（D3 的核心）

随包发布的纯数据，`src/media_ai/bindings/<provider>.toml`，一个 provider 一个文件。

```toml
# src/media_ai/bindings/volc-ark.toml
[provider]
name       = "volc-ark"
title      = "Volcengine Ark"
transport  = "http"                                        # http | rpc | local
adapter    = "media_ai.providers.volc_ark:VolcArkAdapter"
docs       = "https://www.volcengine.com/docs/82379/1330310"
setup_hint = "在 Ark 控制台开通模型后创建 API Key；模型 ID 是账号相关的"

[provider.auth]
kind   = "api_key"
header = "Authorization"
scheme = "Bearer"
env    = ["ARK_API_KEY", "VOLC_API_KEY"]                   # 向导推荐的环境变量名

[provider.base_url]
default      = "https://ark.cn-beijing.volces.com/api/v3"
configurable = true

[[binding]]
id        = "volc-ark/seedance-2.0"
provider  = "volc-ark"
model     = "seedance-2.0"
title     = "Seedance 2.0"
model_id  = "doubao-seedance-2-0-260128"                   # 默认值，用户可覆盖
lifecycle = "ga"                                           # ga | preview | deprecated
verified  = ""                                             # 空 = 从未真实 API 验证过
scenes    = [
  "video.text_to_video",
  "video.image_to_video",
  "video.keyframe_to_video",
  "video.reference_to_video",
]

[binding.constraints]
durations   = [4, 6, 8, 10, 12]
resolutions = ["480p", "720p", "1080p"]
aspect_ratios = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
async       = true
supports    = { seed = true, audio = true, negative_prompt = true, last_frame = true,
                reference_images = true, reference_videos = true, reference_audios = true,
                watermark_control = true, return_last_frame = true, cancel = true }
options     = ["camera_fixed", "watermark"]

[binding.usage]
unit = "video_second"                                      # usage ledger 的计量单位
```

RPC / 内部平台的口子（D7 里你要留的）：

```toml
[provider]
name      = "internal-platform"
transport = "rpc"
adapter   = "your_internal_pkg.media_ai_adapter:PlatformAdapter"

[provider.auth]
kind = "custom"                                            # 字段由 adapter 自己解释
```

`transport = "rpc"` 时框架不假设任何 HTTP 语义：不建 `HttpClient`、不套 `base_url`，
adapter 直接拿到 `ResolvedBinding`（含凭据引用）自行建连；重试用 `media_ai.retry()`。

### 5.1 这份数据同时喂给四个消费方

| 消费方 | 现状 | 之后 |
|---|---|---|
| `media-ai capabilities` | adapter 现场拼装 + `_catalog.py`，两处 | 直接吐 manifest |
| `media-ai init` 向导 | `cli/init.py:47` 手维护的 `MODEL_SLOTS`，加模型要改代码 | 由 manifest 生成 |
| `validate_request` | caps 散在 adapter | 读 `constraints` |
| skill 的 binding 片段 | 手写 | 参数表由 manifest 生成，prompt 技巧人写 |

**这是 3 号诉求「开发与 Setup 一一对齐」的机制保证**——不是靠人记得同步改两处。

### 5.2 新模型接入规范（4 号诉求，可被 CI 断言）

接入一个新模型 = **提交一条 `[[binding]]` + 指一个 adapter**。manifest 的必填字段就是你列的
四项：对应哪个 provider（`provider`）、具体 model（`model` + `model_id`）、支持哪些功能
（`scenes` + `constraints`）、怎么鉴权（`[provider.auth]` + `[provider.base_url]`，或
`transport = "rpc"`）。

CI 断言（`tests/test_manifests.py`）：

1. 每条 binding 的必填字段齐全，`id` 全局唯一且形如 `<provider>/<model>`
2. `adapter` 可导入，且是 `Adapter` 子类
3. 声明的每个 scene 在 adapter 里有对应实现（`adapter.supported_scenes()` 覆盖）
4. `verified` 要么是合法日期，要么显式为空——**不允许猜一个日期**
5. `constraints.options` 里的每个 key 在 adapter 的 option 白名单里
6. `lifecycle = "deprecated"` 必须带 `replacement`

**同 provider 的兄弟模型（Seedream 4.5 → 5.0）通常是纯数据、零代码**——wire 协议是同一套。
新 provider 才需要写 adapter（约 200–300 行）。§11.1 用 Seedream 5.0 验证了这个承诺，并标出了
它**不**成立的那一处。

### 5.3 本地能力也是 binding

`video.concat` 需要有东西「提供」它，而 manifest 的 `transport` 本来就留了 `local`：

```toml
# src/media_ai/bindings/local.toml
[provider]
name      = "local"
transport = "local"
adapter   = "media_ai.providers.local:LocalAdapter"

[provider.auth]
kind = "none"                       # 不需要凭据

[[binding]]
id       = "local/ffmpeg"
provider = "local"
model    = "ffmpeg"
scenes   = ["video.concat"]
```

几件事因此同时对齐：

- **向导的「本地工具」分组是推导出来的**：`auth.kind = "none"` 的 binding 不问凭据——不需要给
  skill 新增一个「这是什么」的声明字段（见 §8）
- **`mock` 归位**：它本就是一条 `transport = "local"` 的 binding，不再是寻址逻辑里的特例
- **`[defaults]` 里 `video.concat` 只有一个候选**，向导自动填，用户无感
- 将来加 trim / mux / transcode，是 `local/ffmpeg` 多几个 scene，不新开 skill、不新开 provider

## 6. 代码结构

```
src/media_ai/
  core/                        # 依赖方向不变：core 永不 import providers
    binding.py       ← 新   BindingSpec / ProviderSpec / manifest 加载与校验
    resolve.py       ← 新   寻址：--binding / --provider+--model / defaults → ResolvedBinding
    scene.py         ← 新   Scene 枚举 + 从 request 推导 scene
    capabilities.py  ← 改   输入从 ModelCapabilities 换成 BindingSpec.constraints
    types.py         ← 改   Request 上带 scene
    registry.py      ← 重写  binding 注册表（manifest 驱动 + 入口点发现）
    errors.py result.py geometry.py usage.py logging.py retry.py mediaref.py   # 基本不动
  bindings/                    # 新：随包发布的 manifest（纯数据）
    volc-ark.toml  gemini.toml  openai.toml  elevenlabs.toml  mock.toml
  providers/                   # adapter（wire 代码）
    _http.py  _base.py         # HTTP 便利层，仍是可选的
    volc_ark.py  gemini.py  openai.py  elevenlabs.py  mock.py
  credentials/                 # 只剩显式引用解析
    reference.py  secret.py  redaction.py  stores.py
  cli/
  skills/
```

**删除**：`core/modelspec.py`（`ModelSpec`/`Catalog` 的职责被 manifest 接管）、
`providers/_catalog.py`、`credentials/profile.py`（profiles 机制）、`credentials/resolver.py`
（隐式链）。

### 6.1 Adapter 接口

```python
class Adapter:
    """一个 provider 的 wire 实现。构造时拿到已解析的 binding，不再自己读环境变量。"""

    def __init__(self, binding: ResolvedBinding) -> None: ...

    def supported_scenes(self) -> frozenset[Scene]: ...   # CI 用它校验 manifest

    def generate_image(self, req: ImageRequest) -> GenerationResult: ...
    def generate_video(self, req: VideoRequest) -> GenerationResult | JobHandle: ...
    def generate_speech(self, req: SpeechRequest) -> GenerationResult: ...
    # …music / sound / dialogue / job 同现有形状
```

`ResolvedBinding` = manifest 的 `BindingSpec` + 用户 config（`model_id` / `base_url` /
`credential` / `options`）+ 一个惰性凭据句柄。

**Adapter 不再读环境变量。** 现在 `providers/volc.py` 直接读 `ARK_BASE_URL`、`ARK_IMAGE_MODEL`、
`ARK_VIDEO_MODEL`、`ARK_POLL_INTERVAL`、`ARK_POLL_TIMEOUT` 五个 env，Gemini 还有七个——这是
D1「好定位」下最该消掉的东西：**一个 binding 的行为，只由它那一段配置决定**。全部下沉为
binding 的 `options`。仅保留少量真正全局的 env：`MEDIA_CONFIG_FILE`、`MEDIA_CREDENTIALS_FILE`、
`MEDIA_USAGE_LOG`。

## 7. CLI 表面

### 7.1 寻址（三种写法，精确度递减）

```bash
media-ai video generate --binding volc-ark/seedance-2.0 --prompt "…" --output c.mp4  # 最精确
media-ai video generate --provider volc-ark --model seedance-2.0 --prompt "…"        # 等价
media-ai video generate --model seedance-2.0 --prompt "…"                            # 唯一即用
media-ai video generate --prompt "…" --output c.mp4                                  # 走默认（D2）
```

- 只给 `--model`：已配置 binding 中唯一命中就用；命中多条 → **歧义错误列候选**
- 全省略：查 `[defaults]` 中该 scene 的 binding；没配 → **报错，绝不静默走 mock**
- `mock` 是一条普通 binding，必须显式选或显式设为默认

### 7.2 错误形状（面向 Agent，D7）

```json
{"ok": false, "error": {
  "category": "cli", "code": "no_default_binding",
  "message": "no binding configured for scene 'video.text_to_video'",
  "scene": "video.text_to_video",
  "configured": ["volc-ark/seedream-4.5"],
  "available": ["volc-ark/seedance-2.0", "gemini/veo-3.1"],
  "hint": "media-ai config set-default video.text_to_video volc-ark/seedance-2.0"}}
```

```json
{"ok": false, "error": {
  "category": "cli", "code": "ambiguous_model",
  "message": "model 'seedance-2.0' is served by 2 configured bindings",
  "candidates": ["volc-ark/seedance-2.0", "heygen/seedance-2.0"],
  "hint": "re-run with --binding <id>"}}
```

```json
{"ok": false, "error": {
  "category": "unsupported", "code": "scene_not_supported",
  "message": "binding 'gemini/veo-3.1-lite' does not support scene 'video.reference_to_video'",
  "binding": "gemini/veo-3.1-lite", "scene": "video.reference_to_video",
  "supported_scenes": ["video.text_to_video", "video.image_to_video", "video.keyframe_to_video"],
  "alternatives": ["volc-ark/seedance-2.0"]}}
```

`alternatives` 只从**已配置**的 binding 里筛，所以对 Agent 是可直接行动的信息——CLI 不替它
换（D2），但把换的依据给足（D7）。

新增 `error.code`（稳定的机器可读标识）与 `hint`（可直接执行的命令）——现有的
category → exit code 映射不变。

### 7.3 新增/变化的命令

```bash
media-ai bindings list                    # 已配置的 binding + 状态（凭据是否可解析）
media-ai bindings available               # manifest 里可接入但尚未配置的
media-ai bindings add volc-ark/seedance-2.0     # 交互式加一条（等价于向导的单步）
media-ai config set-default video.text_to_video volc-ark/seedance-2.0
media-ai config show                      # 脱敏后的完整生效配置
media-ai capabilities --binding volc-ark/seedance-2.0
```

**移除**：`--provider-profile` / `$MEDIA_PROFILE`（profiles 机制整体删除，`extends` 覆盖了它的
用例）、`$MEDIA_PROVIDER` 隐式默认。

## 8. Onboarding（`init` 重新设计）

结构仍是「先问完，再一次性写」（现有设计正确，保留），但**问题清单全部由 manifest 生成**：

0. **本地工具** —— `auth.kind = "none"` 的 binding（`local/ffmpeg`、`mock`）单独一问：免费、离线、
   不碰凭据。今天 `media-ai-concat` 跟 `media-ai-image` 并排出现在同一个多选题里，但选前者什么
   都不问、选后者要接着问三个凭据——**两种性质完全不同的决策长得一模一样**。分开问就解决了
1. **你要做什么** —— 能力域多选（图片 / 视频 / 语音 / 音乐 / 音效）→ 决定装哪些 skill
2. **每个能力域用哪些接入** —— 从 manifest 列出支持该域 scene 的所有 binding，多选
3. **对每条选中的 binding**，逐条问：
   - 凭据来源（三选一：环境变量 `ARK_API_KEY` / 存入 `credentials.toml` / 已有引用）
   - `base_url` 是否覆盖（manifest 说 `configurable = true` 才问）
   - `model_id` 是否覆盖（默认用 manifest 值；Ark 这类账号相关 id 会主动提示）
4. **每个能力域的默认 binding** —— 从刚配好的里选，写入 `[defaults]`
5. **一次性 apply**

第 3 步是 D1 的直接体现：同一 provider 下选了三个模型就问三遍凭据。向导会显示
「上一条用的是 `env://ARK_API_KEY`」作为提示，但**不会替你复用**——复用是你手打同一个引用的
结果，不是系统替你决定的。

**这个分组不需要给 skill 新增声明字段。** 一度考虑过在 `metadata.install` 里加一个 `kind`
（`generate` / `local-tool` / `mechanism` / `meta`），但它 100% 能从已有数据推导出来：`meta` 就是
`tier: core`，`mechanism` 就是 `tier: dependency`，`generate` 是「optional 且有 scene」，
`local-tool` 是「optional 且无 scene」。`cli/_discovery.py` 已经在做同一个推导——
`operations_for_skill("media-ai-concat")` 返回空集，所以它自动不进凭据询问，那个模块的 docstring
写得很明确：*"The provider mapping is **derived, never hardcoded**"*。加一个声明字段等于把推导退回
成一张要维护的表，会漂移。

`media-ai-job` 的依赖同理：现在 `media-ai-video` 里硬编码了 `needs: ["media-ai-job"]`，但异步不是
video 独有的——manifest 里每条 binding 都有 `constraints.async`。规则改成**任何已配置的 binding
声明了 `async = true` 就装 job skill**，那行硬编码删掉。

`doctor` 逐 binding 体检（严格离线）：manifest 是否存在 → adapter 能否导入 → `credential`
引用能否解析（只判存在，不 reveal）→ 声明的 scene 在 adapter 是否有实现 → `[defaults]` 指向的
binding 是否都已配置。

`uninstall` 语义不变（装了什么就能卸什么，默认不留残留）。

## 9. Skill 结构（D4）

```
media-ai-video/
  SKILL.md                                    # 能力域主入口：机器契约、scene 选择、默认链路
  references/
    scenes.md                                 # 各 scene 的输入组合与判断方法
    concat.md                                 # 拼接成片（原 media-ai-concat）
    bindings/volc-ark.seedance-2.0.md         # 该 binding 特有的参数与 prompt 技巧
    bindings/gemini.veo-3.1.md
```

- 主 SKILL.md 的示例命令**不带 `--provider`/`--model`**（D2 的意义所在）
- binding 片段的**参数表由 manifest 生成**，prompt 技巧人写
- 安装时按已配置的 binding 决定装哪些片段——没配 Veo 就不装 Veo 的片段，上下文不浪费

### 9.1 skill 从 10 个降到 9 个：concat 并入 video

`media-ai-concat` 是 **video-only** 的：`cli/concat.py` 输出 `"modality": "video"` /
`"operation": "video.concat"`，它自己的 SKILL.md 也写着 *"Typically the last step after generating
per-shot clips with the `media-ai-video` skill"*。它从来就是 video 工作流的最后一步，被切成独立
skill 是按 CLI 命令组切分的副产物。

**`media-ai concat` 命令本身不动**（机器契约不受影响），只是文档归属并入 `media-ai-video`，
`media-ai-concat/` 目录删除。最大的收益不是少一个目录，是**少一次 skill 触发**：Agent 生成完
5 段 clip，「怎么拼」就在它刚读过的那个 skill 里。

**speech / music / sound 保持三个，不合并。** 三者参数几乎零交集（speech 是选角：`--voice` /
cast / timestamps；music 是作曲：`--plan` / `--duration-ms` 3s–600s；sound 是单个音效：0.5–30s /
`loop` / `prompt_influence`），触发词也没交集。合并会让 Agent 每次读都跳过三分之二——skill 的成本
就是上下文。真正重复的部分（机器契约、binding 寻址、输出格式）由 `media-ai-shared` 这个 core skill
吸收。

最终 9 个：`image` · `video`（含 concat）· `speech` · `music` · `sound` · `job` · `shared` ·
`capabilities` · `usage`。

## 10. 淘汰清单（D5）

**删模型**：`dall-e-*`、`sora`、`imagen-*`（已 REMOVED）；`veo-2.0`、`veo-3.0`、
`gemini-2.5-flash-image`（DEPRECATED，且不在第一批清单里）。代码、catalog、文档、路由 hints
全部清掉，不留「返回清晰错误」的存根。

**删机制**：`model_hints` 子串路由、`backing_model`（`extends` 取代）、profiles、
`[providers.<name>]` 表、隐式凭据链、`$MEDIA_PROVIDER` 默认 mock、`ModelSpec`/`Catalog`
的 synthetic fallback。

**删功能**：局部重绘（inpaint）—— `--mask` 参数、`ImageRequest.mask`、
`ImageCaps.supports_mask`、OpenAI `images/edits` 的 multipart 蒙版路径及其测试。今天这条路径是
能跑的（只有 gpt-image 支持），但没有实际需求，留着就要一直维护它在每个新 image binding 上的
「支持/不支持」判断。见 §3。

**删 skill 目录**：`media-ai-concat/`，内容并入 `media-ai-video/references/concat.md`
（`media-ai concat` **命令保留**，见 §9.1）。

## 11. 第一批 binding 清单

| Binding | model_id | 场景 | `verified` |
|---|---|---|---|
| `volc-ark/seedream-5.0-pro` | `doubao-seedream-5-0-pro-260628` | image.text_to_image, image.image_to_image | 空 |
| `volc-ark/seedream-5.0-lite` | `doubao-seedream-5-0-260128` | image.text_to_image, image.image_to_image | 空 |
| `volc-ark/seedream-4.5` | `doubao-seedream-4-5-251128` | image.text_to_image, image.image_to_image | 空 |
| `volc-ark/seedance-2.0` | `doubao-seedance-2-0-260128` | video.{text,image,keyframe,reference}_to_video | 空 |
| `gemini/nano-banana-2` | `gemini-3.1-flash-image` | image.text_to_image, image_to_image | 2026-07-12 |
| `gemini/veo-3.1` | `veo-3.1-generate-preview` | video.{text,image,keyframe,reference}_to_video, video.extend | 空 |
| `gemini/gemini-tts` | `gemini-2.5-flash-preview-tts` | speech.text_to_speech, speech.dialogue | 2026-07-12 |
| `openai/gpt-image-2` | `gpt-image-2` | image.text_to_image, image.image_to_image | 2026-07-12 |
| `elevenlabs/eleven-multilingual-v2` | `eleven_multilingual_v2` | speech.text_to_speech | 2026-07-12 |
| `elevenlabs/eleven-v3` | `eleven_v3` | speech.dialogue | 空 |
| `elevenlabs/music-v2` | `music_v2` | music.{text,plan}_to_music, music.plan | 空 |
| `elevenlabs/sound-v2` | `eleven_text_to_sound_v2` | sound.text_to_sound | 空 |
| `mock/mock` | — | 全部 | — |

`verified` 依据 `docs/LIVE_TESTS.md`：**Volc Ark 整个 provider 从未真实验证过**（当时无 key）；
ElevenLabs 只验证过 `speech generate` + `eleven_multilingual_v2` 一条，dialogue / timestamps /
music / sound 都标着 not yet exercised。这些如实填空，不补日期。

### 11.1 Seedream 系列：binding 级 constraints 的第一个验证案例

三个 Seedream 共用同一个端点（`POST /images/generations`）、同一套鉴权，但**每一项参数约束都不同**：

| | 5.0 pro | 5.0 lite | 4.5 |
|---|---|---|---|
| model_id | `doubao-seedream-5-0-pro-260628` | `doubao-seedream-5-0-260128` | `doubao-seedream-4-5-251128` |
| 分辨率档位 | 1K, 2K | 2K, 3K, 4K | 2K, 4K |
| 输出格式 | png, jpeg | png, jpeg | **jpeg only** |
| 文生组图 / 图生组图 | ✗ | ✓ | ✓ |
| 交互编辑（坐标/标记） | ✓ | ✗ | ✗ |
| 流式输出 | ✗ | ✓ | ✓ |
| 联网搜索 | ✗ | ✓ | ✗ |
| 参考图上限 | 10 | 参考图 + 输出 ≤ 15 | 参考图 + 输出 ≤ 15 |
| 提示词优化模式 | standard, fast | standard | standard |
| 像素方式总像素区间 | [921600, 4624220] | 待补 | 待补 |

**现有代码把这三套压成了一套硬编码**（`providers/volc.py` 的 `_capabilities_for`），而且每一项都
和文档对不上：

- `named_sizes=("1K","2K","4K")` —— 三个模型分别是 1K/2K、2K/3K/4K、2K/4K，没有一个匹配
- `output_formats=("png",)` —— 4.5 只出 jpeg，而且 adapter 根本没发 `output_format` 字段：
  `--output x.png` 拿到的其实是 jpeg 字节，扩展名是错的
- `max_references=9` —— 5.0 pro 是 10，4.5/5.0 lite 是「参考图 + 输出 ≤ 15」的联合约束
- `max_count=15` —— 5.0 pro 不支持组图
- `_ARK_MIN_IMAGE_PIXELS = 2560*1440` 的「低于就回退成 2K」—— 5.0 pro 的下限是 `1280x720`

**这些不是跨 provider 的差异，是同一个 provider 内部三个兄弟模型的差异**，一套 caps 就已经装不下
了。这是 §5 binding 级 constraints 最直接的存在理由。

顺带给「兄弟模型 = 纯数据、零代码」这个承诺划出边界：**它成立，但有一处例外。**
`optimize_prompt_options.mode`（standard / fast）是 5.0 pro 独有的新请求字段。按本项目既有约定，
provider 特有的旋钮走 `--option`：声明 `constraints.options = ["optimize_prompt_mode"]`，adapter
里加一行透传。**所以是「一条 manifest + 一行代码」，不是零代码**——如果 adapter 做成通用的
option→body 透传，才真的是零。这个取舍留到 P2 决定。

新增 `constraints.inputs` 描述**输入**约束（现有 `ModelCapabilities` 只描述输出）：

```toml
[binding.constraints.inputs]
formats     = ["jpeg", "png", "webp", "bmp", "tiff", "gif", "heic", "heif"]
max_bytes   = 31457280          # 30 MB
max_pixels  = 36000000          # 6000x6000
min_edge    = 15                # 文档：宽高长度 > 14 px
ratio_range = [0.0625, 16]      # [1/16, 16]
```

有了它，一张 40 MB 的参考图在**联网前**就被拒，而不是花一次调用换一个 400。

**待补**：5.0 pro 的「支持生成单图/多张图层」——「图层」是什么形态的产物、走哪个字段，文档没写，
先留空并在 manifest 的 `notes` 里标 TODO；5.0 lite 的「联网搜索」开关字段名同理。**不猜。**

## 12. 实施阶段

| 阶段 | 内容 | 是否破坏现有行为 |
|---|---|---|
| **P0** | `core/binding.py` + manifest schema + 加载器 + 第一批 manifest + CI 断言 | 否，纯新增 |
| **P1** | `core/resolve.py` + registry 重写；CLI 切到 binding 寻址；删 profiles / model_hints / MEDIA_PROVIDER 默认 | **是**，配置格式 breaking |
| **P2** | adapter 改造：接 `ResolvedBinding`，不读 env，声明 `supported_scenes()`。四个 provider 逐个 | 是 |
| **P3** | 凭据收敛为显式引用；删 resolver 链 | 是 |
| **P4** | `init` / `doctor` / `bindings` / `config` 命令由 manifest 驱动 | 是 |
| **P5** | skill 重组（主入口 + binding 片段）+ 文档全面重写 | 是 |
| **P6** | 删淘汰模型与机制，清理文档 | 是 |

每阶段独立 PR，`uv run pytest -q` + `ruff` 全绿才推进。测试仍然全离线（`FakeClient`）。

## 13. 待补 / 遗留

- **Seedream 5.0 pro 的「多张图层」输出**、**5.0 lite 的联网搜索开关字段名**、两者的像素区间
  ——文档未给，manifest 里留空 + `notes` 标 TODO（§11.1）
- **`optimize_prompt_mode` 的透传方式**：adapter 逐个 option 显式映射，还是做通用的
  option→body 透传。前者安全、后者才真的「零代码接兄弟模型」。P2 决定
- **内部 RPC 平台**：`transport = "rpc"` + `auth.kind = "custom"` 的口子已留；具体 adapter 在
  内部仓库实现，通过 `media_ai.bindings` 入口点注册 manifest
- **多账号 / 多区域**：`extends` 已覆盖，无需额外设计（§4.2 用例 1）
- **`schema_version`**：结果 JSON 形状会因 `error.code` / `error.hint` 与 `meta.scene` /
  `meta.binding` 变化，`core/result.py` 的 `SCHEMA_VERSION` 需要 bump
- **Seedance 2.0 之外的 Volc 视频模型**：本轮不接（原「Seedance 5」是笔误）
