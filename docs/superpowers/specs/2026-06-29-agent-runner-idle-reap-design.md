# Agent-Runner 按需常驻(idle-reap + 懒启动)设计

> **Phase A of two.** 本 spec 只覆盖 A 期:idle-reap + 懒启动 + proactive 中央唤醒。
> B 期(并发活跃 CLI 上限 + 排队,防 OOM 尖峰)是独立子系统,另起 spec。

**Goal:** 把"每个 enabled 用户一个永久常驻 consumer"改成"按需常驻"——长时间无活动的用户的 consumer 被回收(reap),下次有工作时再懒启动,从而在相同服务器配置上承载更多注册用户、并降低后端长轮询负载。

**Architecture:** 用 `agent_runtime_instances.status` 新增 `dormant` 状态表达"已注册但已回收"。supervisor 每 tick 对超过 idle 阈值且非 in-flight 的 consumer 执行 reap;dormant 用户不再被 tick 自动 spawn,只由三类触发器经统一 `_wake(uid)` 拉起:chat 消息(推)、感知帧(推)、到期定时器(拉,scheduled_wake / dream 夜窗)。决策逻辑放在纯模块 `reaper.py` 可单测,IO/编排在 supervisor,后端只加两处小 hook。

**Tech Stack:** Python(backend + agent_runtime supervisor + tools/chat_resident_consumer),Postgres(`agent_runtime_instances` 租约表 + `chat_messages` + `scheduled_wake_v2`),现有 LISTEN/NOTIFY wake bus(`core/wake_bus.py` + `db.pg_notify`)。

## Global Constraints

- **消息零丢失(硬不变量)**:消息落库先于任何处理;唤醒只影响处理时机不影响投递;push 唤醒 + 每 tick 兜底拉取 + reply-claim 去重三重保证。详见 §6。
- **失败偏向保命**:reaper 任何不确定/异常 → 不 reap(保持常驻);wake 失败 → 下 tick 兜底重试。绝不误杀活跃用户。
- **proactive 不被延后**:capture/dream 无底层活动即 no-op;有活动(帧/消息/夜窗)立即经对应触发器唤醒。
- **默认关、暗发**:`AGENT_IDLE_REAP_ENABLED` 默认 false;关时行为与今天完全一致(全常驻)。
- **E2E 不变**:服务端只见密文;唤醒/冷启不触碰明文;consumer 冷启从持久卷续 session/记忆。
- **多 worker 安全**:dormant 状态以 DB(租约表)为准;唤醒经 `leases.acquire` 原子选主,exactly-one consumer per user。

---

## 状态机

```
            chat / 帧 / 到期定时器(经 _wake)
   dormant ─────────────────────────────────▶ running ──┐
      ▲                                                  │ status='running'
      │  chat-idle > AGENT_IDLE_THRESHOLD_SEC            │ 每 tick 续租
      │  且 非 in-flight 且 无 imminent 事件              │
      └──────────────── idle-reap ──────────────────────┘

   (enabled 但 status='dormant' 的用户:tick 的 spawn pass 跳过,不自动拉起)
```

- `running`:有活 consumer,supervisor 每 tick 续租(现状)。
- `dormant`:已 reap,无 pid,**仍在 enabled 发现集**但被 spawn pass 跳过,只等唤醒。
- 关闭/禁用(离开 roster):走现有 `release`(`status='idle'` + kill),与本特性无关。

---

## 组件 / 文件划分

依赖方向(单向):`reaper.py(纯)` ← `supervisor.py(编排/IO)` → `leases.py(DB)`;后端写 last_active + notify,supervisor 读 last_active + LISTEN。app.py 只装配,不改。

### 1. `backend/agent_runtime/reaper.py`(🆕,纯函数,无 IO)
休眠/唤醒**决策**,完全可单测。

- `should_reap(*, last_active_at: float, now: float, idle_sec: float, in_flight: bool) -> bool`
  返回 `(not in_flight) and (now - last_active_at > idle_sec)`。`last_active_at<=0`(未知)→ 不 reap。
- `wakes_due(*, scheduled_due_uids: set[str], dream_due_uids: set[str], pending_msg_uids: set[str]) -> set[str]`
  合并三类"该唤醒的 dormant 用户"为一个集合(供 supervisor 调用 `_wake`)。纯集合运算。

### 2. `backend/agent_runtime/leases.py`(改)
- 新增并接受 `status='dormant'`。
- `mark_dormant(user_id: str, lease_owner: str, *, now: float | None = None) -> None`
  `UPDATE … SET status='dormant', pid=NULL, lease_owner=NULL, lease_expires_at=NULL, last_active_at=to_timestamp(now), updated_at=now()`。与 `release`(`status='idle'`)的区别仅在 status 标签——`dormant` = "已注册 parked,勿自动 spawn"。
- `list_dormant(*, now=None) -> list[dict]`:供兜底/调试。

### 3. `backend/agent_runtime/supervisor.py`(改)
- `Supervisor.tick(roster)` 增加:
  - **reap pass**:对每个活 child,读其 `last_active_at`(来自租约行),`reaper.should_reap(...)` 为真 → `kill_fn(pid)` + `leases.mark_dormant(uid, owner)` + 移出 `self.children`。`in_flight` 判定见 §6。
  - **spawn pass 跳过 dormant**:构造"该 spawn 的 enabled 用户"集合时,排除租约 `status='dormant'` 的用户。
- `Supervisor._wake(user_id, entry) -> None`(🆕,**唯一 spawn 路径**):清 dormant → `leases.acquire`(原子,失败=别的 worker 已拉起,返回)→ `spawn_fn` → `_write_token` → 记入 `self.children`。chat/帧/定时三类触发最终都调它。
- **后台 LISTEN 线程**:`listen_connection()` 订阅 wake bus 的 `chat` / `perception` channel;收到 `uid` 且该用户 dormant+enabled → `_wake`。
- **每 tick 中央拉取**:`scheduled_wake` 的 `claim_due(now)`(跨用户)+ dream 夜窗 due + **兜底**"dormant 但有未回 user 消息"的用户 → 经 `reaper.wakes_due` 合并 → 逐个 `_wake`。

### 4. 后端 chat/append 路径(改,小)— 活动真相源
- `core/store.append_chat`(或其调用点)在写入 user/assistant 消息后,bump `agent_runtime_instances.last_active_at = now()`(该用户行)。这同时修复既有"last_active 不按 turn 回写"的 bug。consumer 无直连 DB,故由后端写。

### 5. 后端 感知帧 ingest 路径(改,小)— 帧到达推唤醒
- 帧入库处(写 `frame_envelopes`)调 `wake_bus.notify("perception", user_id)`,使 dormant 用户在有新帧时被唤醒跑 capture。(实现时定位确切入口。)

### 6. 后端 send wedge-guard(改,小)— dormant 不误 503
- `/v1/model_api/chat/send` 的 wedge-guard:目标用户 `status='dormant'` 时**不**返回 `hosting_runtime_unavailable`——消息照常落库 + notify 触发唤醒,返回现有 `202 processing`。

### 7. `tools/chat_resident_consumer.py`(基本不动)
- 新 spawn 的 consumer 首轮 loop 自然跑 chat poll + capture/dream due 检查,即"冷启补跑 B",无需新增 catch-up 代码。

### 8. 配置 / 开关
- `AGENT_IDLE_REAP_ENABLED`(默认 `false`)。
- `AGENT_IDLE_THRESHOLD_SEC`(默认 `1080` = 18min)。

---

## 触发器(四类工作源)

dormant 用户只在"所有源都安静"的真空窗里休眠;任一源有活立即唤醒:

| 工作源 | 方式 | 机制 |
|---|---|---|
| chat 消息 | 推 | `append_chat` → `wake_bus.notify("chat", uid)` → LISTEN → `_wake` |
| 感知帧 | 推 | 帧入库 → `wake_bus.notify("perception", uid)` → LISTEN → `_wake` |
| scheduled_wake 到点 | 拉 | tick → `scheduled_wake_v2.claim_due(now)` → `_wake` |
| dream 夜间窗 | 拉 | tick → 用户进入夜窗 **且有未消化的新轮次** → `_wake`(无新轮次=无可 dream,不唤醒,符合 no-op 原则) |

capture/dream 无底层活动即 no-op;有活动时上述触发器即时唤醒,consumer 上线先补跑 due 检查,故 **B 不被延后**。

---

## §6 消息零丢失(硬不变量,实现必须满足)

1. **落库先于处理**:`append_chat` 把消息写进 `chat_messages`(Postgres,durable),与 consumer 状态无关。consumer 仅是处理器。
2. **唤醒只影响时机**:dormant 时到达的消息已在库;`_wake` 后 consumer `poll(since=last_ts)` 取回。
3. **push + pull 双保险**:notify 丢失时,每 tick 兜底查"dormant 且有未回 user 消息"的用户 → `_wake`。最坏唤醒延迟 = 1 个 tick(~15s)。
4. **不 mid-turn 误杀**:`append_chat` 即 bump `last_active_at` → 刚有消息的用户 last_active 新鲜,`should_reap` 必跳过;阈值 18min ≫ 单轮上限(CLI timeout 120s),turn 不可能跨阈值。
5. **at-least-once + 去重**:即便极端中途被杀,user 消息已落库且未标 replied;reply-claim(`reply_claimed_by` / `reply_claim_expires_at`)使重生 consumer 重新认领处理,最多重处理、不丢、不双回。

**结论**:reap/懒启动改变的是"何时处理"(冷启加几秒延迟),从不改变"是否投递"。

---

## 竞态与安全

| 风险 | 处理 |
|---|---|
| mid-turn 被误 reap | last_active 收到消息即 bump;阈值 ≫ 单轮上限 |
| reap 与消息同时 | 消息总先落库;先存后 reap→不 reap,先 reap 后存→notify/兜底拉起 |
| 多 worker 双唤醒 | `_wake` 经 `leases.acquire` 原子,只一个成功 |
| notify 丢失 | 每 tick 兜底拉取(chat 未回 + due 定时器),最坏 ~15s 内唤醒 |
| 不确定时取向 | reaper 异常 → 不 reap;wake 失败 → 下 tick 重试 |
| 冷启上下文 | session/记忆在持久卷,唤醒后续上,E2E 不丢 |
| supervisor 重启 | dormant 在 DB → 重启不把全员重拉成常驻 |

---

## 测试策略

- **纯单测(`reaper.py`)**:`should_reap` 阈值边界 / `in_flight` 守卫 / `last_active<=0` 不 reap;`wakes_due` 三集合合并。
- **supervisor 集成(注入 fake spawn/kill/now)**:超阈值 child 被 reap + `mark_dormant`;spawn pass 跳过 dormant;`_wake` 拉起 dormant;兜底扫描唤醒"有未回消息"的 dormant 用户;多 worker `acquire` 只一个 spawn。
- **`leases`**:`mark_dormant` 写出 `status='dormant'`;discovery/spawn pass 视 dormant 为 parked。
- **后端**:`append_chat` bump `last_active_at`;帧 ingest 触发 `wake_bus.notify`;dormant 用户 send 返回 202 而非 503。
- **端到端脚本**(测试栈):enabled 用户 idle 超阈值 → 被 reap(DB status=dormant、无 pid)→ 发消息 → 被唤醒 → 收到真实回复;scheduled_wake 到点唤醒并 fire。

---

## 灰度上线

1. 暗发:`AGENT_IDLE_REAP_ENABLED=false` 合入,行为与今天一致。
2. test 这台打开,观测:agent-runner 内存下降、后端长轮询连接数下降、proactive(提醒/dream)照常、**flap 计数**(每用户 reap→wake 频次;过高调大阈值)、无漏唤醒(对比消息回复延迟分布)。
3. 稳定后 prod 打开。
4. 可观测性:reap/wake 事件日志带 uid + 原因;dormant/running 计数;兜底唤醒命中计数(>0 说明有 notify 丢失,需排查)。

---

## 不在本期范围(B 期)

- 并发活跃 CLI 进程上限 + 排队/背压(防 OOM 尖峰)。idle-reap 削"底噪"(总注册数 × 常驻成本),B 期削"尖峰"(并发活跃 × CLI 内存),二者互补。
