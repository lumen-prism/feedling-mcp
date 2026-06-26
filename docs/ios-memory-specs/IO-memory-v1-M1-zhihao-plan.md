# IO Memory v1 M1 后端落地 Plan

> 日期: 2026-06-20  
> 作者: Codex 整理 for hx/Z  
> 目标读者: zhihao / 服务端  
> 状态: M1 执行口径已确认  
> 背景: 基于 zhihao 对 `IO-memory-readside-zhihao-backend-questions.md` 的回复整理,并吸收 2026-06-20 追加确认:可以有两个接口,top-N 先用 50。

---

## 0. 目标

M1 先做 memory readside,不做完整 memory core。

本阶段目标:

- 兼容现有 `memory_moments.doc(JSONB)`。
- 不迁移历史数据。
- 不新增表。
- 不破坏 iOS Memory Garden。
- 打通 agent 可用的 `index -> fetch`。
- 为后续 `insert / supersede / commit` 留接口空间。

人话: 第一步先让 agent 能安全地“看记忆目录、取记忆正文”,不要一上来重做写入和整理。

---

## 1. M1 范围

### 必做

- `MemoryMoment -> MemoryIndexItem` adapter。
- `index()`: 返回 agent 可读的轻量目录。
- `fetch(ids)`: 根据 id 取完整正文。
- backend 预筛 top-N。
- enclave 解密候选 memory 并生成 index/fetch。
- fetch 失败结果结构: `missing_ids / unavailable_ids`。
- 旧 Memory Garden 继续正常展示。

### 暂不做

- `insert`。
- `supersede`。
- `merge`。
- `contradict`。
- `decay`。
- bucket resolve。
- route A Consumer 接入。
- eval 自动化。

人话: M1 只修“读记忆”,不碰“写记忆/整理记忆”。

---

## 2. API 位置

M1 endpoint 由 backend 对外暴露:

```text
/v1/memory/index
/v1/memory/fetch
```

调用链:

```text
agent / hosted runtime
  -> backend memory endpoint
  -> backend 做鉴权、user_id 校验、候选预筛
  -> enclave 解密 top-N
  -> enclave 生成 index / fetch result
  -> backend 返回
```

人话: 外部只找 backend,backend 再去找 enclave。backend 是前台,enclave 是保险柜。

---

## 2.1 API 草案

### `POST /v1/memory/index`

request:

```json
{
  "limit": 50,
  "include_sensitive": false
}
```

response:

```json
{
  "items": [
    {
      "id": "mem_123",
      "summary": "用户崩溃时先需要陪伴和在场感，不要立刻给行动步骤。",
      "bucket_refs": ["安抚方式"],
      "status": "active",
      "salience": "high",
      "is_open_thread": false,
      "score": 0.91,
      "is_sensitive": false
    }
  ]
}
```

默认行为:

- `limit` 不传时默认 50。
- `include_sensitive=false` 时,敏感卡默认不进 index。
- backend 不返回 `verbatim / her_quote / follow_up`。

### `POST /v1/memory/fetch`

request:

```json
{
  "ids": ["mem_123", "mem_456"],
  "include_superseded": false,
  "include_archived": false
}
```

response:

```json
{
  "memories": [
    {
      "id": "mem_123",
      "summary": "用户崩溃时先需要陪伴和在场感，不要立刻给行动步骤。",
      "verbatim": "你每次一给步骤我就觉得你不在我身边。",
      "bucket_refs": ["安抚方式"],
      "status": "active",
      "salience": "high",
      "follow_up": "类似场景先表达在场，等用户稳定后再询问是否一起处理。"
    }
  ],
  "missing_ids": [],
  "unavailable_ids": []
}
```

默认行为:

- 默认只返回 `active`。
- `superseded / archived` 默认不可 fetch。
- `local_only / 无 K_enclave / 解密失败` 进入 `unavailable_ids`。
- 跨用户 memory 按 missing 处理。

人话: `index` 是看目录,`fetch` 是打开卡片。

---

## 3. backend / enclave 分工

### backend 负责

- API 入口。
- 当前用户鉴权。
- `user_id` 强校验。
- 从 `memory_moments` 取候选。
- 用 envelope 明文字段预筛/粗排。
- 只把 top-N 候选交给 enclave。
- 返回统一 response。

### enclave 负责

- 解密 `body_ct`。
- 读取密文正文里的 `summary / verbatim / follow_up / bucket_refs`。
- 生成 `IndexEntry`。
- 生成 `FetchResult`。
- 处理解不开的 memory。

人话: backend 不能看正文,只能排序和调度;enclave 才能打开记忆内容。

---

## 3.1 backend 预筛伪代码

M1 先不做复杂召回,只做可解释的粗筛:

```text
candidate_rows =
  memory_moments
    where user_id = current_user
    where status = active
    where visibility allows agent recall
    where K_enclave exists
    where local_only != true
    order by
      is_open_thread desc,
      salience desc,
      importance desc,
      last_active desc,
      updated_at desc
    limit 50
```

然后把 `candidate_rows` 的 envelope 交给 enclave。

M1 不要求 backend 做语义相关性判断。语义判断由 enclave 解密后生成 index,再交给 agent 选。

人话: backend 只负责“先挑一批可能有用的卡”,不要在看不到正文的情况下假装懂语义。

---

## 3.2 backend -> enclave 内部调用形状

内部调用可以按 zhihao 现有后端风格落地,但语义上建议拆成两个内部动作:

```text
memory_index_from_envelopes(user_id, candidate_envelopes)
memory_fetch_from_ids(user_id, memory_ids, include_flags)
```

内部请求给 enclave 的内容:

- `user_id`
- candidate envelope 列表或 ids
- include flags
- 当前请求 trace id / request id

内部响应给 backend 的内容:

- index items 或 fetch results
- `missing_ids`
- `unavailable_ids`

硬约束:

- backend 日志不能打印 enclave 返回的 `summary / verbatim / follow_up`。
- backend 只能透传给当前请求的 agent/runtime。
- enclave 解密失败不要抛整体失败,按 item 放进 `unavailable_ids`。

人话: enclave 是保险柜工作人员,可以打开卡;backend 只是传话和收结果,不要把明文记到日志里。

---

## 4. envelope 字段建议

### 可放明文外壳

这些字段给 backend 预筛用:

```text
status
schema_version
updated_at
importance
last_active
surface_count
is_open_thread
salience
```

可选:

```text
bucket_id[]
```

但 M1 如果不做 backend 按桶过滤,`bucket_id[]` 可以先不放。

### 必须密文

```text
summary
verbatim
follow_up
bucket 真实名称
sensitive_scope 具体值
```

人话: 服务端可以知道“这张卡重要不重要、是不是 active”,但不能知道“这张卡具体写了什么”。

---

## 5. index response

M1 index 返回:

```json
{
  "id": "mem_123",
  "summary": "用户崩溃时先需要陪伴和在场感，不要立刻给行动步骤。",
  "bucket_refs": ["安抚方式"],
  "status": "active",
  "salience": "high",
  "is_open_thread": false,
  "score": 0.91,
  "is_sensitive": false
}
```

规则:

- 不返回 `verbatim`。
- 不返回 `her_quote`。
- 不返回 `sensitive_scope` 具体值。
- `her_quote` 在 adapter 里统一映射成 `verbatim`。
- 敏感内容只返回 `is_sensitive` 或 `sensitivity_class`。

人话: index 是目录,不是正文。

---

## 6. fetch response

M1 fetch 返回:

```json
{
  "memories": [
    {
      "id": "mem_123",
      "summary": "...",
      "verbatim": "...",
      "bucket_refs": ["..."],
      "status": "active",
      "salience": "high",
      "follow_up": "..."
    }
  ],
  "missing_ids": [],
  "unavailable_ids": []
}
```

规则:

- 找不到: 进 `missing_ids`。
- 解不开 / 无 `K_enclave` / `local_only`: 进 `unavailable_ids`。
- `superseded` 默认不返回。
- `archived` 默认不返回。
- 如需返回旧卡,后续加 `include_superseded=true`。
- 返回顺序保持入参 ids 顺序。
- 跨用户 memory 一律当 missing,避免暴露存在性。

人话: fetch 是打开卡片,所以权限和失败情况要清楚。

---

## 7. top-N 策略

M1 先定:

```text
top-N = 50
```

backend 先按这些字段粗排:

```text
status = active
salience
importance
last_active
is_open_thread
surface_count
updated_at
```

然后把 top 50 给 enclave。

后续如果测试发现召回漏,再改成配置项或调到 80/100。

人话: 先让 backend 挑 50 张最可能有用的卡,再让 enclave 打开看内容。

---

## 8. M1 测试清单

建议至少补这些测试:

1. 旧 `MemoryMoment` 能进入 index。
2. index response 不包含 `verbatim / her_quote / follow_up`。
3. fetch 能按 ids 返回正文。
4. fetch 返回顺序和请求 ids 顺序一致。
5. 不存在的 id 进入 `missing_ids`。
6. `local_only` / 无 `K_enclave` / 解密失败进入 `unavailable_ids`。
7. `superseded / archived` 默认不返回。
8. 跨用户 memory 按 missing 处理。
9. backend 预筛最多只交给 enclave 50 条。
10. 旧 `/v1/memory/list` / Memory Garden 展示不受影响。

人话: 不只测 happy path,还要测“不该看的看不到、不该泄露的不泄露”。

---

## 9. route A

M1 不接 route A Consumer。

但接口设计保持统一:

```text
index()
fetch(ids)
```

后续 route A 可以复用同一套 backend endpoint。

人话: 先打通 hosted/API 路线,route A 不阻塞这版。

---

## 10. 验收标准

M1 完成后需要验证:

- 现有旧 memory 不迁移也能生成 index。
- index 不包含原话。
- fetch 能取到正文。
- `local_only` / 无 `K_enclave` 不可 fetch。
- `missing_ids / unavailable_ids` 行为正确。
- 旧 Memory Garden 不受影响。
- backend 日志不出现 `summary / verbatim / follow_up`。
- agent 能按 `index -> fetch` 使用 memory。

人话: 证明“目录能看、正文能取、隐私不炸、旧 UI 不坏”。

---

## 11. 后续 M2

M1 之后再做:

- `insert`。
- `supersede`。
- 提取 / 入卡规则。
- commit 契约。
- agent recall 真接入。
- route A Consumer 接入。
- eval 自动化。

人话: M1 是读通;M2 才是写入和整理。

---

## 12. 已确认结论

2026-06-20 已和 zhihao 对齐:

1. M1 可以有两个接口: `/v1/memory/index`、`/v1/memory/fetch`。
2. backend 预筛 top-N 先按 `50` 做默认值。

人话: 后端第一步可以开始按这个 plan 做 readside 接口;后续如果召回漏,再把 top-N 变成配置项或调大。
