# IO Memory · 读写行为合同(Read/Write Contract)

> 2026-06-24 · 作者:CC · 状态:**NORMATIVE(规范,生效中)**;Codex 已核对 §7,§5.1 fetch sensitive gate 已实现但 v1 默认休眠。
> 性质:**当前版本 route A / route B 必须一致的"基础行为不变量"**。不是质量调优(那是 M3)。**本合同对读写行为规则有最终解释权;定稿/skill/prompt 冲突以本合同为准。**
> 关系:这是从 `IO-memory-子系统-spec与plan-定稿v1.md` §3/§4 抽出的**规范法条**。**定稿后续应引用本合同,不再复述这些规则,避免两个真相源。**
> 适用三处:① 后端 `feedling-mcp/backend/memory/*`、`hosted_runtime.py`;② consumer `tools/chat_resident_consumer.py`;③ `io-onboarding/skill*.md`。

---

## §0 范围

**本合同管**:何时读 memory、何时取正文、敏感何时可查、何时**不**写、create/supersede/patch/delete 各自的触发、写入的时序与"能否说已记好"。这些两条 route **现在就必须表现一致**。

**本合同不管**(明确划走):
- **判断得准不准 / 漏记误记 / eval / 提取与 recall 的 prompt 质量** → 归 **M3**(`IO-memory-M3-质量与eval-方向.md`)。
- **type 分类法 / type↔tab 映射 / 记忆格式** → 归**格式议题**(`IO-memory-格式与tab-议题-给codex看.md`),正在重做。**本合同停在 action/行为层,不冻结 type 分类。**

---

## §1 两层结构(各有不同的"绑定机制",别混)

| 层 | 是什么 | 谁强制 | 漂移风险 |
|---|---|---|---|
| **L1 服务端不变量** | action schema、规范化等价、supersede 原子/软删、anchor 门槛、敏感 gating、归档过滤、legacy 双写 | **后端代码 + conformance 测试**(机器强制) | 低(两 route 都穿同一组 HTTP 端点) |
| **L2 agent 判断规则** | 何时读/写、create vs supersede、能否说"已记好" | **prompt/skill 文本**(人来守) | **高**——活在 route B prompt 和 route A skill 两处文本里 |

> L2 才是抽合同的主要价值:**route B 的 hosted prompt 和 route A 的 onboarding skill,都从本合同 §2/§3 派生、并显式引用它。**

---

## §2 读规则(L2,两 route 一致)

- **R1 何时读(index)**:当**长期记忆可能相关**时才查 —— 用户提到过去、问到关于自己的事、需要延续上下文、纠正/更新旧事实。**普通寒暄/问候/玩笑/一次性闲聊不查。**
- **R2 取多少(fetch)**:agent 读 `index` 的 summary,**自己挑 1–3 条真正相关的 id**,只 `fetch` 这几条正文。**不要全量 fetch,不要在弱相关时 fetch。**
- **R3 没命中不编**:`index`/`fetch` 没有相关结果时,**正常回答、不要编造记忆**。
- **R4 敏感 gating(v1 默认关闭)**:本 app v1 决定默认把敏感记忆当普通记忆处理,即 `MEMORY_SENSITIVE_GATING_ENABLED` 默认 off。off 时,`include_sensitive` 不影响 readside,index/fetch/selector 都不因 `is_sensitive` 过滤。
  - flag-on 时恢复从严门禁:`include_sensitive` 默认 false;agent 不得为"多补点上下文"主动打开 sensitive;只有用户**显式请求**、且请求本身就关于该敏感主题时,才允许取敏感卡。
  - flag-on 时,`index` 默认不返回敏感卡;`fetch` 按 id 取正文时也会在 enclave 解密后过滤敏感卡,并返回 `blocked_sensitive_ids` 便于观测。
- **R5 兜底(recall)≠ 替代 agent-first**:agent 没自查时,consumer(route A)/ hosted fallback(route B)用**服务端 selector**(`/v1/memory/recall` 或进程内 `_select_context_memories_via_readside`,同一个 `select_memory_index_items`)推一小撮 baseline。**这是 floor,不是用来取代 agent 自己语义挑。** baseline 保持小;agent 仍应在需要更深时自查。

**两 route 的读分工(机制不同、行为一致,详见定稿 §3.2)**:route B 看得见 tool_calls → 条件式(没调才兜底);route A 看不见 → 无条件推小 baseline + agent 自查叠加。**一致 = "agent 挑不出来时都有兜底",不是"两条都 always 双推"。**

---

## §3 写规则(L2,两 route 一致)

**动作词分两层(别混)**:
- **agent-facing 别名**:`memory.create` / `memory.supersede` / `memory.patch` / `memory.delete`(skill / prompt 里教 agent 用这套)。
- **executor canonical action**:`memory.add` / `memory.supersede` / `memory.patch` / `memory.delete`。**`memory.create` 是 `memory.add` 的 agent-facing 别名**——执行层规范化时 `create → add`。
- route A(`_normalize_v2_action_type`)和 route B(`coerce_runtime_action`)规范化后**必须产出同一个 executor action**(见 §4,conformance 测试守)。

- **W1 何时不写**:寒暄、提问、玩笑、一次性/临时陈述、**或只是引用已有记忆**——**都不写**。不要每轮都写。
- **W2 create**:出现一条**新的、持久的事实**(宠物/人/地点/偏好/长期身份事实/关系状态等)。要 grounded、会长期成立。
- **W3 supersede**:新陈述**替换/纠正**一条已有事实时用它(换工作、改口、纠错)。**永不硬删**——旧卡软退场 + 链到新卡(后端原子执行,见 §4)。
- **W4 patch**:在**不改变卡身份**的前提下补充/修正已有卡的字段(加细节、补 follow_up)。
- **W5 delete**:**罕用**。仅用于明确错误/用户明确要求遗忘的条目。**纠正旧事实优先 supersede,不要用 delete。**
- **W6 anchor 门槛(涉及推理类卡)**:`insight` 需 ≥1 anchor;`reflection` 需 ≥2 anchor 且受频率上限(后端强制,见 §4)。(注:insight/reflection 属"agent 推理"类,受格式议题影响,本合同不冻结其分类。)
- **W7 去重**:该事实已有卡 → 用 patch/supersede,**不要 create 出重复卡**。
- **W8 时序 +「已记好」规则(重要)**:**写是回复之后才提交的(尤其 route A:consumer 在回复后才 `POST /v1/memory/actions`)。agent 在回复那一刻并不知道是否落库成功。**
  - → **不要在回复里把"已保存/已记好"当既成事实说。** 要么说成意图("我记一下""我会记住"),要么不提。
  - → 只有在确实拿到成功的 actions 响应后,才可确认已存(route A 通常看不到该响应,故默认按"说意图")。

---

## §4 服务端不变量(L1,文档化 + 链代码与测试)

这些后端已强制(除标⚠️者),**两 route 自动一致**(都走同一组 HTTP 端点)。**改这些必须同步改对应 conformance 测试。**

**写入口有两个,别混(写规则只走前者)**:
- **`POST /v1/memory/actions` = agent/runtime 统一写入口**:收 `memory.*` 动作 → 规范化(route A `_normalize_v2_action_type` / route B `coerce_runtime_action`)→ executor `_execute_memory_actions`。**§3 的写规则、A/B 一致性、conformance 都指这条。**
- **`POST /v1/memory/add` = legacy / envelope 直写入口**:直接收 envelope(密文 inner + 明文 `occurred_at/source/type`)落库,**不经 executor、不做 `memory.*` 规范化**。属遗留路径,**不在 §3 写规则约束内**,新逻辑不要往这条加。

| 不变量 | 代码 | 测试 |
|---|---|---|
| action schema + **route A/B 规范化等价**(经 `/v1/memory/actions`) | `tools/chat_resident_consumer.py:_normalize_v2_action_type` / `hosted_runtime.py:coerce_runtime_action` | `tests/test_memory_action_conformance.py`(断言两者产出同一 executor action) |
| recall = index→selector→fetch,复用同一 selector | `backend/memory/routes.py:/v1/memory/recall` + `memory_index_selector.select_memory_index_items` | `tests/test_memory_recall_route.py`、`tests/test_memory_index_selector.py` |
| readside 候选/排序/limit(默认 50 / 0=全开 / HARD_MAX) | `backend/memory_readside_core.py:readside_candidates / effective_readside_limit` | `tests/test_memory_readside_core.py`、`tests/test_memory_readside.py` |
| **敏感 gating(flagged)**:`MEMORY_SENSITIVE_GATING_ENABLED` 默认 off,off 时敏感=普通;flag-on 时 `index` 默认 `include_sensitive=false`, `fetch` 按 id 取时也默认过滤敏感正文,被挡 id 返回 `blocked_sensitive_ids`;只有显式 `include_sensitive=true` 才允许取敏感正文 | `memory_readside_config.effective_include_sensitive`;`index` 侧:`memory_index_core` + enclave `v1_memory_index`;`fetch` 侧:`memory_fetch_core` 透传 `include_sensitive` + enclave `v1_memory_fetch` 解密后过滤;selector 侧:`memory_index_selector` | `tests/test_memory_readside.py`、`tests/test_memory_readside_core.py`、`tests/test_memory_index_selector.py` |
| 归档/superseded 不返回 | `memory_service._active_memory_moments` / `memory_available`(`is_archived`/`status`/`superseded_by`) | `tests/test_memory_m2_write_loop.py` |
| supersede 软退场 + 原子(旧卡 `status=superseded`+`superseded_by`+`is_archived`,永不硬删) | `backend/memory/actions.py:_memory_supersede_action` | `tests/test_memory_m2_write_loop.py` |
| anchor 门槛(insight≥1 / reflection≥2 + cadence) | `backend/memory/service.py`(`_validate_anchor_ids` / `_reflection_time_cap_ok`) | (随写入闭环测试) |
| **legacy 双写**(`title/description/her_quote`)保 iOS Garden | `backend/memory/actions.py:_memory_inner_from_action` | `tests/test_memory_m2_write_loop.py` |

---

## §5 防漂移(合同怎么"不只是一份会过期的 md")

1. **L1**:靠上表的 conformance 测试做机器闸门。改行为 → 先改测试。
2. **L2**:**单一来源 = 本合同 §2/§3**。
   - `io-onboarding/skill*.md` 的读写章节**显式引用本合同**(skill 顶部一行指针)。
   - route B 的 hosted prompt/controller 同样以本合同为准。
   - **review checklist**(改动 memory 读写行为时必过):□ 改了 §2/§3 吗?□ route A skill 同步了吗?□ route B prompt 同步了吗?□ L1 受影响则 conformance 测试同步了吗?
3. **挂里程碑验收**:里程碑 M 的"真 agent-first smoke"(后端日志看到 agent 自调 index/fetch)= 验 §2 读规则真被执行;"持久事实两边都落卡" = 验 §3 写规则一致。

### §5.1 已完成项
- **fetch sensitive gate + tests 已补,但 v1 默认休眠**:`memory_fetch_core` 透传 `include_sensitive`;enclave `v1_memory_fetch` 在解密后按 `is_sensitive` 过滤;非 `include_sensitive=true` 不返回敏感卡正文,并返回 `blocked_sensitive_ids`。当前 v1 通过 `MEMORY_SENSITIVE_GATING_ENABLED=off` 默认把敏感当普通;翻开 flag 即恢复门禁。

---

## §6 范围外(别写进本合同)

- **type 分类法 / type↔tab / 记忆格式** → `IO-memory-格式与tab-议题-给codex看.md`(讨论中,会重做)。本合同涉及 type 处一律标"当前分类、受格式议题影响"。
- **判断质量 / eval / prompt 调优 / 漏记误记** → M3。
- 常驻/pinned 层、proactive 唤醒、A4 屏幕→记忆、MemPalace、merge/decay → 各自后置文档。

---

## §7 给 Codex 的核对点

1. §4 的代码/测试映射对得上吗?有没有我漏的不变量(尤其 enclave 边界)?**两条写入口的定性**(`/v1/memory/actions`=统一 executor 入口;`/v1/memory/add`=legacy envelope 直写、不经 executor)——准确吗?
2. §3 动作词:**agent-facing 别名 `memory.create` / executor canonical `memory.add`(create→add)** 这样表述清楚吗?其余 supersede/patch/delete 别名与 canonical 是否一致?
3. **§5.1 已完成:fetch sensitive gate + tests** —— Codex 已实现;后续改动需同步测试。
4. 本合同已设为 **normative**;请确认定稿 §3/§4 已"保留架构、删细则、改引用"(CC 已改,见定稿)。
