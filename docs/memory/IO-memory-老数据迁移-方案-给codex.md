# IO Memory · 老数据迁移方案 v2(静默/安静窗口,给 Codex review)

> 2026-06-27 · CC · 状态:收敛稿(承接上轮 Codex review,已采纳大部分 + 一处更正)。
> 范围:**只迁老用户的老记忆卡**。identity 不迁(schema 没变);新用户两路已直接产 v1。

---

## 0. 决策与边界(hx 已拍)

- **只迁老用户老记忆卡。** 老卡加密 inner 只有 `{title/description/her_quote/context/linked_dimension}`,缺 v1 的 `{summary/content三段/bucket/threads}`。
- **老卡现在能读、不崩,但降级**:enclave 解密时把老 inner 适配成 v1 形状(`summary←title`、`bucket←"未归类"`、`threads←空`、`content←description`)。所以读得到、能弱召回(语义对 title),但**按 bucket/thread 精确查会漏**——这是功能性损失,迁移修的就是它。
- **迁移方式 = 静默后台,挑用户安静窗口跑(蹭 capture lane),不阻塞不强制。** 依据:老卡降级期可接受 → 迁移不急 → 不用让用户等、也不用边聊边抢着迁。Garden 可选显"整理中 N/M"做透明度。
- **必走 LLM 派生**(bucket 归类 / threads 线索 / content 三段里的"使用提示"是语义,纯字段映射只产空壳):VPS=用户 agent、API=agent-runtime spawn 的 agent;**统一走 resident consumer 的 `call_agent`**(和落卡同路)。

---

## 1. 检测:怎么知道有老卡(**不用 card_v**)

- ⛔ **不能用 `card_v`**:clean v1 刻意不写版本号,测试把 `card_v` 和 `type/salience` 一起当 **legacy_key 断言"不该存在"**(`tests/test_memory_v1_schema.py:80`、`tests/test_memory_m2_write_loop.py:86`)。加 card_v = 违反 v1 schema 契约。
- ✅ **靠"解密后看形状"**:inner 缺 v1 字段(无 `bucket`/`threads`/`content` 三段)= 老卡。inner 加密,所以判断发生在能解密的地方(enclave / 迁移 job 内)。
- **两级**:
  1. **便宜的触发标记(per-user)**:enclave 读卡时若发现老形状 → 置 `has_legacy_cards`(或首次一次性扫一遍)。调度器只看这个便宜标记决定要不要入队,**不用每个安静窗口全解密**。
  2. **精确的逐张判断 + 迁移 job 表**:job 内解密这批 → 缺 v1 字段的就是要迁的。每张记 `memory_id + old_body_hash + status(pending/done/failed/skipped)` → **可续跑、可观测、单卡失败不影响全局**。(这是上轮 Codex 建议,采纳。)

---

## 2. 触发与执行:蹭 capture 安静窗口通道

**复用 A-full 已做好的 capture lane 基座(不是新建队列,也不是给 proactive_jobs 到处加 if):**
- typed job 基座(`backend/proactive/capture_jobs.py`):enqueue / 单飞 / 幂等 key / claim-lease / stale 回收。
- 触发 coordinator(`backend/proactive/capture_scheduler.py`):安静窗口检测(冷场 / 锁屏背景 / 轮数)。
- poll **跳过 reach-out wake gate**(关"AI主动找我"不影响迁移)。
- consumer 按 `job_kind` 分发到 `_process_*_jobs`。

**新增**:
- `job_kind=memory_migrate` + `_process_migrate_jobs` handler(照 `_process_capture_jobs` 模子)。
- **触发**:用户进安静窗口 + `has_legacy_cards` → 入队一个 batch(5-10 张)。比 Dream 急一点(尽快排干,但仍只在安静时跑)。
- **单飞**:同用户 `memory_migrate` 与 `memory_capture` 不并跑(或至少共用 `memory_lock` 串行)。

---

## 3. 写回:新增 `memory.upgrade`(原地、薄层、加锁)

- **新增内部动作 `memory.upgrade`**(+ 暴露成 HTTP action 给 VPS agent 用)。语义:**保留 `id/created_at/occurred_at/source`**,重新加密写回 `body_ct/nonce/K_user/K_enclave`,产出**干净 v1 inner(无 `type`)**。
- ⚠️ **对上轮 review 的更正**:Codex 上轮说"content_patch 会被 dispatcher 转成 supersede(`actions.py:716`)"——**在最新 `origin/test`(57b2b4f)这是错的**。`_memory_content_patch_action` **已是原地**(`item_id=memory_id` 保 id、`moments[idx]=updated`、保 `occurred_at`);716 行其实是 `_memory_supersede_action` 自己的返回块。**所以 upgrade 是薄层**(照 content_patch 的原地机制改成干净 v1 语义即可),不是从头造。
- ⚠️ **并发坑(上轮 Codex 对,重要,必须防)**:`_save_moments`(`service.py:102`)是 `db.memory_replace_all`(整表语义),`store.memory_lock` 只锁写那一下、没锁"读-改-写"整体 → 长跑迁移拿旧 list replace-all 会**冲掉用户并发新写的卡**。
- ✅ **正确修法(别用"整段进锁",那会因 LLM 慢冻结读写几秒)**:迁移写回**走 `db.memory_upsert` 单行写、不 replace-all** → 天然不删别的卡;**LLM 在锁外**,只在 `memory_lock` 内做 fresh-reload + `old_body_hash` CAS + 单行 upsert。完整时序见 **§5.5**。**不能省。**

---

## 4. 加密边界

- LLM(agent)吐**明文 v1**;**consumer 侧 `build_envelope` 重新加密后才写**(io_cli 保持 stdlib-only,加密在 consumer,沿用 D1)。
- 解密喂 LLM:经 **enclave**(双信封 K_enclave 路)。
- 全程 e2e 加密不破:LLM 看到明文(必须,才能改写),但库里存的是重新加密的密文。

---

## 5. 完整流水线(一批的循环)

```
1. 触发:用户进安静窗口(冷场/锁屏)+ has_legacy_cards → 入队 memory_migrate job
2. job 起:捞该用户的卡,挑出"老形状"的一批(5-10 张),按 job 表跳过已 done
3. 解密:这批经 enclave 解密成明文(title/description/her_quote/...)
4. 喂 LLM:handler 拼迁移 prompt(= 这几张明文 + 现有 bucket/thread 词表
          + 指令"逐张改写成 v1、别发明事实、不 merge/supersede、保 id")→ call_agent
5. LLM 返:每张老卡 → 一张 v1(bucket/threads/summary/content三段)
6. 重新加密:consumer 用 build_envelope 把明文 v1 封回密文
7. 写回(逐卡,见 §5.5 时序):每张 `memory.upgrade` 走 **`db.memory_upsert` 单行写**(保 id/occurred_at),
          写前 hash CAS;**LLM 不在锁里**。→ 标记这几张 done
8. 没迁完 → 等下个安静窗口接着来(job 表续跑);全 done → 置 user migration_done
```

**核心**:代码负责"挑老卡 + 解密 + 喂 + 加密写回",**LLM 只做中间那步"老格式→v1 翻译"**。

---

## 5.5 中间态与读写时序(⭐ 重点审,hx 点名)

**前提(已核 origin/test 57b2b4f)**:DB 有 per-card 写原语 `db.memory_upsert(uid, moment_id, occurred_at, doc)`(`db.py:927`,`ON CONFLICT DO UPDATE`,只动一行);`store.memory_lock` 是**进程内 threading.Lock**(`store.py:110`);`memory_replace_all` 现是 reconcile(但**仍按整表语义** —— 拿旧 list replace 会删掉并发新写的卡)。

**A. 读永远对(每张卡原子地"非老即新")。** 写回是单行原子,所以任一时刻每张卡要么老形态、要么 v1,**绝不半新半旧**。读路对两形态都兜底 → 混合读自洽:迁移前=降级读、迁移后=v1、LLM 派生中(未写回)=仍读到老的(正确)、一个列表里新老混=各按各形态渲染。唯一会变的是 `--bucket` 查询结果随迁移变全(召回逐渐变好,非 bug)。**没有任何一次读会崩或看到坏卡。**

**B. 写两段式,LLM 绝不在锁里:**
```
Phase 1(慢,无锁,只读):load 批 → 解密 → call_agent(LLM,几秒)→ build_envelope 加密
Phase 2(快,逐卡,进 memory_lock):
   重新读这一张 fresh
   ├ 卡没了(被删) → 跳过
   ├ body_hash ≠ Phase1 的 old_body_hash(LLM 那几秒被用户改过) → 跳过、重入队
   └ 一致 → db.memory_upsert(单行写 v1,保 id/occurred_at)
```
**用 `memory_upsert` 单行写、不 replace-all → 天然不会删/冲掉并发新卡。**

**C. 交错全枚举(均安全):**
| 场景 | 结果 | 安全 |
|---|---|---|
| 迁 A 时用户新写 D | 各写各的,D 不丢 | ✅ |
| 迁 A 时用户改/supersede A | hash CAS 不匹配 → 跳过、不覆盖用户改动 | ✅ |
| 迁 A 时用户删 A | 找不到 → 跳过、不复活 | ✅ |
| 同卡迁两次(续跑)| 形态已 v1 → 不再算老卡,跳过 | ✅ 幂等 |
| capture replace-all 把 A' 退回老 A | 下窗口形态判断重认是老卡 → 再迁一次 | ✅ 自愈、不丢 |

**兜底真理:"是否已迁"以卡的字段形态为准(job 表只是优化/可观测)** → 即使被并发退回,只是再迁一遍,id/内容全程不丢、最终一致。

**D. 唯一要 Codex 拍的:消费者并发模型。** 前台/capture/migrate 是真多线程还是按用户串行?
- 串行 → 上述 race 根本不发生,更稳。
- 真并发 → upsert+CAS+自愈已够;**可选**把现有 `_save_moments` 锁放宽到 load-modify-save 做"零退回",但改既有写路、blast radius 大,**非必须**(自愈已兜不丢)。

---

## 6. 迁移专用 prompt(不能直接复用写入 prompt)

复用 v1 写入指引的**格式**,但加迁移约束:
- 只升级**这一张**老卡,不发明新事实,**不 merge、不 supersede**(原地)。
- `her_quote` 只当原话/上下文素材,`linked_dimension` 只当 thread 候选。
- resolve-before-create:喂现有 bucket/thread 词表,让老卡落进一致的桶,别膨胀。

字段映射(种子,非硬规则):`description/title → summary + content.记忆`、`context → content.上下文`、`her_quote → content.上下文/原话依据`、`linked_dimension → threads 候选`。

---

## 7. 限速 / 并发红线(静默版,比纯并发后台轻)

- 因为**挑安静窗口跑**,不用激进 throttle/让位状态机。
- 仍要:batch 5-10;同用户 capture/migrate 不并跑;**429/额度错指数退避**(5m/15m/1h);用户回来就 checkpoint、等下个窗口续。
- 🔴 红线:**绝不丢卡、绝不冲掉用户并发新写、原地只 upgrade 不删**;改状态用合成/可恢复数据,改完恢复。

---

## 8. VPS 路径(同一套)

- VPS 的 resident consumer 也跑 `memory_migrate` job(蹭同通道),agent 做派生,走同一个 `memory.upgrade`。
- (可选)skill 里写死一句"迁移老卡必须 preserve id、别 supersede",防 agent 手滑。

---

## 9. 分阶段上线(绝不为通过牺牲功能 / 绝不误删 VPS 数据)

1. 先做 `memory.upgrade`(原地+加锁)+ 迁移 job 表 + 单批 5-10,跑通 resume / 失败 / 暂停。
2. 用 **2-3 个老用户合成数据验证**:不丢卡、不冲新写、不爆 model key、id 不变、占位符态正确。
3. 再放量;Garden 接"整理中 N/M"(可选)。

---

## 10. 要 Codex 审 / 拍的点

**已采纳你上轮 review**:不用 card_v(实锤测试断言)、迁移 job 表、新增 memory.upgrade、并发坑真实、迁移专用 prompt、VPS 也要 upgrade、分阶段验证。

**一处请在更正后确认**:content_patch 在最新 origin/test 已是原地(716 是 supersede 块),upgrade 是薄层 —— 同意吗?

**⭐ 重点审 §5.5 中间态与读写时序**:用 `db.memory_upsert` 单行写 + LLM 在锁外 + hash CAS + 形态自愈,声称"读 100% 安全、写不丢卡不冻结、最终一致"。**这套交错分析有没有漏的场景?尤其确认消费者并发模型(前台/capture/migrate 真并发还是串行),决定要不要把 `_save_moments` 锁放宽。**

**待你拍**:
1. `has_legacy_cards` 触发标记放哪(enclave 读时顺手置位 vs 首次一次性扫)?
2. `memory.upgrade` 走 content_patch 扩一个分支 vs 独立函数 `_memory_upgrade_in_place`(都用 `memory_upsert` 单行写)?
3. batch 大小 / 退避参数 / 安静窗口阈值默认值?
4. 迁移 prompt 的字段映射够不够,有没有老字段会丢语义?
