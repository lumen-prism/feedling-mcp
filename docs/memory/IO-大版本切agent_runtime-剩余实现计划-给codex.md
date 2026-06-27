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

**要 Codex 拍**:
- history_import **完全退役** vs 过渡并存?(新版本 host 走 genesis,老 route-B 退役)
- iOS genesis 上传的加密(content envelope / enclave_pk_fpr)怎么和 CVM 解密对齐(`backend/genesis/service.py` chunk envelope meta)。
- identity 现在两条产:老 history_import vs 新 genesis 7.C-write —— 切后只留 genesis?

---

## 2. voice backfill 静默补(老 30-40 host 用户)

**问题**:老 host 用户(route-B 下被 `tone_style`/`custom_persona_prompt` 塑形)切 agent_runtime 后**没 `genesis_persona` blob** → voice 断、退通用,且那些字段(route-B 退役后没人读)变孤儿。

**要做(lazy-on-spawn,复用 §7.B,不批不脚本)**:
- spawn 时(`_genesis_persona_content` 附近)若 **blob 为空 且 identity 有 persona 信号**(`tone_style`/`custom_persona_prompt` 非空)→ 触发**一次** §7.B persona-build(LLM,用户 key,genesis/CVM 路,**喂 identity 而非历史**)→ 写 `genesis_persona` blob → spawn 读到。
- **per-user 一次、快**(非批量,一个 LLM 调用)、**幂等**(写过不再触发)。
- identity 信号**都空** → 不触发(留空,交 Dream 从 ongoing 聊天慢慢养)。
- 之后 Dream 持续 refine(已有 lane)。
- **范围仅老 host 用户**;新用户走 §1 genesis,不进此路。

**要 Codex 拍**:
- 触发点放 `spawners` 还是 `supervisor`?lazy-on-spawn(首次延迟一次 §7.B)vs cutover 批一次 —— 倾向 lazy(自愈、不挑时机)。
- §7.B 喂 identity 的输入映射:`custom_persona_prompt`→"你是谁"主干、`tone_style`→语气、`self_introduction`→补充;**逐字 exemplars 补不了**(原始历史没留),只出 baseline,Dream 后续养。
- "有信号才补、全空留空"的判断阈值。

---

## 3. memory 卡迁移(老卡 → v1)

**已有完整方案,本计划引用不重写**:`docs/memory/IO-memory-老数据迁移-方案-给codex.md`(`efde71f`)。
要点:静默后台 + 安静窗口(蹭 capture lane)+ **`memory.upgrade` 走 `db.memory_upsert` 单行写 + `old_body_hash` CAS + LLM 在锁外**(§5.5 时序)+ 零黑屏 + 不用 card_v(靠字段形状)+ 分阶段。
与 §2 解耦(动记忆卡 body_ct,不动 identity/persona)。

---

## 4. flag flips + config(非代码,ops)

- `FEEDLING_HOST_ALL` = 切 host → agent_runtime(或逐用户灰度)。
- `FEEDLING_GENESIS_WORKER_ENABLED` = 开 genesis worker。
- Dream 触发(`dream_scheduler` tick)。
- session cap `AGENT_SESSION_MAX_TURNS` **40 → 20-30**(spec §8/§11.3,防 voice 漂)。

---

## 5. 依赖与顺序

1. **§1 接线 + 开 worker** → 验**新用户**:onboard → genesis → identity+voice → spawn=TA。
2. **§2 backfill** + **§3 memory 迁移** → 处理**老用户**(切过来时补 voice / 升级卡)。§2、§3 互相独立。
3. **灰度翻 `FEEDLING_HOST_ALL`** → host 上 agent_runtime;route-B 逐步退役。
4. 全程 invariant:identity 写读不受影响(独立于 voice);memory 迁移零黑屏。

---

## 6. 完成后(本计划之外的后续)

代码完 → **Codex review** → 改 → 出**完整测试用例(最新版)** → Codex review + 测 → **hx 真机测试文档(含所有 case:身份/声音 genesis + 接线 + backfill + 迁移 + 读写召回 + Garden)**。

---

## 7. 要 Codex 整体拍的

- §1 的 iOS↔CVM 加密上传对齐是不是最大风险点?
- §2 lazy-on-spawn vs cutover-batch,哪个更稳?
- history_import 退役节奏(和 route-B 一起退 vs 分开)?
- 有没有我漏的"建好没接 / 切后失效"的块?(我扫过 env flag,认为 cutover/genesis/Dream 都已建、只差翻 flag)
