# IO Memory v1 · VPS 接入 + onboarding 脱钩 · 执行方案(给 Codex review)

> 2026-06-25 · CC 出方案 · 目标:route A(VPS/resident-agent)完整跑 v1 —— 主循环 `index/fetch/write` + Seven 的落卡/Dream 提示词 + 新用户 onboarding 不再被 memory 卡死。
> 配套:`IO-memory-v1-bootstrap-gate死锁-与codex讨论.md`(Codex 已 review,结论 A')、Seven《落卡+Dream 完整方案》、`IO-memory-v1结构定稿-bucket-thread.md`。

---

## 0. 依赖顺序(必读)
**步骤 3(后端脱钩 gate)是步骤 1(skill v1)的前提** —— 不脱钩,新 VPS 用户写 v1 卡(小肚量)永远填不满 floor → onboarding 卡死。
**本次决定:hx 走 HTTP-direct,不等 zhihao 的 gateway。** route A 现在本就 HTTP-direct(MCP 已删),skill 直接让 agent curl 原生 v1 端点,**不依赖 gateway**。gateway(把端点注册成工具)= zhihao 后续重构时再加,**非本次前提**。
**⚠️ 边界(Codex 纠正):agent 只提交结构化 action 到 `/v1/memory/actions`(明文 `{type, memory:{bucket,threads,summary,content,importance,pulse,source}}`),由服务端/enclave 建加密 envelope。agent 不拼 `body_ct`/`K_enclave`、不需要 crypto。** "agent 说要记什么,服务端负责怎么安全存"。(**route A HTTP-direct 是过渡形态,最终收敛 agent runtime,crypto/envelope 由 runtime/enclave 统一处理 → 本次不在 route A 纠结严格 E2E,仅需知会现状,不阻塞 Step 3。**)

**开发可并行,但部署必须有序** —— 后端 gate 没先上,新用户还是被 floor 卡;skill 没改,agent 还按老端点调。**部署顺序(HTTP-direct 版):**
```
① 后端 A' gate 上 test(Codex,非破坏,旧 skill 也能活)        ← 新用户硬前提
② hx 改 skill 为 HTTP-direct v1(agent curl 原生端点 + 原生 payload)
③ Seven prompt 内容接进 skill
④ 验收:先老用户(gate 能过)测读写,后端 gate 上了再测 0 记忆新用户
（gateway = zhihao 后续重构,把 HTTP-direct 升级成注册工具,非本次前提）
```
- **步骤 3 后端 Layer 1** = Codex(①,新用户硬前提)
- **步骤 1 skill v1**(主循环 + onboarding 脱钩,HTTP-direct)= hx(②,老用户可先测、不等 ①)
- **步骤 2 提示词内容**(落卡/Dream)= Seven 出内容,hx 接线(③)

---

## 步骤 3(前提)· 后端 Layer 1 脱钩 gate —— 采纳 Codex A'
> CC 同意 Codex 的 A'(不是纯放行,chat 保留 identity 轻 gate,防 day-1 飘)。以下为 A' 的最小改动:

**产品口径:Memory = 关系材料,不是开门钥匙;Identity = agent 最低底色,先有;onboarding 完成 = identity 建立 + live chat 链路接通。**

1. **`bootstrap/gates.py` · `_bootstrap_state()`** 不再返回 `needs_memory`;stage 只剩 `needs_identity` / `main_loop`;`missing_tabs` 保留空数组兼容旧响应、不参与 gate。
2. **`_gate_bootstrap_for_identity_init`**:identity 初始化**不再查 memory floor**。0 张 memory 也能初始化,只要 identity envelope / days_with_user / relationship_anchor_evidence 合法。
3. **`_gate_bootstrap_for_chat`**:chat **不再查 memory floor**;保留:identity 没写 → `needs_identity`;identity 有了 → 继续查 resident consumer / live loop。**不纯放行**(否则没 identity 就开聊,day-1 飘)。
4. **`/v1/onboarding/validate`**:`memory_garden` 不再是 passing 必要步骤;改 informational(`passing:true, blocking:false, memory_count:N`)或移出主流程。**Codex 查实这是第三个隐藏 gate(非 409,但让 onboarding 判定失败),且三个 route 都有,必须都改:**
   - `model_api`:`memory_garden` 现要求 `history_ok + story/about_me ≥ 1` → 去掉记忆要求。
   - `official_import`:`memory_ok = not missing_tabs` → 改 non-blocking。
   - `resident`:`memory_ok = not missing_tabs` → 改 non-blocking。
5. **`/v1/bootstrap/status`**:`is_complete` 不再依赖 `bootstrap_memory_ok`;新完成标准 = `identity_written` + `resident_consumer_connected` + `chat_loop_verified` + `agent_messages_count ≥ 1`;`memory_count` 仅展示。
6. **bootstrap 响应文案 / suggestions**:不再教 agent 3 tab / floor / verify-as-gate(后端侧;skill 侧由步骤 1b 处理)。

**`needs_memory` 当前波及三处(Codex 查实):`/v1/identity/init`、`/v1/chat/response`、`/v1/onboarding/validate` —— 三处都要脱钩。**

**P6 退役分两步(不必同 PR 全删):**
- **第一步(必做,随 Layer 1)**:gate / onboarding-validate / bootstrap-status 不再依赖 tab/floor。
- **第二步(cleanup)**:退役 `/v1/memory/verify` 的 floor passing 语义、bootstrap 文案的 3 tab/6 type/floor、`TAB_FOR_TYPE`/`MEMORY_TYPES` 旧 gate 用法、`retype`/insight/reflection anchor 旧逻辑。
  - **(Codex 复跑发现,2026-06-26)** `backend/admin/data_track.py` 仍有旧 memory floor 文案 —— 但是 admin/诊断展示路径,**非 runtime gate**,不挡 Step 3,归 P6。
  - **(2026-06-26)** `test_bootstrap_gates.py` 4 个 verify/retype per-tab 测试已 `@pytest.mark.skip(P6)`(被 v1 `_count_by_tab` shim 退役,预存于 origin/test)—— 随 verify/retype 重做时一并改/删。

**存量卡:不为 gate 迁移**(gate 以后不看 type/tab/floor);旧卡读取继续靠 adapter → v1。

---

## 步骤 1 · route A(VPS)skill 改 v1
> 文件:`io-onboarding/skill.md`(base,~116-410 memory 段 + identity 段 + bootstrap 段);`skill-resident-agent.md` 继承不变(仍 defer base),改的是 base。

**工具名定稿(Codex 拍,全栈统一,不许两套并存)**:
```
feedling_memory_search   → POST /v1/memory/index      (search 比 index 更像 agent 行为)
feedling_memory_fetch    → POST /v1/memory/fetch
feedling_memory_write    → POST /v1/memory/actions
feedling_memory_buckets  → GET  /v1/memory/buckets
feedling_memory_threads  → GET  /v1/memory/threads
```
**两种形态(本次 HTTP-direct vs 未来 gateway)**:
```
本次 HTTP-direct(skill 让 agent 直接 curl 原生端点 + 原生 payload):
  写 → POST /v1/memory/actions {"type":"memory.add","memory":{...}}        # 原生,无 {op} 翻译
       POST /v1/memory/actions {"type":"memory.supersede","target_id":"...","memory":{...}}
  读 → POST /v1/memory/index {query/bucket/thread/ambient} / fetch {ids}
未来 gateway(zhihao 注册成工具时,做 {op}→{type} 翻译):
  feedling_memory_write {op:add} → {"type":"memory.add",...}
```
`feedling_memory_*` 是**统一叫法**(zhihao 文档、skill、未来 gateway 对齐);**HTTP-direct 下 skill 写明原生端点+payload,agent curl;gateway 来了再收成注册工具。**

**1a. 主循环读写:`list/get/add_moment` → `search/fetch/ambient/write`**(镜像感知两段式:`search`=轻扫目录≈`screen_analyze`,`fetch`=取正文≈`decrypt_frame`)
```
读(Step B 消息到达):
  feedling_memory_search {ambient:true}                 # 底色,无 query,importance×pulse×recency
  feedling_memory_search {query/bucket/thread}          # 相关时:看目录(不含 content)
  feedling_memory_fetch {ids:[...]}                     # 挑 1-3 取 content,回写 last_referenced_at
  follow_thread(X) = feedling_memory_search {thread:X}
写(断点:idle/轮数/显式收尾,小肚量 0-2):
  feedling_memory_buckets / feedling_memory_threads     # resolve-before-create 词表
  feedling_memory_write {op:add, memory:{bucket,threads,summary,content,importance,pulse}}
  feedling_memory_write {op:supersede, target_id, memory:{...}}   # target_id 必须真实(见步骤2规则)
```
**⚠️ skill / wrapper 一律走 `feedling_memory_write → /v1/memory/actions`,绝不再碰 `/v1/memory/add` + type**(否则把 v1 拉回旧 type/tab)。

**1b. onboarding 段脱钩(对齐步骤 3 / A')**
- **删** 4-pass bootstrap(按 type 写)、`feedling_memory_verify passing=true` 当 gate、"identity 从 Memory Garden derive、Story+About me floor 是 identity_init 硬前提"。
- **identity 独立先建**:onboarding 先做一段"认识你"的 identity 设置(用户掌控、可空),**不靠记忆卡数**。
- **完成标准改**:identity 建立 + resident consumer 接通 + live loop 验证 + ≥1 条 agent 消息(对齐步骤 3.5)。
- **Tool Reference「Memory garden」段** 改 v1 工具(去 add_moment/retype/verify-as-gate)。
- **冷启动**:day-1 底色靠 identity + 自然对话里有机产生的几张卡,**不强制灌卡**(小肚量)。

**1c. 顺带**:`skill-api.md` Acceptance 若提 floor 一并去(低优先);旧 stub skill 跳转占位不动。

### Step 1 · Codex sanity-check 收(2026-06-26)· 开工前锁定
**⚠️ 关键坑:identity 首次 init 不能 HTTP-direct 明文(和 memory 不同)**
- memory:agent 提交明文 action → `/v1/memory/actions` → **服务端建 envelope**,无需 crypto。
- identity:`/v1/identity/init` **仍要预建 `envelope`**(crypto);`/v1/identity/actions` 只支持 profile_patch / dimension_nudge / relationship_days_set,**不支持首次 init**。
- → **skill 必须写清**:`Memory actions are server-enveloped (no crypto). Identity init may still require the existing crypto-capable identity tool/path until gateway/runtime standardizes it.` 否则后端 gate 已放行 0 记忆 identity,但 route A agent 实际写不了 identity → onboarding 走不完。
- **后端 follow-up(另一轮)**:给 `/v1/identity/actions` 加「服务端建 envelope 的 init」→ 彻底闭环 route A HTTP-direct。

**其它锁定**:
- **`ambient` 不是真端点** → 映射 `/v1/memory/index` 无 query / 默认排序;别写成 `/v1/memory/ambient`。
- **HTTP-direct 写法**:读 `POST /v1/memory/index`·`/fetch` + `GET /buckets`·`/threads`;写 `POST /v1/memory/actions` + `X-API-Key` + body `{type: memory.add|supersede|delete, ...}`;agent 不建 `body_ct/K_enclave/K_user`。
- **范围(Codex)**:`skill.md`(全 v1)+ `skill-resident-agent.md` + `skill-hermes.md`(connection+validate 口径)+ `skill-api.md`(轻改)+ grep 全仓 `feedling_memory_add_moment/retype/verify`·`floor`·`Pass 1-4`·`type=`·`moment/quote/fact/event/insight/reflection`;`quickstart.md`/`troubleshooting.md` 至少标 stale。
- **running capture / periodic review / push 段也引用旧 type** → 一并换 v1(capture 判断:稳定偏好/关系事实/边界/情绪模式/重要事件/未完线程;写结构 bucket/threads/summary/content/importance/pulse/source;冲突先 search/fetch 找 old_id 再 supersede;不写就 skip,不输出 noop)。
- 执行顺序:skill.md → resident+hermes → api 轻改 → grep 清残留 → quickstart/troubleshooting 标 stale。

---

## 步骤 2 · Seven 内容接入(落卡 / Dream / eval)
> Seven《落卡+Dream 完整方案》的提示词 = 真正要填的内容。

- **落卡 prompt + 读取指引** → 进 skill.md 的 memory 段(route A)+ `backend/memory/prompts_v1.py`(route B),**一份内容两处用,语义对齐**。
- **断点触发 + 小肚量(0-2)+ 并优于增** → capture 行为,route A 在 agent loop 里、route B 在 runtime(zhihao);阈值(15-30 分/20-30 轮)+ importance 四档 = 起始值、eval 调。
- **动作映射**:Seven prompt 吐 `add|merge|supersede|noop` → **`merge→supersede`(揉成更全的卡再覆盖)、`noop→不写`**;后端只认 add/supersede/delete,**接 prompt 那层翻译,后端不动**。
- **⚠️ supersede 防呆(Codex)**:**没有真实 `target_id` 不许 supersede**。模型"感觉像在更新旧记忆"不算数,必须真的通过 `search/fetch` 拿到旧卡 id;拿不到就 `add` 或 `skip`。防止凭空 supersede 把不存在/猜错的卡覆盖。
- **pulse = Seven 口径**(AI 自己被触动多大),已同步进结构定稿。
- **Dream + eval = post-v1**:设计就位(Seven 文档),排在 v1 读 loop 接通之后;eval golden set 要 hx+Seven 人工标。

---

## 部署 / 验证(HTTP-direct 版,有序)
**并行开发,有序部署:**
1. **① 后端 A' gate**(Codex)先上 test(非破坏性,旧 skill 仍活)—— 新用户硬前提。
2. **② skill 改 HTTP-direct v1**(hx):agent curl 原生端点、**只提交明文 action**(`POST /v1/memory/actions {type:...,memory:{...}}`),**服务端建 envelope**;**老用户(gate 能过)现在就能测,不等 ①**。
3. **③ Seven prompt** 接进 skill。
4. **④ 验收**:
   - **老用户(可先测)**:存量 type 卡 → adapter 读出 v1 → `search/fetch/write` 跑通。
   - **新用户(等 ① 上了再测)**:0 张记忆 → identity 能建 → 开聊不被 floor 卡 → 主循环跑通 → `bootstrap/status` 按 identity+live-loop 判完成。
> gateway = zhihao 后续重构,把 HTTP-direct 升级成注册工具,**非本次前提**。

---

## Codex review 已收(2026-06-25)+ 4 约束已补
Codex 看完方案 + 真实代码,同意三步大方向,提了 4 约束,**已全部补进本文**:
1. **工具名统一** `feedling_memory_search/fetch/write/buckets/threads`(search 包 /index、write 包 /actions)+ gateway 翻译 write→`{type:memory.add/...}` —— 见步骤 1 顶部。
2. **validate 三分支**(model_api / official_import / resident 都有旧 memory gate)全改 non-blocking —— 见步骤 3.4;`feedling_onboarding_validate` 确认是第三隐藏 gate。
3. **部署有序** —— 见 §0 + 部署节。**注:本次 hx 走 HTTP-direct(不等 gateway),顺序简化为 后端 gate(Codex)→ skill HTTP-direct(hx)→ Seven → 验收;gateway 推后给 zhihao。**
4. **supersede 必须真实 target_id**(凭空不许 supersede)—— 见步骤 2。
> type/tab 旧逻辑 Layer 1 不必一次删完,但 **skill/wrapper 一律走 `/v1/memory/actions`,不碰 `/v1/memory/add`+type**(否则拉回旧模型)。
> **Codex 结论:补完这 3 处(工具名/validate三分支/部署顺序)即可开工,他落步骤 3 后端。** ← 已补完,可开工。

## Codex review-2(2026-06-26)· 边界确认后再开工
Codex 停在 review,同意 A' + HTTP-direct + 部署顺序,但要把 4 条**边界写进 plan** 才开工:
1. **onboarding 不再卡 memory floor**(§步骤3,已写)。
2. **`memory_garden` = informational / non-blocking**(§3.4,已写)。
3. **⚠️ HTTP-direct 只提交 action,不自建 envelope** —— agent 提交明文 `{type, memory:{...}}` 到 `/v1/memory/actions`,**服务端/enclave 建加密 envelope**;agent 不拼 `body_ct`/`K_enclave`、不需 crypto。(已修 §0 + 步骤1 + 部署节的"自建 envelope"错。)
4. **`/v1/memory/verify` 旧 floor 语义本次不动** —— 归 P6 cleanup,别混进这次。(本次只改 bootstrap / onboarding-validate / status 这层。)

**执行前再查实的隐藏 gate / 残留**(Codex 列):`bootstrap/gates.py`、`/v1/onboarding/validate` 三 route(model_api/official_import/resident)、`/v1/bootstrap/status`、`context_memory_selection.py`、`memory_index_selector.py`、hosted/proactive 路径、**`feedling_onboarding_validate` 是否只调 `/v1/onboarding/validate` 还是有额外判断**。

**最大风险 = skill 文案边界(不是后端 gate)**:① skill 若仍暗示"先写 memory 才能 onboarding" → 和后端新逻辑冲突;② skill 若让 agent 自建 envelope → 安全边界错;③ `/onboarding/validate` 若有第三隐藏 gate → 前端/agent 看到的状态和真实后端不一致。

> **结论:边界已写进本文,Codex 可按 A' 落 Step 3 后端。**
