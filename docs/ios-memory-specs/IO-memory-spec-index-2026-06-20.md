# IO Memory Spec Index

> 日期: 2026-06-20  
> 作者: Codex 整理  
> 分支: `docs/io-memory-core-joint-spec`  
> 用途: 给 hx/Z、Seven、zhihao 和协作 agent 一个入口,说明 IO memory 相关文档各自是什么、什么时候产生、现在是否作为执行依据。

---

## 0. 一句话结论

当前 IO memory 的主线执行文档是:

1. `IO-记忆-背景-迭代-定稿.md` 看背景和为什么这么走。
2. `IO-memory-core-v1-联合工程spec.md` 看下一步工程主线。
3. `IO-memory-core-产品价值说明.md` 看给产品/leader 解释时怎么说。

Eval 相关文档暂时是暂停轨,不是当前第一优先级。它们保留为之后评测和题库建设的输入。

人话: 现在先别在十几份 md 里乱翻。要理解全局,先读背景定稿;要执行,读联合 spec;要对外解释价值,读产品价值说明。

---

## 1. 三个项目在整个 App 里的位置

| 项目 | 角色 | 和 memory 的关系 |
|---|---|---|
| `feedling-mcp-ios` | iOS App,用户实际看到的 Chat / Identity Card / Memory Garden / Live Activity / 审计卡 | Memory Garden 展示层在这里;用户以后会在这里看到、编辑、确认记忆 |
| `feedling-mcp` | 后端 + MCP + enclave + 加密存储 + 合约/审计 | 真实记忆写入、加密、召回、commit、index/fetch 最终要落在这里 |
| `io-onboarding` | 公开 onboarding 文档 + agent skill | 告诉用户和 agent 如何接入 IO;未来 memory 规则/skill 也会影响 agent 怎么写卡、怎么读卡 |

人话: iOS 是用户看到的身体,后端是记忆保险柜和执行系统,onboarding 是教用户和 agent 怎么正确使用这套身体。

---

## 2. 状态定义

| 状态 | 含义 |
|---|---|
| Active | 当前可以作为执行依据 |
| Supporting | 当前仍有用,但不是主 spec |
| Reference | 历史输入或外部参考,只用于理解来源 |
| Superseded | 已被后续文档吸收或替代 |
| Paused | 暂时不执行,后续阶段再恢复 |
| Separate track | 相关但不是 IO memory core 主线 |

“是否启用”的意思: 这份文档现在是否应该指导代码/接口/协作决策。

---

## 3. 推荐阅读顺序

### 给 Seven / leader

1. `IO-memory-core-产品价值说明.md`
2. `IO-记忆-背景-迭代-定稿.md`
3. `IO-memory-core-v1-联合工程spec.md`

重点看: 为什么 memory 是人机恋的承重墙,以及 M1 为什么先做工程 core 而不是直接改 UI。

### 给 zhihao / 服务端

1. `IO-memory-core-v1-联合工程spec.md`
2. `IO-MemoryCard-v1-Codex-工程契约草案.md`
3. `references/io-memory-spec-v1.md`
4. `references/2026-06-15-multi-user-memory-store-design.md`

重点看: 当前线上 `memory_moments.doc(JSONB)` 怎么兼容新 `MemoryCard / index / fetch / commit`。

### 给协作 agent / Claude / Codex

1. `IO-记忆-新对话上下文交接.md`
2. `IO-记忆-背景-迭代-定稿.md`
3. `IO-memory-core-v1-联合工程spec.md`
4. 本索引

重点看: 先建立项目心智模型,再开始写代码或拆任务。

---

## 4. 文档清单与启用状态

| 时间 | 文档 | 来源/作者 | 状态 | 是否启用 | 作用 |
|---|---|---|---|---|---|
| 早期 | `Agent记忆-6个tab整理.md` | hx/Z 早期整理 | Reference | 否 | 早期 tab/概念整理,只作为思路来源 |
| 2026-06-15 | `references/2026-06-15-multi-user-memory-store-design.md` | 服务端讨论输入 | Reference | 否 | 多用户密文存储、scope/index 思路参考;不是 IO 卡库最终方案 |
| 2026-06-15 | `references/memory-instruction-files.md` | 服务端讨论输入 | Reference | 否 | instruction file 类记忆参考;适合理解索引/预算,不直接等同人机恋卡库 |
| 2026-06-16 | `IO-memory-现状与方向-可行性验证.md` | hx/Z + 代码验证 | Supporting | 是,作为现状依据 | 说明线上 memory 当前怎么写、怎么存、怎么召回,以及为什么加密不挡 RAG |
| 2026-06-18 | `references/IO记忆系统_架构对齐与交接说明.md` | leader 反馈/交接 | Reference | 否 | leader 对 hx 方案的架构校准输入 |
| 2026-06-18 | `references/io-memory-spec-v1.md` | leader + hx/Z 对齐后反馈 | Supporting | 是,作为架构原则 | propose/integrate、agentic recall、永不硬删、全加密等原则来源 |
| 2026-06-18 | `IO-记忆-新对话上下文交接.md` | hx/Z 整理 | Supporting | 是,作为 onboarding/handoff | 给新 agent 快速接上下文,含术语人话解释 |
| 2026-06-18 | `IO-记忆-背景-迭代-定稿.md` | hx/Z 整理 | Active | 是 | 当前最完整的背景和阶段结论 |
| 2026-06-18 | `IO-Memory-Eval-v0-人机恋关系记忆题目答案草稿.md` | hx/Z + Codex 草稿 | Paused | 否 | eval 题型样张,之后 Seven 找案例时继续扩 |
| 2026-06-19 | `IO-Memory-Eval-v0-交接(草稿+分工).md` | hx/Z + Codex 草稿 | Paused | 否 | eval 分工和 case 格式,当前先不作为工程第一步 |
| 2026-06-19 | `IO-MemoryCard-v1-Codex-工程契约草案.md` | Codex | Superseded | 否 | Codex 版契约草案,已被联合 spec 吸收 |
| 2026-06-19 | `IO-memory-core-工程spec-Claude版.md` | Claude | Superseded | 否 | Claude 版工程 core 草案,已被联合 spec 吸收 |
| 2026-06-20 | `IO-memory-core-v1-联合工程spec.md` | Codex 整合 Claude + Codex | Active | 是 | 当前工程主线 spec |
| 2026-06-20 | `IO-memory-core-产品价值说明.md` | Claude | Supporting | 是,用于沟通 | 给产品/leader 解释为什么做 memory core |
| 2026-06-20 | `IO-memory-readside-zhihao-backend-questions.md` | Codex 整理 for hx/Z | Reference | 否,已回复 | readside/index/fetch 进入后端前的边界问题;已被 M1 plan 吸收 |
| 2026-06-20 | `IO-memory-v1-M1-zhihao-plan.md` | Codex 整理 for hx/Z | Active | 是 | zhihao 已确认的 M1 后端落地 plan:两个接口 + top 50 + readside first |
| 2026-06-20 | `IO-memory-readside-demo-说明.md` | Codex | Supporting | 是,作为本地验收说明 | MemoryCard v1 / index / fetch / insert / supersede 的本地最小闭环 demo;对应代码在 `feat/memory-core-local-demo` 分支 |
| 2026-06-20 | `superpowers/plans/2026-06-20-memory-readside-demo.md` | Codex 执行计划 | Supporting | 是,作为执行记录 | readside adapter demo 的早期执行计划;已被当前 local core demo 扩展 |
| 未定 | `IO-onboarding-自建路-一键安装设想.md` | hx/Z 整理 | Separate track | 否 | onboarding/安装体验方向,不是 memory core 当前主线 |

---

## 5. 当前已启用的核心结论

### 5.1 工程主线

当前先做 memory core / readside / contract,不把 eval 放在第一步。

人话: 先把“记忆卡长什么样、怎么查目录、怎么取正文、怎么接线上旧卡”做出可运行样子;考试题库后面继续做。

### 5.2 数据结构方向

继续兼容当前线上 `memory_moments.doc(JSONB)` 和加密 envelope,不一上来改表、不迁移历史数据。

人话: 不是推倒重来,是在旧记忆卡外面包一层更适合 agent 用的新格式。

### 5.3 读取方向

采用 `index -> fetch`:

1. `index` 先给 agent 看轻量摘要。
2. agent 判断命中后再 `fetch` 正文。

人话: 先看目录,觉得相关再翻书页,不要每轮把整本记忆都塞给模型。

### 5.4 写入方向

长期方向仍是 `propose -> integrate`:

1. propose: agent/LLM 判断什么值得记。
2. integrate: 系统/enclave 按死规矩 commit,做状态迁移、supersede、merge、decay。

人话: AI 可以提建议,但真正入库要走稳定规则,不能让模型随便改历史。

### 5.5 敏感记忆方向

敏感/亲密/XP 内容重点记录“边界和使用条件”,不记录露骨细节,也不在无关场景主动浮现。

人话: 记住“怎么尊重你”,不要记成一本隐私小抄。

---

## 6. 下一步执行建议

### M1: 继续做 readside adapter

目标: 把当前线上 `MemoryMoment` 转成 `MemoryIndexItem / MemoryFetchResult` 的演示跑通。

涉及项目:

- `feedling-mcp-ios`: 先放 demo、说明和验收文档。
- `feedling-mcp`: 之后由 zhihao 判断真实 index/fetch 应落在哪个后端接口。

### M2: 和 zhihao 对齐后端边界

需要对齐的问题:

1. `MemoryCard v1` 哪些字段进密文正文,哪些字段可作为 envelope 外壳元数据。
2. `index()` 是实时 enclave 解密生成,还是先做缓存。
3. 旧 `MemoryMoment` adapter 放后端、enclave,还是先作为工具层。
4. `commit` 的状态机第一版只支持 insert/supersede,还是一起带 merge/contradict。
5. route A 的 agent 是否能调用同一套 index/fetch。

这些问题已整理成 `IO-memory-readside-zhihao-backend-questions.md`,可直接发给 zhihao。

### M3: 再恢复 eval

目标: 用 Seven 找到的真实场景扩到 50 题,测试 capture / index / fetch / answer 是否真的更像长期伴侣。

人话: 工程先能跑,再用考试判断它是不是跑得好。

---

## 7. 建议的文件命名整理

本次先不直接改名,避免断已有引用。建议后续确认后改成以下结构:

```text
Docs/io-memory/
  00-index.md
  01-background-final.md
  02-core-v1-joint-spec.md
  03-product-value.md
  paused-eval/
    eval-v0-handoff.md
    eval-v0-companion-cases.md
  drafts/
    codex-memory-card-v1-draft.md
    claude-core-spec-draft.md
  references/
    leader-spec-v1.md
    backend-memory-store-design.md
    memory-instruction-files.md
```

人话: 现在先立目录牌,不要急着搬家。等大家都确认主线后,再统一搬进 `Docs/io-memory/`。
