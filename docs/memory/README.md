# IO 记忆系统 · 文档索引

> 所有 memory 相关文档统一收在这里(原先散在 feedling-mcp/docs 和 feedling-mcp-ios/Docs 两个 repo)。
> 状态约定:**当前** = 现在生效;**历史脉络** = 怎么走到今天的记录(保留备查);**archive/** = 已被取代。

## 🚀 先看这两份(对齐/入门)
- [统一架构-大白话](IO-memory-统一架构-大白话.md) — 整件事一页讲清(给 Seven/CC/zhihao 对齐)
- [本轮迭代-meeting总览](IO-memory-本轮迭代-meeting总览.md) — 做了啥/没做啥(开会快照)

## 🧭 当前 · 架构主线
- [统一架构-spec(v2)](IO-memory-统一架构-spec-给codex-review.md) — 收敛蓝图(Codex 两轮 review 已折叠)
- [统一架构-plan](IO-memory-统一架构-plan.md) — 5 阶段路线图(P1→P5)
- [P1-工程执行计划(施工图)](IO-memory-P1-工程执行计划-Codex版.md) — **P1 可直接开工的施工图**

## 🔧 当前 · 分阶段 / 专题
- [M2-写入闭环-CC方案](IO-memory-M2-写入闭环-CC方案.md) — insert/supersede 设计(已实现,待合 main)
- [M2-写入闭环-详细实现说明-Codex](IO-memory-M2-写入闭环-详细实现说明-Codex.md) — M2 实现细节
- [M3-质量与eval-方向](IO-memory-M3-质量与eval-方向.md) — 记忆质量/eval(起点:狗"蛋子"漏记 bug)
- [recall-window-可配置](IO-memory-recall-window-可配置-给codex.md) — 召回窗口 50→可配/全开

## 🕰 历史脉络(保留备查,M1/M1.5 已上 test)
- [M1-agent-tools-decision-context](IO-memory-M1-agent-tools-decision-context-for-cc.md)
- [M1-agent-tools-Codex方案](IO-memory-M1-agent-tools-Codex方案.md)
- [M1.5-agent-tools-CC方案与决策](IO-memory-M1.5-agent-tools-CC方案与决策.md)
- [M1.5-agent-tools-测试方案](IO-memory-M1.5-agent-tools-测试方案.md)
- [readside-M1-plan-evolution-and-code-handoff](IO-memory-readside-M1-plan-evolution-and-code-handoff-codex.md)
- [readside-m1-local-test-guide](IO-memory-readside-m1-local-test-guide-codex.md)

## 🗄 archive/(已被取代)
- `readside-M1-Claude-review-for-codex.md` — 有"pick 没实现"笔误,被 M1.5/M2 取代
- `readside-M1-隐患与待确认.md` — 同上
- `routeB-接管道-plan-给codex.md` — 被 agentic 决策取代
- `hosted-recall-bug-CC-review-回复.md` — 那个召回 bug 已修

---

### 阅读顺序(新人/对齐)
大白话 → meeting总览 → 统一架构 spec → plan → P1 施工图。细节按需翻 M2/M3/专题。

### 注
- 还有一批**更早的 memory 文档仍在 `feedling-mcp-ios/Docs/`**(已提交:Eval-v0、MemoryCard-v1 契约、memory-core spec、zhihao questions、现状与方向 等)。它们是 iOS repo 里已提交的早期工作,本次未跨 repo 迁移——如需也收拢到这里,单独决定。
