# IO Memory Readside / Core v1 后端边界确认

> 日期: 2026-06-20  
> 作者: Codex 整理 for hx/Z  
> 目标读者: zhihao / 服务端  
> 状态: 待 zhihao 回复  
> 关联主文档: `IO-memory-core-v1-联合工程spec.md`

---

## 1. 背景

我们现在在做 IO 记忆系统 v1 的工程落地。当前主线不是先做 eval,也不是先改 UI,而是先把 memory 的底层读写契约跑通。

本轮目标是先做一个最小可测版本:

- 兼容现有 `memory_moments.doc(JSONB)` 和加密 envelope。
- 不迁移历史数据。
- 不破坏当前 iOS Memory Garden 展示。
- 先实现 readside: `index -> fetch`。
- 后续再实现完整 `commit / supersede / merge / decay`。

人话: 先让 agent 能像翻目录一样看记忆摘要,命中后再取正文,不是一上来重做整个记忆系统。

---

## 2. 当前 hx/Z 这边已经定下的方向

- 现有 `MemoryMoment` 继续保留,作为线上兼容层。
- 新增 `MemoryIndexItem`,给 agent 看轻量摘要。
- 新增 `MemoryFetchResult`,给 agent 命中后取完整正文。
- `summary / verbatim / bucket_refs / status / salience / sensitive_scope` 是 MemoryCard v1 的核心字段。
- 敏感/XP/亲密内容优先记录"边界和使用条件",不记录露骨细节。
- 旧卡先通过 adapter 映射到新结构,不做历史迁移。

---

## 3. 需要 zhihao 回答的问题

### Q1. `index()` 应该落在哪里?

推荐默认: 先在 enclave 内实时解密生成 index。

请确认:

- A. 放在 enclave 内,实时解密 `body_ct` 后生成 index。
- B. 放在 backend,但 backend 只能处理 envelope 外壳字段。
- C. 做缓存: enclave 生成 index 后写回某个缓存字段或表。

我倾向 A。

原因: 当前正文都在密文里,summary/title/description 也在密文正文中;backend 不应该直接看到明文。

### Q2. 第一版是否接受“不新增表,只扩 JSONB 字段”?

推荐默认: 第一版不新增表。

请确认:

- 是否可以继续把新字段放进 `memory_moments.doc(JSONB)` 的密文正文里。
- 是否允许 envelope 外壳增加少量非敏感字段,例如 `status`、`schema_version`、`updated_at`。
- 哪些字段绝对不能放 envelope 明文外壳。

人话: 我们想先少动数据库,尽量在旧盒子里放新卡片格式。

### Q3. `MemoryIndexItem` 返回给 agent 时,可以包含哪些字段?

建议第一版 index 返回:

```json
{
  "id": "mem_123",
  "summary": "用户崩溃时先需要陪伴和在场感，不要立刻给行动步骤。",
  "bucket_refs": ["安抚方式"],
  "status": "active",
  "salience": "high",
  "is_open_thread": false,
  "score": 0.91
}
```

请确认:

- `summary` 是否可以返回给 agent。
- `bucket_refs` 是否可以返回给 agent。
- `sensitive_scope` 是否应该返回,还是只作为过滤逻辑内部使用。
- `verbatim / her_quote` 是否必须只在 `fetch()` 里返回。

我倾向: index 不返回原话,只返回摘要和状态;正文只在 fetch 返回。

### Q4. `fetch(ids)` 应该怎么做权限和失败处理?

请确认第一版规则:

- 只允许 fetch 当前用户自己的 memory。
- 找不到的 id: 跳过还是返回 error item。
- `local_only` / 没有 `K_enclave` 的记忆: 是否直接不可 fetch。
- archived / deleted / superseded 的记忆: 默认不返回,还是需要显式 include。

推荐默认:

- 默认只返回 active。
- 找不到返回 `missing_ids`。
- enclave 解不开的返回 `unavailable_ids`。
- superseded 只有显式请求时返回。

### Q5. route A 能不能调用同一套 `index/fetch`?

当前已知问题: route A 的 Consumer 现在主要转发聊天和 memory action,不一定使用 IO 卡库做 recall。

请确认:

- route A 的 agent 是否能调用同一套 `index/fetch`。
- 如果不能,第一版是不是只覆盖 hosted/API route。
- Consumer 是否需要新增调用入口,还是后端/MCP 暴露即可。

我倾向: 接口设计上统一,但第一版测试先覆盖 hosted/API route。

### Q6. 第一版是否先不做 `commit`?

推荐第一版只做 readside:

- `MemoryMoment -> MemoryIndexItem`
- `index()`
- `fetch(ids)`
- 本地/测试环境可验证

暂不做:

- insert
- supersede
- merge
- contradict
- decay
- bucket resolve

请确认这个切法是否适合后端排期。

人话: 先把“读记忆”修顺,别一上来同时做写入、整理、衰减、合并。

---

## 4. 希望 zhihao 的回答格式

请按下面格式回:

```md
## Q1 index 位置
选择: A / B / C
原因:
后端建议:

## Q2 JSONB / 表结构
选择:
哪些字段可明文:
哪些字段必须密文:

## Q3 index 字段
允许返回:
不允许返回:
需要改名的字段:

## Q4 fetch 规则
找不到:
解不开:
superseded:
archived:

## Q5 route A
第一版覆盖:
后续怎么接:

## Q6 第一版范围
是否同意 readside first:
建议补充:
```

---

## 5. zhihao 回完后的执行计划

- 根据 zhihao 的回答更新 `IO-memory-core-v1-联合工程spec.md`。
- 把本地 demo 从“假数据样机”升级成“贴近后端接口形状”的测试 demo。
- 如果后端边界明确,再进入 `feedling-mcp` 做最小 readside 接口方案。
- 本地验证通过后,再考虑测试环境联调。

---

## 6. 验收标准

第一阶段成功不看 UI,而看接口和数据流:

- 给一组现有 memory,能生成稳定 index。
- agent 可用 id fetch 到完整正文。
- 不破坏现有 Memory Garden。
- 不泄露不该明文暴露的敏感字段。
- 对旧数据不迁移也能兼容。

人话: 先证明“目录能看、正文能取、旧卡能用、隐私不炸”。
