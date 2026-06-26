# IO Memory Core Local Demo 说明

> 日期: 2026-06-20  
> 分支: `feat/memory-core-local-demo`  
> 状态: 本地沙盘 demo,不接真实后端、不改线上数据  

---

## 一句话

这个 demo 已经不只是 readside adapter,而是一个本地版最小 memory core 沙盘。

它演示 5 件事:

1. 旧 `MemoryMoment` 怎么适配成 `MemoryCard v1`。
2. agent 怎么先看 `index` 目录。
3. agent 选中后怎么 `fetch` 正文。
4. 新记忆怎么 `insert` 进入卡库。
5. 新理解怎么 `supersede` 旧理解,旧卡保留但默认不再召回。

人话: 它不是后端上线功能,但已经能证明“记忆从写入到被使用”的最小闭环长什么样。

---

## 当前 demo 覆盖的最小上线范围

| 能力 | 是否覆盖 | 人话 |
|---|---|---|
| `MemoryCard v1` | 是 | 新记忆卡的目标结构 |
| `index` | 是 | 给 agent 看的目录,不含原话 |
| `fetch` | 是 | agent 选中后拿正文 |
| `insert` | 是 | 新增一条记忆 |
| `supersede` | 是 | 新理解替代旧理解 |
| `merge` | 否 | 重复卡合并,本轮暂缓 |
| `contradict` | 否 | 冲突卡标记,本轮暂缓 |
| `decay` | 否 | 权重衰减,本轮暂缓 |
| 真实加密/enclave/DB | 否 | 等 zhihao 确认后接后端 |

---

## 怎么运行

在仓库根目录运行:

```bash
python3 -m tools.memory_readside_demo.demo
```

你会看到这些 JSON 段:

1. `legacy_memory_moment`: 当前旧 Memory Garden 卡长什么样。
2. `legacy_card_v1`: 旧卡读取时如何变成 `MemoryCard v1`。
3. `initial_readside.index`: agent 第一眼看到的目录。
4. `initial_readside.fetch`: agent 选中后拿到的正文。
5. `writeside.inserted`: 一条新记忆如何写入。
6. `writeside.superseding`: 一条新理解如何替代旧理解。
7. `after_commit.active_index`: commit 后默认可召回的 active 记忆。
8. `after_commit.old_fetch_default`: 旧卡默认不再 fetch,但不是删除。

测试命令:

```bash
python3 -m unittest tools.memory_readside_demo.test_adapter -v
```

如果输出 `OK`,说明本地最小闭环行为正常。

---

## 关键行为解释

### index 不暴露原话

`index` 只返回:

```json
{
  "memory_id": "mem_new_advice_boundary",
  "summary": "用户崩溃时先需要陪伴和在场感;稳定后可以一起分析问题。",
  "bucket_refs": ["安抚方式", "情绪崩溃"],
  "status": "active",
  "salience": "high",
  "is_open_thread": false,
  "score": 0.8
}
```

人话: agent 先看目录,不能一上来看到用户原话。

### fetch 才返回正文

`fetch` 返回:

```json
{
  "memory_id": "mem_new_advice_boundary",
  "summary": "用户崩溃时先需要陪伴和在场感;稳定后可以一起分析问题。",
  "verbatim": "不是永远不要建议,是我崩溃的时候不要先给步骤。等我缓过来,你可以陪我一起想办法。",
  "status": "active",
  "supersedes": ["mem_old_advice"]
}
```

人话: agent 觉得相关,再打开这张卡看细节。

### supersede 不等于删除

旧卡:

```json
{
  "id": "mem_old_advice",
  "summary": "用户不喜欢在难过时被立刻给建议。",
  "status": "superseded",
  "superseded_by": "mem_new_advice_boundary"
}
```

新卡:

```json
{
  "id": "mem_new_advice_boundary",
  "summary": "用户崩溃时先需要陪伴和在场感;稳定后可以一起分析问题。",
  "status": "active",
  "supersedes": ["mem_old_advice"]
}
```

人话: 旧理解过时了,但不删;默认只用新理解。

---

## 这一步仍然不做什么

- 不改数据库。
- 不改 iOS UI。
- 不接真实后端。
- 不处理真实加密 envelope。
- 不接 route A。
- 不做 eval 自动化。

人话: 这是完整功能的沙盘,不是测试环境上线。

---

## 下一步

等 zhihao 回答 `IO-memory-readside-zhihao-backend-questions.md` 后,把这个沙盘映射到真实后端:

1. 确认 `index/fetch` 落在 backend 还是 enclave。
2. 确认哪些字段密文、哪些字段可作为 envelope 外壳元数据。
3. 确认 `insert/supersede` 第一版 commit 怎么接。
4. 进入 `feedling-mcp` 做测试环境可验证的最小闭环。
