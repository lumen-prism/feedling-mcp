# IO 大版本(host 切 agent_runtime)· 剩余实现计划 — 给 Codex review

> 2026-06-27 · CC · 状态:实现计划稿,待 Codex review。目标:host 从 route-B(model_api)切到 agent_runtime,**三层(identity + voice + memory)+ perception/proactive 全通**。

---

## 0. 现状(已审,带 file:line)

**已建好,别重做**:
- agent_runtime + **cutover 机器**:`backend/hosted/agent_runtime_cutover.py` + `FEEDLING_HOST_ALL`(全切)/ 逐用户灰度。
- **genesis 蒸馏**:`backend/genesis/`(service/worker/llm_client + 7.A-C prompts);写加密 `genesis_persona` blob + identity 卡 + memory facts。
- **Dream lane**:`backend/proactive/dream_scheduler.py`(enqueue/handler/status,`job_kind=memory_dream`)。
- **memory** 读/写/落卡(capture lane,VPS e2e 验过)。
- **identity 写**:`/v1/identity/actions` `_identity_profile_patch` 服务端 re-encrypt(`actions.py:69`)+ io_cli `identity-write`(allowlist 内)。
- **voice 注入**:spawn 读 blob → prepend system prompt(`spawners.py:_genesis_persona_content` 231/364,persona-first)。
- iOS Garden(已上 main)。

**不做**:老 app 版本 gate —— 新版本直接发,旧 app 不让用/用不了(hx 0627 拍)。

**剩余 = 本计划的 §1–§4。**

---

## 1. onboarding → genesis 接线(给 **voice** 补 onboarding)

**问题**:genesis(产 identity 卡 + voice persona)**没接进 live onboarding**:
- iOS 上传走老 `uploadHistoryImport()` → `/v1/history_import/upload`(`feedling-mcp-ios FeedlingAPI.swift:2550`),**没调** `/v1/genesis/imports`。
- `FEEDLING_GENESIS_WORKER_ENABLED` 默认 OFF(`supervisor.py:636`)。
- → 新用户 onboard 完:**identity 有**(history_import 产),**voice 没有**(genesis 没跑)。

**要做**:
1. **(iOS)上传改道**:onboarding 把历史送进 genesis 的**加密分块流** `/v1/genesis/imports`(create → PUT 加密 chunks → finalize),不是 history_import。⚠️ genesis 收**加密 chunks**(CVM 隐私),iOS 端要按 content-envelope 加密后上传,不是明文。
2. **(后端)开 worker**:`FEEDLING_GENESIS_WORKER_ENABLED`;确认 finalize → worker pick up → 蒸馏 → 写 `genesis_persona` blob + identity 卡(7.C name/维度)+ memory facts。
3. **(glue)** 确认蒸完 spawn 能读到 blob(`_genesis_persona_content`)。

**已定**:
- **新 iOS onboarding 只走 genesis**;`history_import` **只做旧路 / 回滚**,**不和 genesis 同时处理同一批上传**(一份上传只进一条路,别双写)。
- 切后 identity 只由 genesis 7.C-write 产(新用户);老用户 identity 已存,不重产。

**待对齐(技术,不阻塞决策)**:iOS genesis 上传的加密(content envelope / enclave_pk_fpr)怎么和 CVM 解密对齐(`backend/genesis/service.py` chunk envelope meta)—— §8.1 第 1 闸门 E2E 时定。

---

## 2. voice backfill 静默补(老 30-40 host 用户)

**问题**:老 host 用户(route-B 下被 `tone_style`/`custom_persona_prompt` 塑形)切 agent_runtime 后**没 `genesis_persona` blob** → voice 断、退通用,且那些字段(route-B 退役后没人读)变孤儿。

**要做(⚠️ 以 §8.2 为准 —— batch + async enqueue,不在 spawn 同步跑 LLM)**:
- **30-40 个先 cutover-batch 一次**(一次性 job,复用 §7.B 喂 identity 非历史 → 写 blob);**lazy 兜底走 async enqueue**:spawn 发现缺 blob 只**入队 backfill job + 本轮 identity baseline 回答 + 下轮吃 voice**(**不在 spawn 热路径同步跑 LLM**)。
- per-user 一次、幂等(写过不再触发);identity 信号**都空** → 不补,留空交 Dream。
- 之后 Dream 持续 refine(已有 lane,但见 §8.3 tick 闸门)。
- **范围仅老 host 用户**;新用户走 §1 genesis。

### 2.1 实现 spec(Codex 设计 review 收敛,CC 已核实,锁定)

**A. 写 blob —— 走 genesis 正规生产线,source 归 `ai_persona` 家族**
- ⚠️ 纠正:**只有 `source_family == "ai_persona"` 走 persona_build**(`worker.py:429`);`user_profile` 走 strip 分支不产 persona(`worker.py:123`)。所以不能"喂任意非-history"。
- 做法:**新增 source_kind `identity_persona_backfill`(归进 `AI_PERSONA_SOURCE_KINDS` `worker.py:27`)**;把 `tone_style/custom_persona_prompt/self_introduction` 拼成 persona material → 作为 **1 个加密 chunk** 创建 genesis import job → 现有 worker claim → decrypt → persona_build → apply outputs → 写 `genesis_persona` blob。
- **不改 worker source 分支硬塞,不绕过 job 表直接写 blob。** 原料是"老用户 persona 素材",不是上传历史。

**B. 触发 —— 走 genesis job lane(`genesis_import_jobs`),不走 `capture_jobs`**
- ⚠️ 纠正:worker claim 的是 `genesis_claim_uploaded_jobs`(`db.py` genesis_import_jobs status=uploaded);capture_jobs 是 memory/dream 的 lane。**backfill 不走 capture_jobs。**
- batch:30-40 老用户各创建 `genesis_import_jobs + 1 encrypted chunk + finalize`。
- lazy:supervisor 发现缺 `genesis_persona` 且 identity 有 persona 信号 → 创建同款 genesis job + 本轮 baseline(不在 spawn 同步跑 LLM)。
- **幂等(三态区分,Codex 提醒,必做)**:lazy 每 tick 都可能发现"缺 blob + 有素材",必须分三态防重复尝试:
  1. **素材为空**(`has_persona_signal`=False)→ 不入队,留空交 Dream;
  2. **正在 backfill / 已 backfill**(已有 genesis job 带稳定 key `persona_backfill:v1:<user_id>:<material_hash>`,uploaded/processing/done)→ 跳过;
  3. **缺 blob + 有素材 + 无 job** → 才入队。
  用 **material_hash + job 查询**兜住(已实现纯件:`genesis/persona_backfill.py`)。

**C. pickup —— `_spawn_identity` 加 persona 指纹(用 `sha256` digest,不用 body_ct hash)**
- `genesis_persona` blob 已存明文 digest(`persona_sha256`,`service.py`)→ 用它做 `persona_version`(不泄露内容、比 body_ct hash 准)。
- 在 `_effective_roster`(`supervisor.py:494`)统一 enrich 点 `db.get_blob(user_id,"genesis_persona")` → 取 sha256 → 塞 `entry["persona_version"]` → `_spawn_identity` 加它。
- 写 blob 后,下个 tick `_spawn_identity` 变 → **自然 respawn 重 seed prompt** → 下轮吃 voice。
- ❌ 不直接 kill consumer(太硬、打断会话);❌ 不原地改 prompt 文件(运行中 claude/codex 不一定重读 system prompt)。

**input 映射**:`custom_persona_prompt`→"你是谁"主干、`tone_style`→语气、`self_introduction`→补充;逐字 exemplars 补不了,只出 baseline,Dream 后养。identity 信号全空 → 不补,留空。
**前提**:§8.5 P0 已解(✅ `c56e3c9`)。

---

## 3. memory 卡迁移(老卡 → v1)

**已有完整方案,本计划引用不重写**:`docs/memory/IO-memory-老数据迁移-方案-给codex.md`(`efde71f`)。
要点:静默后台 + 安静窗口(蹭 capture lane)+ **`memory.upgrade` 走 `db.memory_upsert` 单行写 + `old_body_hash` CAS + LLM 在锁外**(§5.5 时序)+ 零黑屏 + 不用 card_v(靠字段形状)+ 分阶段。
与 §2 解耦(动记忆卡 body_ct,不动 identity/persona)。

---

## 4. flag flips + config(非代码,ops)

- `FEEDLING_HOST_ALL` = 切 host → agent_runtime(或逐用户灰度)。
- `FEEDLING_GENESIS_WORKER_ENABLED` = 开 genesis worker。
- Dream 触发(`dream_scheduler` tick) —— 见 §8.5,要确认线上定时打 tick,不是翻 flag 就跑。
- **session cap:host 侧已默认 24**(`_HOST_SESSION_MAX_TURNS` `spawners.py:56/282`)—— **本次只需验部署 env 不覆盖它**。VPS consumer 默认 40 **不算本次任务**。

---

## 5. 依赖与顺序

**以 §8.5 末尾的 8 步闸门顺序为唯一准**(避免两个顺序源)。本节不再单列顺序。
全程 invariant:identity 写读不受影响(独立于 voice);memory 迁移零黑屏。

---

## 6. 完成后(本计划之外的后续)

代码完 → **Codex review** → 改 → 出**完整测试用例(最新版)** → Codex review + 测 → **hx 真机测试文档(含所有 case:身份/声音 genesis + 接线 + backfill + 迁移 + 读写召回 + Garden)**。

---

## 7. 要 Codex 整体拍的(已 review,见 §8)

- §1 的 iOS↔CVM 加密上传对齐是不是最大风险点?
- §2 lazy-on-spawn vs cutover-batch,哪个更稳?
- history_import 退役节奏(和 route-B 一起退 vs 分开)?
- 有没有我漏的"建好没接 / 切后失效"的块?

---

## 8. Codex review 收敛(0627,CC 已独立核实全部为真,采纳)

**核心认知更新:"切 runtime" 不是 clean cutover —— route-B / legacy 不能整退。** 这是本轮最大收获,打破"接线+翻 flag"的隐含假设。

### 8.1 上线前闸门(不是直接翻 flag)
1. **iOS→genesis 上传 E2E**(最大风险):iOS 不是换 URL,要对齐 **envelope 字段 + chunk hash + finalize/status + progress UI**。先把上传合同跑通 E2E。
2. **genesis worker preflight**(实锤 `supervisor.py:529` `_genesis_worker_should_start` 要 `enabled` **且** `secret`(`FEEDLING_RUNTIME_TOKEN_SECRET`)**且** `enclave_url`(`FEEDLING_ENCLAVE_URL`),缺一 dormant):上线前加 **preflight 检查**,否则"iOS 上传成功但没人蒸 voice"= 假成功。

### 8.2 voice backfill 改:**别在 spawn 热路径同步跑 LLM**(Codex 对,采纳)
- spawn(发消息热路径)里跑 §7.B LLM 会拖慢/失败。改:**spawn 发现缺 blob 只 enqueue 一个 backfill job;本轮用 identity/工具 baseline 回答,下一轮吃到 voice。**
- 最稳:**30-40 个老 host 用户先 cutover-batch 一次(一次性 job),lazy(async enqueue)兜底**散户。
- 幂等;identity 全空则不补、留空交 Dream。

### 8.3 route-B / legacy **部分保留**(实锤,必须拍)
- **图片 turn 留 legacy**:`agent_runtime_cutover.py:133 should_route = is_enabled and not has_image`(`chat_routes.py:375`)—— **runtime 纯文本,图片走 legacy multimodal**。→ **route-B 整退 = 图片聊天断。** 退役前要么 runtime 支持图片,要么**保留 legacy 多模态路**。
- **gateway provider 需 `FEEDLING_LITELLM_ENABLE`**:`cutover.py:123` codex+gateway 用户没开 gateway → 回 legacy。HOST_ALL 开了但 LITELLM 没开 → 这批断。
- **history_import 保留一个版本做回滚/兼容**,不和 route-B 同日硬删(新 iOS 走 genesis,但留旧路兜底)。

### 8.4 收敛后的落地顺序(闸门式)
1. iOS→genesis 上传 E2E(envelope/hash/status/progress)。
2. genesis worker preflight(三前置齐才算 ready)。
3. 老用户 voice:小批 cutover-batch + spawn lazy(**async enqueue,非同步**)兜底。
4. 明确 **image turn + gateway provider** 的 route-B 保留策略(不整退)。
5. 最后才翻 `HOST_ALL / GENESIS_WORKER / Dream / session cap`。

**一句话(Codex,采纳):能继续落,但按"补上线前闸门"落,不是直接翻 flag。最该先拆的是 genesis 上传合同 + worker preflight。**

---

## 8.5 Codex round-2 收敛(0627,CC 又独立核实,全为真,含纠 CC 一处错)

**新增 3 个闸门(都实锤):**

1. **🔴 P0 硬闸门 —— HOST_ALL 零 roster 下 persona 解不出来(含 token 写入时机)。** `_genesis_persona_content`(`spawners.py:231`)靠 **api_key** 走 enclave 解 `genesis_persona` blob,docstring 自己写着 **"token-only auth → tools-only"**;而 HOST_ALL 的 Stage-D entry **没 api_key**(`_resolve_discovered` `supervisor.py:314`,只有 runtime token)→ **全量托管下 voice blob 写了也解不出 → 退通用。**
   - ⚠️ **还有 token 写入时机问题(Codex 补,已核)**:supervisor 现在是 **`spawn_fn(...)` 之后**才 `_write_token`(`supervisor.py:140` respawn 路),而 persona 是在 **`spawn_fn` 内 seed home 时解的**(`agent_home_files(persona_content=_genesis_persona_content(...))`)→ **解密发生时 token 还没写**。
   - **修法**:① persona decrypt 支持 **runtime token**;**且** ② **先写/mint token 再 spawn**,或 supervisor mint 好 token **直接传给 spawner**(让 decrypt 当场拿得到)。两者都要,缺时机那半照样解不出。**这是 voice cutover 的死结,必须先解,否则 §1/§2 都白做。**

2. **P1 —— photo 工具 prompt 与 allowlist 不一致。** prompt(`agent_tools_prompt.md:20`)让 agent 用 `photo-recent/photo-read`,io_cli 也实现了(`io_cli.py:230`),但 `_IO_CLI_VERBS`(`spawners.py:43`)**没放 photo** → **Claude 被权限拦**(Codex 可能能跑)。**修法**:补 allowlist 或 prompt 删 photo,二选一对齐。

3. **P1 —— Dream 不是翻 flag 就跑。** enqueue 在 `/v1/capture/tick`/`/v1/dream/tick`(`routes.py:173/189`),且有夜间窗口 + 最少新卡/新对话阈值(`dream_scheduler.py:48`)。**要确认线上谁定时打 tick、频率、失败日志** —— 不是开了就自动跑。

**纠 CC 一处错(采纳)**:**session cap host 侧已经是 24**(`_HOST_SESSION_MAX_TURNS="24"` `spawners.py:56/282`),不是 40;只有 **VPS consumer 默认仍 40**(`chat_resident_consumer.py:263`)。→ host 这项**不是代码任务**,只需确认部署 env 不覆盖。

**修订落地顺序(替代 §8.4):**
`① genesis 上传 E2E → ② worker preflight → ③ **persona decrypt/token preflight(P0)** → ④ batch+lazy voice → ⑤ **photo/tool 权限对齐** → ⑥ image/gateway legacy 策略 → ⑦ **Dream tick 验证** → ⑧ 最后翻 flags`

**Codex 拍板**:image/gateway legacy 保留 = 认可;async backfill "本轮 baseline、下轮 voice" = 认可**但前提是先解 P0**(否则 backfill 写好 spawn 仍解不出)。**这版可进入实现,§8.5 三闸门补齐后开工。**

---

## 9. 实现进度(0627 checkpoint)— branch `feat/agent-runtime-cutover-gates`(基于 origin/test)

**已完成(10 commit,全 Codex review+测过)**:
- `fb11696` ⑤ photo 权限(`_IO_CLI_VERBS` 加 photo-*)
- `c56e3c9` ③ **P0**:HOST_ALL 下 persona decrypt(enclave 收 runtime token + entry 携 token + timing)
- `5bf5c85` ④A 基础:`companion_persona_backfill` source_kind + 纯件 `genesis/persona_backfill.py`(assemble/hash/signal)
- `e9f6cda` ④C pickup:`persona_version`(blob sha256)→ `_spawn_identity` → 自然 respawn 重 seed
- `dda83f0` 修:`_persona_version` last-good cache(DB 抖动不误 respawn)
- `e120457` ④A-3:`run_persona_backfill`(assemble→幂等→加密 chunk→genesis import job→worker)
- `3e01f13` 修:幂等改 `source_kind+material_sha256`(backfill_key 被 `_safe_job_metadata` 过滤)+ backfill 不进 blocking spawn gate(`write_genesis_state` 加 source_kind,gate 排除)
- `d470442` ④B-1:`POST /v1/genesis/persona_backfill` endpoint + `_enclave_get_json_for_gate`/`_identity_plain_for_action` token-aware
- `5878afd` 修:复位 `apply_outputs` 成功 return(B-1 插入截断了它,CC 核出 Codex 误判为 cosmetic)

- `e9e4701` ④**B-3 lazy 触发(完成 ④)**:`sup.tick` 后对 `persona_version==""` 用户 POST endpoint;单独 mint 短 TTL `["genesis","envelope_decrypt"]` token(不污染 spawn scopes)+ cap(`FEEDLING_PERSONA_BACKFILL_MAX_PER_TICK=2`)+ cooldown(`..._COOLDOWN_SEC=3600`)+ best-effort(不阻塞 tick);flag `FEEDLING_PERSONA_BACKFILL_LAZY` 默认关。

- `c375b88` 修(Codex review):无 token 时 `_identity_plain_for_action` 走旧两参调用(CI 转绿,test_identity_actions 12 passed)+ B-3 timeout 15→5s + 首触发 `uid in dict`。

**✅ ④ voice backfill 收口完成(12 commit)。Codex 复验绿:相关 suite 139 passed。可进合并前常规 CI/review。**

**剩余(非 ④)**:
- ④B-2 batch = ops 对 30-40 调 endpoint,**无新代码**。
- ① iOS 上传改道 genesis(Swift);②/⑥/⑦/⑧ = 已有/策略/ops。
- P2 单测(persona_backfill 纯件 + 幂等 + apply_outputs 成功路径 + persona_version respawn + B-3 cap/cooldown)归测试步/Codex。
