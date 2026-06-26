# IO MemoryCard v1 工程契约草案（Codex 版）

> 日期：2026-06-19  
> 作者：Codex draft for hx/Z  
> 版本说明：这是 Codex 先行起草的 spec，用于后续和 Claude / 其他 agent / zhihao 的版本做交叉 review，不代表最终定稿。  
> 用途：先给 Seven / Claude / zhihao 对齐工程方向。本文不是最终后端实现方案，也不是 eval 题库；它只定义跳过 eval 后，IO 记忆系统下一步可落地的 MemoryCard v1 契约。

---

## 0. 一句话

当前线上已有 `MemoryMoment`：能自动写、加密存、在 Memory Garden 展示、在 enclave 内做关键词召回。

下一步不重写系统，也不先做 eval，而是在现有 `memory_moments.doc(JSONB)` 加密信封上定义一层 **MemoryCard v1 契约**：让同一条记忆既能继续给用户看，也能被 agent 以 `index -> fetch` 的方式稳定使用。

---

## 1. 当前线上现状

### 1.1 现在存储长什么样

线上表是：

```text
memory_moments
- user_id
- moment_id
- occurred_at
- doc JSONB
```

`doc` 是一个加密信封。服务端能看到外壳字段，正文在 `body_ct` 里。

```json
{
  "id": "mem_123",
  "type": "quote",
  "occurred_at": "2026-06-19T00:00:00Z",
  "created_at": "2026-06-19T00:01:00Z",
  "source": "chat",
  "visibility": "shared",
  "owner_user_id": "user_abc",
  "anchor_memory_ids": [],
  "v": 1,
  "body_ct": "encrypted-body",
  "nonce": "nonce",
  "K_user": "wrapped-key-for-user",
  "K_enclave": "wrapped-key-for-enclave"
}
```

解密后的正文现在更接近 iOS 展示模型 `MemoryMoment`：

```json
{
  "id": "mem_123",
  "type": "quote",
  "title": "不要先给建议",
  "description": "用户不喜欢在难过时被立刻给建议。",
  "her_quote": "你每次一给步骤我就觉得你不在我身边。",
  "context": "一次情绪崩溃的聊天",
  "linked_dimension": "陪伴方式",
  "occurred_at": "2026-06-19T00:00:00Z",
  "created_at": "2026-06-19T00:01:00Z"
}
```

### 1.2 现在用户看到什么

iOS Memory Garden 目前按 `type` 分三组：

```text
moment / quote        -> Story
fact / event          -> About me
insight / reflection  -> TA Thinking
```

这套结构对“展示”是够用的：用户能看到故事、事实、洞察。

但它对“agent 稳定使用记忆”还不够，因为它缺少清晰的 index、敏感浮现策略、状态生命周期和 recall 契约。

### 1.3 现在 recall 怎么跑

当前 recall 不是明确的 `index -> fetch`，而是：

```text
用户发消息
  -> enclave 向 backend 拉 memory list
  -> enclave 解密正文
  -> context_memory_selection 做关键词/字符匹配
  -> 选出若干张记忆
  -> 拼进 prompt
  -> 模型回答
```

这能跑，但问题是：

1. 关键词召回不稳定，容易漏掉语义相近但词不同的场景。
2. 没有一个 agent 可读的轻量 index，让 agent 先判断要不要 fetch。
3. 记忆正文偏展示文案，不一定适合 agent 判断“这条记忆什么时候该用”。
4. 敏感内容没有稳定字段描述“可不可以主动浮现”。

---

## 2. 本轮目标

本轮目标不是重写 memory 系统，而是定义一层向前兼容的工程契约。

### 2.1 要做

1. 定义 `MemoryCard v1`：记忆正文的目标结构。
2. 定义 `MemoryIndexItem`：agent recall 时先看到的轻量摘要。
3. 定义 `MemoryFetchResult`：agent 选中后 fetch 到的完整正文。
4. 定义 `MemoryMoment -> MemoryCard v1` adapter：让旧数据先能进入新结构。
5. 明确哪些字段明文、哪些字段密文，供 zhihao 判断后端可行性。

### 2.2 不做

1. 不改数据库表结构。
2. 不迁移历史 memory。
3. 不先引入 embedding / vector search。
4. 不先做完整 eval。
5. 不先做 `supersede / merge / contradict` 的完整写侧状态机。
6. 不先解决 route A 的完整收口。

---

## 3. MemoryCard v1

### 3.1 设计原则

`MemoryCard v1` 要同时服务三类使用者：

1. **用户**：能在 Garden 里看懂、编辑、删除。
2. **agent**：能判断什么时候该用、怎么用、什么时候不能主动提。
3. **后端/enclave**：能安全存取、做权限控制、生成 index/fetch 响应。

所以它不是单纯展示卡，也不是纯检索文档。

### 3.2 建议结构

```json
{
  "schema": "io.memory_card.v1",
  "id": "mem_123",
  "summary": "用户崩溃时优先需要陪伴和确认在场，不喜欢立刻收到行动步骤。",
  "verbatim": "用户说过：崩溃时不要先给步骤，否则会觉得 AI 不在身边；希望先被抱抱、被确认“我在”。",
  "bucket_refs": ["安抚方式", "情绪崩溃", "亲密陪伴"],
  "importance": "high",
  "status": "active",
  "is_open_thread": false,
  "follow_up": null,
  "sensitive_scope": null,
  "surface_policy": "safe_to_use_when_relevant",
  "source_type": "quote",
  "provenance": {
    "source": "chat",
    "message_span": ["msg_001", "msg_003"]
  },
  "created_at": "2026-06-19T00:00:00Z",
  "last_active_at": "2026-06-19T00:00:00Z"
}
```

### 3.3 字段解释

| 字段 | 作用 | 用户体验含义 |
|---|---|---|
| `summary` | 一句话解释这张记忆 | Garden 标题/摘要，也给 agent 判断 |
| `verbatim` | 原话片段或有界 span 快照 | 保留关系语气，不只存冷摘要 |
| `bucket_refs` | 所属记忆桶 | 让用户和 agent 都知道归类 |
| `importance` | 重要性 | 影响排序、召回优先级、衰减 |
| `status` | 生命周期 | active / superseded / contradicted / archived |
| `is_open_thread` | 是否是未闭合议题 | 未闭合的关系线索不要过早沉底 |
| `follow_up` | 下次遇到类似场景怎么处理 | 给 agent 使用记忆的操作提示 |
| `sensitive_scope` | 敏感类型 | 隐私、创伤、成人亲密、身份信息等 |
| `surface_policy` | 浮现策略 | 是否能主动提，还是只能用户主动问时用 |
| `source_type` | 兼容旧 `MemoryMoment.type` | 保留 Story/About me/TA Thinking 的显示逻辑 |
| `provenance` | 来源 | 方便审计和用户理解这条记忆从哪来 |

### 3.4 枚举建议

```json
{
  "importance": ["low", "medium", "high", "critical"],
  "status": ["active", "superseded", "contradicted", "archived"],
  "sensitive_scope": [
    null,
    "adult_boundary",
    "identity",
    "trauma",
    "relationship_boundary",
    "privacy_preference"
  ],
  "surface_policy": [
    "safe_to_use_when_relevant",
    "do_not_surface_unprompted",
    "only_when_user_initiates",
    "local_only"
  ]
}
```

---

## 4. MemoryIndexItem

### 4.1 用途

`MemoryIndexItem` 是 agent 先看到的“目录项”。它不是完整正文。

目标是让 agent 能快速判断：

1. 这条记忆和当前用户问题是否相关。
2. 是否需要 fetch 正文。
3. 是否有敏感限制。

### 4.2 建议结构

```json
{
  "memory_id": "mem_123",
  "bucket": "安抚方式",
  "summary": "用户崩溃时先需要陪伴和在场感，不要立刻给行动步骤。",
  "status": "active",
  "importance": "high",
  "sensitive_scope": null,
  "surface_policy": "safe_to_use_when_relevant",
  "last_active_at": "2026-06-19T00:00:00Z"
}
```

### 4.3 生成规则

第一版 index 不需要新表，不需要明文持久化。

建议路径：

```text
backend 返回 encrypted memory list
  -> enclave 解密 MemoryMoment / MemoryCard
  -> adapter 转成 MemoryCard v1
  -> enclave 生成 MemoryIndexItem[]
  -> 返回给 agent/runtime
```

这样服务端不需要理解 `summary` 的语义，也不需要看到明文摘要。

---

## 5. MemoryFetchResult

### 5.1 用途

agent 从 index 判断某条记忆相关后，再 fetch 正文。

`fetch(memory_id)` 返回完整卡片，但仍要遵守 visibility 和 sensitive policy。

### 5.2 建议结构

```json
{
  "memory": {
    "schema": "io.memory_card.v1",
    "id": "mem_123",
    "summary": "用户崩溃时优先需要陪伴和确认在场，不喜欢立刻收到行动步骤。",
    "verbatim": "用户说过：崩溃时不要先给步骤，否则会觉得 AI 不在身边；希望先被抱抱、被确认“我在”。",
    "bucket_refs": ["安抚方式", "情绪崩溃", "亲密陪伴"],
    "importance": "high",
    "status": "active",
    "is_open_thread": false,
    "follow_up": null,
    "sensitive_scope": null,
    "surface_policy": "safe_to_use_when_relevant",
    "source_type": "quote",
    "provenance": {
      "source": "chat",
      "message_span": ["msg_001", "msg_003"]
    },
    "created_at": "2026-06-19T00:00:00Z",
    "last_active_at": "2026-06-19T00:00:00Z"
  }
}
```

### 5.3 fetch 边界

1. `local_only` 不应返回给 agent，只给用户 Garden 看。
2. `do_not_surface_unprompted` 可以 fetch，但 agent 不能主动在无关场景提起。
3. `only_when_user_initiates` 只有用户主动提到该类话题时才允许 fetch 或使用。
4. `archived` 默认不进 index，但可以在用户显式搜索/查看 Garden 时出现。

---

## 6. 旧 MemoryMoment Adapter

### 6.1 为什么需要 adapter

线上已经有旧数据，不能要求后端先迁移历史 memory。

所以第一版应做一层兼容转换：

```text
MemoryMoment -> MemoryCard v1-like object -> MemoryIndexItem / FetchResult
```

### 6.2 映射规则

旧数据：

```json
{
  "id": "mem_123",
  "type": "quote",
  "title": "不要先给建议",
  "description": "用户不喜欢在难过时被立刻给建议。",
  "her_quote": "你每次一给步骤我就觉得你不在我身边。",
  "context": "一次情绪崩溃的聊天",
  "linked_dimension": "陪伴方式"
}
```

映射成：

```json
{
  "schema": "io.memory_card.v1",
  "id": "mem_123",
  "summary": "用户不喜欢在难过时被立刻给建议。",
  "verbatim": "你每次一给步骤我就觉得你不在我身边。",
  "bucket_refs": ["陪伴方式"],
  "importance": "medium",
  "status": "active",
  "is_open_thread": false,
  "follow_up": null,
  "sensitive_scope": null,
  "surface_policy": "safe_to_use_when_relevant",
  "source_type": "quote",
  "provenance": {
    "source": "legacy_memory_moment",
    "message_span": []
  }
}
```

### 6.3 默认值

| MemoryCard 字段 | 旧数据来源 | 默认值 |
|---|---|---|
| `summary` | `description` 优先，空则用 `title` | 空字符串 |
| `verbatim` | `her_quote` | null |
| `bucket_refs` | `linked_dimension` / `context` | `["uncategorized"]` |
| `importance` | 无 | `medium` |
| `status` | 无 | `active` |
| `is_open_thread` | 无 | `false` |
| `sensitive_scope` | 无 | null |
| `surface_policy` | 无 | `safe_to_use_when_relevant` |
| `source_type` | `type` | `unknown` |

---

## 7. 明文 / 密文边界

### 7.1 建议原则

服务端需要 act on 的字段可以明文；带用户语义、隐私、关系内容的字段应放进密文 body。

### 7.2 建议明文字段

```json
{
  "id": "mem_123",
  "type": "quote",
  "status": "active",
  "visibility": "shared",
  "owner_user_id": "user_abc",
  "occurred_at": "2026-06-19T00:00:00Z",
  "created_at": "2026-06-19T00:01:00Z",
  "updated_at": "2026-06-19T00:02:00Z",
  "source": "chat",
  "anchor_memory_ids": []
}
```

### 7.3 建议密文字段

```json
{
  "summary": "用户崩溃时优先需要陪伴和确认在场，不喜欢立刻收到行动步骤。",
  "verbatim": "用户说过：崩溃时不要先给步骤，否则会觉得 AI 不在身边。",
  "bucket_refs": ["安抚方式"],
  "follow_up": "类似场景先陪伴，不要立刻给步骤。",
  "sensitive_scope": null,
  "surface_policy": "safe_to_use_when_relevant",
  "provenance": {
    "source": "chat",
    "message_span": ["msg_001", "msg_003"]
  }
}
```

### 7.4 需要 zhihao 判断

`sensitive_scope` 和 `surface_policy` 是否要明文，是本 spec 最需要后端判断的点。

倾向：

1. 如果 backend 需要在不解密时过滤敏感卡，可以明文放一个粗粒度 `sensitivity_class`。
2. 具体 `sensitive_scope` 和 `surface_policy` 仍放密文，避免服务端看到用户具体敏感类型。

---

## 8. 本轮接口建议

### 8.1 index

```text
GET /v1/memory/index
```

返回：

```json
{
  "items": [
    {
      "memory_id": "mem_123",
      "bucket": "安抚方式",
      "summary": "用户崩溃时先需要陪伴和在场感，不要立刻给行动步骤。",
      "status": "active",
      "importance": "high",
      "sensitive_scope": null,
      "surface_policy": "safe_to_use_when_relevant",
      "last_active_at": "2026-06-19T00:00:00Z"
    }
  ]
}
```

第一版实现可以不新增公网 API，也可以是 enclave/runtime 内部函数。接口名只是契约表达。

### 8.2 fetch

```text
GET /v1/memory/fetch?id=mem_123
```

返回：

```json
{
  "memory": {
    "schema": "io.memory_card.v1",
    "id": "mem_123",
    "summary": "用户崩溃时优先需要陪伴和确认在场，不喜欢立刻收到行动步骤。",
    "verbatim": "用户说过：崩溃时不要先给步骤，否则会觉得 AI 不在身边。",
    "bucket_refs": ["安抚方式", "情绪崩溃"],
    "importance": "high",
    "status": "active",
    "surface_policy": "safe_to_use_when_relevant"
  }
}
```

### 8.3 本轮暂不定义 commit

`commit(insert/supersede/merge/contradict)` 是下一阶段。

原因：

1. 写侧涉及状态机、并发、bucket resolve/create、用户确认，范围更大。
2. 读侧 index/fetch 能先产出工程价值。
3. 旧数据 adapter 可先验证 MemoryCard v1 是否足够表达现有记忆。

---

## 9. 和 zhihao 对齐清单

### 必须确认

1. **继续用 `memory_moments.doc(JSONB)` 是否可接受？**  
   本 spec 倾向不改表，只升级 body schema 和 enclave adapter。

2. **`MemoryCard v1` 放在 `body_ct` 里是否可行？**  
   目标是让 summary/verbatim/bucket_refs 等语义内容继续密文落盘。

3. **`index()` 应该在哪生成？**  
   倾向：enclave 解密后生成，不在 backend 明文存 index summary。

4. **`fetch(memory_id)` 应该走 backend 还是 enclave 中介？**  
   倾向：走 enclave，因为 fetch 返回明文正文。

5. **敏感策略字段是否需要后端明文参与过滤？**  
   如果需要，是否只放粗粒度 sensitivity class 明文。

6. **旧 `MemoryMoment` 是否先 adapter，不做历史迁移？**  
   倾向：先 adapter，等 v1 写入稳定后再考虑迁移。

### 暂不要求本轮解决

1. route A 完整收口。
2. embedding / vector search。
3. 完整 commit 状态机。
4. 跨卡整理和梦境巩固。
5. 历史 memory 全量迁移。

---

## 10. 建议落地阶段

### Phase 1：契约和 adapter

目标：不改线上存储，先让现有 `MemoryMoment` 能被看成 `MemoryCard v1`。

产出：

1. `MemoryCard v1` 类型定义。
2. `MemoryMoment -> MemoryCard` adapter。
3. `MemoryCard -> MemoryIndexItem` 转换。

### Phase 2：index/fetch 读侧

目标：让 runtime/agent 能走 `index -> fetch`。

产出：

1. 内部 `index()`。
2. 内部 `fetch(memory_id)`。
3. 规则：local_only 不给 agent；archived 默认不进 index。

### Phase 3：新写入按 v1 body 写

目标：新 memory 不再只写展示型 `title/description/her_quote`，而是写 `MemoryCard v1` body。

产出：

1. capture prompt 输出 v1 card。
2. iOS Garden 兼容 v1 card 展示。
3. 老数据继续 adapter。

### Phase 4：写侧状态机

目标：再引入 `insert/supersede/merge/contradict`。

产出：

1. commit op 契约。
2. per-user 写入串行策略。
3. bucket resolve/create 策略。
4. replace_scrub 等敏感替换能力。

---

## 11. 成功标准

第一版成功不以“最终记忆质量大幅提升”为标准，而以工程接口是否跑通为标准。

验收标准：

1. 旧 `MemoryMoment` 可以稳定转换成 `MemoryCard v1`。
2. 任意一条 card 可以生成 `MemoryIndexItem`。
3. agent/runtime 可以先拿 index，再按 id fetch 正文。
4. `local_only` / `archived` / sensitive policy 至少有明确过滤规则。
5. 不需要改数据库表。
6. 不影响现有 iOS Memory Garden 展示。

---

## 12. 我对方案的判断

这条路线的核心优点是：**先在现有线上系统外面加一层结构化契约，而不是推翻重做。**

它让我们获得三个直接收益：

1. 旧数据可用：现有 Memory Garden 不被破坏。
2. 工程风险低：不先做迁移、不改表、不动 route A。
3. 后续空间清楚：eval、写侧状态机、整理、向量召回都可以接在这层契约之后。

真正需要团队拍板的不是“要不要 MemoryCard”，而是：

1. index/fetch 由谁提供。
2. 哪些字段明文。
3. 第一版是否只读侧落地。
