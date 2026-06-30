# Genesis 入住蒸馏(onboarding distillation)可靠性 —— 问题、根因、借鉴老路的修法

> 作者:CC(Opus)· 2026-06-30 · 给 Codex / xyn review
> 仓库:feedling-mcp,分支 `test`(也已并进 main via PR#26)
> 代码点都在下面写了 file:line,Codex 请独立核一遍再动手。

## TL;DR

- prod 全库:genesis 入住蒸馏 **done 19 / failed 11 = 37% 失败率**,三分之一以上跑不完。
- 根因:**当前 genesis 蒸馏是一个"必须一次跑完的重 job"** —— 串行几十次 LLM 调用、**无网络重试、任一步失败就整 job mark_failed、无断点续传**;用户又普遍用廉价中转站(不稳),几十次调用里几乎必然命中一次失败。
- **关键发现:这套 map-reduce CVM worker 是新引入的(`1663afb`,才 7 个 commit)。它之前的老 onboarding(`hosted/history_import.py`)反而又快又稳**,因为老路有三样新路丢掉的东西:① 前台快到问候、深度蒸馏后台续;② per-call 重试;③ 分阶段(phase)。
- 修法 = **把老路那套"前台轻 + 后台续 + 重试 + 分阶段"的骨架搬回 genesis**,并区分"可重试瞬时失败"和"用户中转站配置失败"两类分别处理。

---

## 1. 问题数据(prod 全库)

11 个失败几乎全挂在「中转站这一侧」:

| 失败原因 | 数量 | 性质 | 可重试? |
|---|---|---|---|
| provider network error(ReadTimeout 等) | 3 | 中转站超时 | ✅ 瞬时 |
| no usable reply text(空回复) | 2 | 中转站返回异常 | ✅ 瞬时 |
| http_429(限流) | 1 | 中转站限流 | ✅ 退避后重试 |
| GenesisWorkerError fact-write / voice-map | 2 | reduce 某步崩 | ⚠️ 多半瞬时 |
| RuntimeError {"error"...} | 2 | key 解密 / 配置 | ❌ 用户配置 |
| http_402(欠费) | 1 | 中转站额度用尽 | ❌ 用户配置 |

- base_url 全是廉价中转站:`api2.68886868.xyz`、`aiopus.org`、`zlapi.vip`、`miaolici.top`、`fucheers.top`、`55.al` …
- 样例:`usr_934a91f270bd3686`,openai_compatible 中转 `claude-sonnet-4-6`,base_url=`fucheers.top`,因 ReadTimeout failed。
- **关联**:另有 prod 账号 `usr_4c3e3f302fecc892` 的 `memory_dream` job 全部 `failed: no_json_object` —— 很可能同一个根(廉价中转返回空/坏 JSON)。**所以这不只是蒸馏:廉价中转 provider 在 genesis / dream / capture 全链路引发连锁不稳。**

---

## 2. 验证过的根因(当前 genesis,test HEAD)

| 现象 | 代码点 | 说明 |
|---|---|---|
| 串行 map-reduce,几十次 LLM | `backend/genesis/worker.py:213`(`for idx, text in enumerate(chunk_texts)`)、`:327`(`for chunk in chunks`) | 每个 chunk 抽取一次 + 多轮 reduce,大导入串行发几十次 |
| 单次超时 90s | `worker.py:395 / 411 / 440`(`FEEDLING_GENESIS_LLM_TIMEOUT_SEC=90`) | 廉价中转慢,易超时 |
| **只有"JSON 坏了重修一次"的重试** | `worker.py:401`(`except GenesisWorkerError as first_error:` → 重修 → `:417` 再失败就 raise) | **没有网络/超时/429 的重试** |
| **任一步失败 → 整 job mark_failed** | `backend/genesis/routes.py` ~`:574`(`except Exception as e: service.mark_failed(...)`) | 全有或全无,**无断点续传**,前面几十次调用全白费 |
| 这套是新的 | `worker.py` 首次出现于 `1663afb feat(genesis): add CVM worker tick`(共 7 commit) | genesis CVM worker 是近期引入 |

三个放大问题叠加:① 纯串行 + 廉价中转慢 → 跑很久(对得上"跑了一两小时");② 任一失败整 job 挂、无重试无续传;③ 中转站不稳在几十次调用里几乎必中一次 → 失败率自然高。

---

## 3. 老路(genesis 之前)为什么又快又稳 —— 参考实现

genesis 引入前(`1663afb^` = `607c249`),host onboarding 走 **`backend/hosted/history_import.py`**(3332 行成熟 pipeline):
`parse → 抽候选 → 记忆卡 → 身份 → 问候 → 后台续蒸`。

老路有三样新 genesis 丢掉的东西:

1. **前台快到问候、深度蒸馏后台续**
   - phase 见 `history_import.py` 进度表:`identity_deriving (76, "Deriving Identity Card")`、`background_importing (96, "Continuing history distillation")`。
   - 即:跑到"身份 + 够问候的几张卡"就让用户进去聊,剩下深度蒸馏在 `background_importing` 后台慢慢补。**用户感觉一会儿就好了**——因为不在等全程。

2. **per-call 重试**
   - `history_import.py:2011`(`result = provider_client.chat_completion(...)`)→ `:2058`(`retry_result = provider_client.chat_completion(...)`):抽取失败会重试。

3. **分阶段(phase)而非单一重 job** —— 每个 phase 独立推进,不是"全 or 无"。

> 老 `history_import.py` **现在还在 repo 里**(test 上仍有此文件),这些模式可直接照着抄,不用从头设计。

---

## 4. 修法(按优先级)

> 核心:把"前台轻 + 后台续 + 重试 + 分阶段"搬回 genesis;并**区分两类失败**——瞬时类靠重试救,用户配置类靠如实透传让用户自己修。

### P0 · per-call 重试 + 指数退避(最小改动、救回约 6/11)
- 在 genesis worker 每次 `chat_completion` 外面包重试:**只对瞬时类**(ReadTimeout / 网络 / 429 / 空回复 / reduce 崩),指数退避(如 1s/3s/9s,最多 3 次)。
- **402(欠费)/ key-config 错不重试**——重试白搭,直接归类成"用户配置失败"。
- 参考:老路 `history_import.py:2058` 已有重试形态。

### P1 · 前台快→问候 + 后台续蒸(borrow 老路)
- 先一遍**轻**的:够出身份卡 + 几张关键记忆卡 → 标 job 可问候 → 放用户进去。
- 剩余 chunk 的深度蒸馏转**后台**继续(老路 `background_importing` 那套)。
- 收益:**问候不被重蒸馏卡住,onboarding 立刻"感觉好了",且后台失败不挡用户进门。**

### P1 · per-chunk checkpoint / 断点续传
- map 阶段每个 chunk 的抽取结果落 checkpoint;失败重跑时**从断点续**,别 `mark_failed` 整个重来。
- 让 P0 的重试便宜(不重做已完成的 chunk)。

### P1 · 错误分类 + 如实透传
- 把失败分成 **`transient`(我们会重试/已重试)** 和 **`provider_config`(你的中转站欠费/超时/限流/key 错)** 两类。
- `provider_config` 类要把"哪个 base_url、什么原因(402/429/timeout)"**透给用户**,让他知道换 provider,而不是 IO 默默挂掉。

### P2 · iOS 显示 failed 态(止血 UX)
- 现象:job 19:46 就 failed 了,用户却感觉"跑了一晚上"——客户端没渲染 failed,一直转圈。
- iOS 端轮询 job status,failed 时显示失败 + 原因(尤其 `provider_config` 类),别无限 spinner。**这条独立、归 iOS。**

### 可选 · 一次性重活用可靠 provider 兜底
- 蒸馏是"必须成"的一次性操作,要不要**不让用户的廉价中转站跑这一步**、用官方可靠 provider 兜底,从源头降失败率。属产品/成本决策。

---

## 5. 归属

- **genesis worker 可靠性(P0/P1)= 后端**(`backend/genesis/worker.py` + `routes.py`)。提示词归 Seven、不在本文范围;worker 这摊多半 xyn/genesis,后端地基也沾边。
- **iOS failed UX(P2)= 客户端**,独立修。
- 参考实现 `hosted/history_import.py` 现成在 repo,降低实现成本。

---

## 6. 备注:supports_responses 字段空(c7d8124 之前)
样例用户 `supports_responses` 为空,但那只影响**常驻 agent 路径**(OpenAI Responses API 探测),**不是蒸馏失败原因**——蒸馏失败纯粹是中转站 ReadTimeout 等。别混。
