# Genesis 入住蒸馏(onboarding distillation)可靠性 —— 问题、根因、完整 v2 方案

> 作者:CC(Opus)· 2026-06-30 · CC + hx + Codex 讨论收敛版
> 仓库:feedling-mcp,分支 `test`(也已并进 main via PR#26)
> 代码点都在下面写了 file:line,请独立核一遍再动手。

## TL;DR

- prod 全库:genesis 入住蒸馏 **done 19 / failed 11 = 37% 失败率**,三分之一以上跑不完。
- 根因:**当前 genesis 蒸馏是一个"必须一次跑完的重 job"** —— 串行几十次 LLM 调用、**无网络重试、任一步失败就整 job mark_failed、无断点续传**;用户又普遍用廉价中转站(不稳),几十次调用里几乎必然命中一次失败。
- **关键发现:这套 map-reduce CVM worker 是新引入的(`1663afb`,才 7 个 commit)。它之前的老 onboarding(`hosted/history_import.py`)反而又快又稳**,因为老路有三样新路丢掉的东西:① 前台快到问候、深度蒸馏后台续;② per-call 重试;③ 分阶段(phase)。
- **决策(hx 拍):做完整 v2,不做"先 P0 补丁、后 P1 重构"。** 已上线、v1 毛病已暴露,半吊子补丁再重做是浪费。
- **关键澄清:重试/错误分类不是丢弃的小兜底,是完整 v2 的必需地基零件** —— v2 的"后台续蒸"仍在打廉价中转站,没重试照样失败,只是失败从用户面前挪到后台、更隐蔽。

---

## 1. 问题数据(prod 全库)

11 个失败几乎全挂在「中转站这一侧」:

| 失败原因 | 数量 | 性质 | 可重试? |
|---|---|---|---|
| provider network error(ReadTimeout 等) | 3 | 中转站超时 | ✅ 瞬时 |
| no usable reply text(空回复) | 2 | 中转站返回异常 | ✅ 瞬时 |
| http_429(限流) | 1 | 中转站限流 | ✅ 退避后重试 |
| GenesisWorkerError fact-write / voice-map | 2 | reduce 某步崩 | ⚠️ 先确认(§4) |
| RuntimeError {"error"...} | 2 | key 解密 / 配置 | ❌ 用户配置 |
| http_402(欠费) | 1 | 中转站额度用尽 | ❌ 用户配置 |

- base_url 全是廉价中转站:`api2.68886868.xyz`、`aiopus.org`、`zlapi.vip`、`miaolici.top`、`fucheers.top`、`55.al` …
- 样例:`usr_934a91f270bd3686`,openai_compatible 中转 `claude-sonnet-4-6`,base_url=`fucheers.top`,因 ReadTimeout failed。
- **关联**:另有 prod 账号 `usr_4c3e3f302fecc892` 的 `memory_dream` job 全部 `failed: no_json_object` —— 很可能同一个根(廉价中转返坏 JSON)。**所以这不只是蒸馏:廉价中转 provider 在 genesis / dream / capture 全链路引发连锁不稳。**(→ §4 组件4:重试放共享层,一次修全链路。)

---

## 2. 验证过的根因(当前 genesis,test HEAD)

| 现象 | 代码点 | 说明 |
|---|---|---|
| 串行 map-reduce,几十次 LLM | `backend/genesis/worker.py:213`(`for idx, text in enumerate(chunk_texts)`)、`:327`(`for chunk in chunks`) | 每个 chunk 抽取一次 + 多轮 reduce,大导入串行发几十次 |
| 单次超时 90s | `worker.py:395 / 411 / 440`(`FEEDLING_GENESIS_LLM_TIMEOUT_SEC=90`) | 廉价中转慢,易超时 |
| **只有"JSON 坏了重修一次"的重试** | `worker.py:401`(`except GenesisWorkerError as first_error:` → 重修 → `:417` 再失败就 raise) | **没有网络/超时/429 的重试** |
| **任一步失败 → 整 job mark_failed** | `backend/genesis/routes.py` ~`:574`(`except Exception as e: service.mark_failed(...)`) | 全有或全无,**无断点续传**,前面几十次调用全白费 |
| LLM 调用入口 | Codex 抽查:`_complete_json / _complete_text` 每次直接 `llm.complete` | 重试 helper 包在这里 |
| 这套是新的 | `worker.py` 首次出现于 `1663afb feat(genesis): add CVM worker tick`(共 7 commit) | genesis CVM worker 是近期引入 |

---

## 3. 老路(genesis 之前)为什么又快又稳 —— 参考实现

genesis 引入前(`1663afb^` = `607c249`),host onboarding 走 **`backend/hosted/history_import.py`**(3332 行成熟 pipeline):
`parse → 抽候选 → 记忆卡 → 身份 → 问候 → 后台续蒸`。

老路有三样新 genesis 丢掉的东西:

1. **前台快到问候、深度蒸馏后台续** —— phase 见进度表:`identity_deriving (76, "Deriving Identity Card")`、`background_importing (96, "Continuing history distillation")`。跑到"身份 + 够问候的几张卡"就让用户进去聊,剩下深度蒸馏后台慢慢补。
2. **per-call 重试** —— `history_import.py:2011`(`result = provider_client.chat_completion(...)`)→ `:2058`(`retry_result = ...`):抽取失败会重试。
3. **分阶段(phase)而非单一重 job** —— 每个 phase 独立推进,不是"全 or 无"。

> 老 `history_import.py` **现在还在 repo 里**(test 上仍有),v2 的 fg/bg 骨架可直接照着抄。

---

## 4. 完整 v2 方案(CC + hx + Codex 讨论结论)

> **决策:一次做成 robust v2,不做"先补丁后重构"。** 下面 5 个组件是一个整体,**无一块是做了要扔的**。重试/分类是 v2 的地基零件(保后台续蒸跑稳),不是临时 P0。

### v2 = 5 个组件

1. **前台轻产出 → 秒进 app**
   只产 onboarding 必需:identity / persona baseline + greeting 最小内容 + 少量核心 memory。达到即放用户进 app。

2. **后台续蒸**
   剩余 memory / voice / 深度蒸馏放后台继续(借老 `history_import` 的 `background_importing` 骨架)。

3. **per-chunk checkpoint**
   已完成 chunk 不因后面失败全部重跑;`provider_config` 失败用户修完 key/充值后**从断点续**,不是整包重传重跑。

4. **共享层重试(地基零件)**
   - retry helper 放 **`provider_client.chat_completion` 层、共享**——`dream / capture / model_api` 复用,**同治** prod `usr_4c3e3f302fecc892` 的 memory_dream 全 `no_json_object`。
   - 退避 + jitter(1s/3s/9s)+ 上限 3 次 + 429 读 `Retry-After`;空回复 2–3 次封顶。**别在抖动的 relay 上无限捶。**
   - **只重瞬时类**:timeout / 网络 / 429 / 空回复 / JSON-repair 后仍失败(有限次)。**不重** 402 欠费 / key 解密 / `model_api_not_configured` / `model_api_config_invalid`。
   - 失败分类落 job status:`transient_exhausted` / `provider_config`(后者 `resumable=true`,接组件3的断点)。

5. **失败可见(不是无限 spinner)**
   前台轻产出后用户已进 app,"转圈一晚上"基本消失;后台失败给轻提示("还在整理" / "某步需要你换 provider / 充值"),不藏。**归 iOS(feedling-mcp-ios),和后端并行。**

### 落地顺序(都属 v2)
1. **先落组件 4(共享层重试 + 分类)** —— 它是其它组件的地基,且**最快止住现在的 37%**。
2. 再落 1 + 2 + 3(前台轻 / 后台续 / checkpoint)。
3. 组件 5(iOS 可见)和后端并行。
4. **落地前先验那 2 个 reduce 崩**(GenesisWorkerError fact-write/voice-map):relay 抖 → 重试覆盖;reduce 代码/数据 bug(批次过大/合并炸)→ 重试不治、单独修。抽真实 case 看堆栈。

### 不做(CC + Codex 一致)
- 不为了"快"只做重试补丁就收工(那不是完整版);不只改 prompt(根因是 worker 可靠性);不强行 retry 402/key/config;不藏失败。

### 可选(产品/成本,暂不拍)
- 蒸馏这种"必须成"的一次性重活,要不要不让用户廉价中转站跑、用可靠 provider 兜底,从源头降失败率。

---

## 5. 归属

- **genesis worker v2(组件 1–4)= 后端**(`backend/genesis/worker.py` + `routes.py`;retry helper 放 `provider_client`)。提示词归 Seven、不在本文范围;worker 这摊多半 xyn/genesis,后端地基也沾边。
- **失败可见(组件 5)= 客户端**(feedling-mcp-ios),和后端并行。
- 参考实现 `hosted/history_import.py` 现成在 repo,降低实现成本。
- 执行:Codex review 本方案 → 与 CC 对齐无异议 → CC 执行。

---

## 6. 备注:supports_responses 字段空(c7d8124 之前)

样例用户 `supports_responses` 为空,但那只影响**常驻 agent 路径**(OpenAI Responses API 探测),**不是蒸馏失败原因**——蒸馏失败纯粹是中转站 ReadTimeout 等。别混。
