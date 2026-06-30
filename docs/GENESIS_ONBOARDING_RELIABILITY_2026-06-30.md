# Genesis 入住蒸馏(onboarding distillation)可靠性 —— 问题、根因、借鉴老路的修法

> 作者:CC(Opus)· 2026-06-30 · CC + Codex 讨论收敛版
> 仓库:feedling-mcp,分支 `test`(也已并进 main via PR#26)
> 代码点都在下面写了 file:line,请独立核一遍再动手。

## TL;DR

- prod 全库:genesis 入住蒸馏 **done 19 / failed 11 = 37% 失败率**,三分之一以上跑不完。
- 根因:**当前 genesis 蒸馏是一个"必须一次跑完的重 job"** —— 串行几十次 LLM 调用、**无网络重试、任一步失败就整 job mark_failed、无断点续传**;用户又普遍用廉价中转站(不稳),几十次调用里几乎必然命中一次失败。
- **关键发现:这套 map-reduce CVM worker 是新引入的(`1663afb`,才 7 个 commit)。它之前的老 onboarding(`hosted/history_import.py`)反而又快又稳**,因为老路有三样新路丢掉的东西:① 前台快到问候、深度蒸馏后台续;② per-call 重试;③ 分阶段(phase)。
- 修法 = **把老路那套"前台轻 + 后台续 + 重试 + 分阶段"的骨架搬回 genesis**,并区分"可重试瞬时失败"和"用户中转站配置失败"两类分别处理。
- **结论已 CC + Codex 收敛(见 §4):P0 先止血(后端重试+分类 / iOS failed UI 并行),P1 再结构(前台轻+后台续+checkpoint)。**

---

## 1. 问题数据(prod 全库)

11 个失败几乎全挂在「中转站这一侧」:

| 失败原因 | 数量 | 性质 | 可重试? |
|---|---|---|---|
| provider network error(ReadTimeout 等) | 3 | 中转站超时 | ✅ 瞬时 |
| no usable reply text(空回复) | 2 | 中转站返回异常 | ✅ 瞬时 |
| http_429(限流) | 1 | 中转站限流 | ✅ 退避后重试 |
| GenesisWorkerError fact-write / voice-map | 2 | reduce 某步崩 | ⚠️ 先确认(§4 D) |
| RuntimeError {"error"...} | 2 | key 解密 / 配置 | ❌ 用户配置 |
| http_402(欠费) | 1 | 中转站额度用尽 | ❌ 用户配置 |

- base_url 全是廉价中转站:`api2.68886868.xyz`、`aiopus.org`、`zlapi.vip`、`miaolici.top`、`fucheers.top`、`55.al` …
- 样例:`usr_934a91f270bd3686`,openai_compatible 中转 `claude-sonnet-4-6`,base_url=`fucheers.top`,因 ReadTimeout failed。
- **关联**:另有 prod 账号 `usr_4c3e3f302fecc892` 的 `memory_dream` job 全部 `failed: no_json_object` —— 很可能同一个根(廉价中转返回空/坏 JSON)。**所以这不只是蒸馏:廉价中转 provider 在 genesis / dream / capture 全链路引发连锁不稳。**(→ §4 P0-B:重试 helper 放共享层,一次修全链路受益。)

---

## 2. 验证过的根因(当前 genesis,test HEAD)

| 现象 | 代码点 | 说明 |
|---|---|---|
| 串行 map-reduce,几十次 LLM | `backend/genesis/worker.py:213`(`for idx, text in enumerate(chunk_texts)`)、`:327`(`for chunk in chunks`) | 每个 chunk 抽取一次 + 多轮 reduce,大导入串行发几十次 |
| 单次超时 90s | `worker.py:395 / 411 / 440`(`FEEDLING_GENESIS_LLM_TIMEOUT_SEC=90`) | 廉价中转慢,易超时 |
| **只有"JSON 坏了重修一次"的重试** | `worker.py:401`(`except GenesisWorkerError as first_error:` → 重修 → `:417` 再失败就 raise) | **没有网络/超时/429 的重试** |
| **任一步失败 → 整 job mark_failed** | `backend/genesis/routes.py` ~`:574`(`except Exception as e: service.mark_failed(...)`) | 全有或全无,**无断点续传**,前面几十次调用全白费 |
| 这套是新的 | `worker.py` 首次出现于 `1663afb feat(genesis): add CVM worker tick`(共 7 commit) | genesis CVM worker 是近期引入 |
| LLM 调用入口 | Codex 抽查:`_complete_json / _complete_text` 每次直接 `llm.complete` | P0 retry helper 包在这里 |

三个放大问题叠加:① 纯串行 + 廉价中转慢 → 跑很久(对得上"跑了一两小时");② 任一失败整 job 挂、无重试无续传;③ 中转站不稳在几十次调用里几乎必中一次 → 失败率自然高。

---

## 3. 老路(genesis 之前)为什么又快又稳 —— 参考实现

genesis 引入前(`1663afb^` = `607c249`),host onboarding 走 **`backend/hosted/history_import.py`**(3332 行成熟 pipeline):
`parse → 抽候选 → 记忆卡 → 身份 → 问候 → 后台续蒸`。

老路有三样新 genesis 丢掉的东西:

1. **前台快到问候、深度蒸馏后台续** —— phase 见进度表:`identity_deriving (76, "Deriving Identity Card")`、`background_importing (96, "Continuing history distillation")`。跑到"身份 + 够问候的几张卡"就让用户进去聊,剩下深度蒸馏后台慢慢补。**用户感觉一会儿就好了**——因为不在等全程。
2. **per-call 重试** —— `history_import.py:2011`(`result = provider_client.chat_completion(...)`)→ `:2058`(`retry_result = ...`):抽取失败会重试。
3. **分阶段(phase)而非单一重 job** —— 每个 phase 独立推进,不是"全 or 无"。

> 老 `history_import.py` **现在还在 repo 里**(test 上仍有此文件),这些模式可直接照着抄,不用从头设计。

---

## 4. 修法(收敛版 · CC + Codex 讨论结论)

> CC 提原方案 → Codex 抽查 test 代码、认可根因 + "P0先止血 / P1后结构" 排序 → CC 再拧紧 5 处(A–E,已并入下方)。

### P0 · 后端,现在做(最小止血、直接降 37%)

1. **可复用的 retry helper**,包住每次 LLM 调用:
   - **放 `provider_client.chat_completion` 层、写成共享的**(不是 genesis 私有)——因为同一类"廉价中转返回空/坏 JSON/超时"**也正在打挂 dream / capture**(prod `usr_4c3e3f302fecc892` 的 memory_dream 全 `no_json_object`)。P0 先在 genesis 的 `_complete_json / _complete_text` 接上,dream/capture/model_api 后续复用同一个 → 一次修、全链路受益。**【拧紧 B】**
   - **退避 + 上限 + 尊重 429**:指数退避 + jitter(如 1s/3s/9s),最多 3 次,429 读 `Retry-After`;空回复也 2–3 次封顶。**别在抖动的 relay 上无限捶——naive 重试会把它打得更惨。【拧紧 A】**
2. **只重试瞬时类**:timeout / 网络 / 429 / 空回复 / JSON-repair 后仍失败(有限次)。
3. **不重试用户配置类**:402 欠费 / key 解密失败 / `model_api_not_configured` / `model_api_config_invalid`。
4. **失败原因分类落 job status**:
   - `transient_exhausted`:重试后仍失败。
   - `provider_config`:用户需换 key / 换 provider / 充值 —— **带 `resumable=true` 并落断点,让用户修完能续跑、不是从头重传重跑。【拧紧 C,接 P1 checkpoint】**
5. **先确认那 2 个 reduce 崩(GenesisWorkerError fact-write/voice-map)的性质**:relay 抖 → 重试覆盖;reduce 代码/数据 bug(批次过大 / 合并逻辑炸)→ 重试白搭、单独修。落地前抽一个真实失败 case 看堆栈。**【拧紧 D】**

> P0 是最小可上线止血,直接降掉大量廉价中转站导致的失败。

### P0 · iOS,并行做(止无限 spinner)

- 客户端轮询 job status,`failed` 时显示失败 + 原因(尤其 `provider_config` 类),别无限转圈(现象:job 19:46 就 failed,用户却感觉"跑了一晚上")。
- **归 iOS 仓库(feedling-mcp-ios)/ 客户端,和后端 P0 并行,别让它卡住后端止血。【拧紧 E】**

### P1 · 后端,结构修复(借老 history_import 骨架)

1. genesis 从"一次性重 job"改成 **前台轻 + 后台续蒸**。
2. **前台只产 onboarding 必需**:identity / persona baseline + greeting 最小内容 + 少量核心 memory → 达到即允许用户进 app。
3. 剩下的 memory / voice / 深度蒸馏放**后台**继续。
4. **per-chunk checkpoint**:已完成 chunk 不因后面失败全部重跑(也兜住 P0-4 的 resume)。

> P1 是体验修复:用户不再被整个深度蒸馏卡住。

### 不做(CC + Codex 一致)

- 不一上来重写 genesis 架构;不只改 prompt(根因是 worker 可靠性、不是 prompt);不把 402/key/config 强行 retry;不把失败继续藏起来。

### 可选(产品/成本决策,暂不拍)

- 蒸馏这种"必须成"的一次性重活,要不要**不让用户廉价中转站跑这一步**、用可靠 provider 兜底,从源头降失败率。

---

## 5. 归属

- **genesis worker 可靠性(P0 后端 / P1)= 后端**(`backend/genesis/worker.py` + `routes.py`;retry helper 放 `provider_client`)。提示词归 Seven、不在本文范围;worker 这摊多半 xyn/genesis,后端地基也沾边。
- **iOS failed UX(P0 并行)= 客户端**(feedling-mcp-ios),和后端 P0 并行、互不阻塞。
- 参考实现 `hosted/history_import.py` 现成在 repo,降低实现成本。

---

## 6. 备注:supports_responses 字段空(c7d8124 之前)

样例用户 `supports_responses` 为空,但那只影响**常驻 agent 路径**(OpenAI Responses API 探测),**不是蒸馏失败原因**——蒸馏失败纯粹是中转站 ReadTimeout 等。别混。
