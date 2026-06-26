# IO 记忆系统 · 架构对齐与交接说明

> 面向：hx
> 性质：在你那份《IO 记忆系统 · 现状全景 + 迭代方向(代码验证版 v2)》基础上，做了一轮架构母题的对齐。本文不推翻你的工作——你的代码定位、eval 先行、"加密不挡 RAG" 这些判断都是对的、继续用。本文做三件事：① 说清现在**确定**了什么、**还没确定**什么；② 给一个整体全景和基本路径；③ 对原 MD 里几处需要重定位的地方做勘误。

---

## 0. 一句话先行

我们要做的不是"把现有中心化记忆管道做得更聪明"，而是把记忆库定位成一个**权威的、外接的、用户可见的卡库**——它对 agent 是个有稳定契约的外设。所有写入只能穿过一条**路径无关的 commit 契约**；召回改走 **agentic**（agent 读紧凑索引自己选），向量检索从核心降为可选。两条路（VPS / API）共享一切，**只在"谁来 propose 落卡"这一点分叉**。

---

## 1. 架构母题（先理解这个，后面才不跑偏）

- **卡库 = 权威外置记忆库（插件式）。** 它是唯一用户可见的面，也是召回和主动浮现唯一读取的数据源。
- **agent 的私有记忆 = 草稿。** 草稿是提议者私有的、**永不浮现**。
- **落卡 = 从草稿慎重发布进权威库。** 但"落卡"其实是两个必须拆开的动作：
  - **propose（提议）**：判断"这段值不值得记、记成什么、和老卡什么关系"。这是 taste，**按路径分叉**。
  - **integrate（整合入库）**：把提议变成库的状态变化（新增 / 覆盖 / 合并 / 降权）。这是契约，**路径无关，由库拥有**。
- 关键澄清：**integrate 不是一个服务端 LLM 环节。** 库不跑我们自己的 LLM。库只暴露一个 `commit` 工具/契约，enclave 做的是**确定性的校验 + 状态迁移 + decay 重算**。判断由调用方（VPS 的 agent / API 用户的 tool 调用）做，enforcement 由 enclave 做。
- 这套定位同时解决了透明问题：草稿私有、库可见，所以"她能看到什么"这个透明 UI 才诚实，也避免了之前那个"gate 比 agent 多一层感知"的不对称缺陷复发。

---

## 2. 已确定的设计决策

| 编号 | 决策 | 说明 |
|---|---|---|
| D1 | 两条路共享一切，只在"谁 propose 落卡"分叉 | VPS：用户自己的 agent 提议；API：toolset 里有同一个 commit 工具，用户用自己的 key 调用 |
| D2 | integrate = 共享 commit 契约，enclave 无 LLM | enclave 只做：校验操作合法 → 应用状态迁移（如 old.status=superseded, new.supersedes=old.id）→ 重算 importance/decay。两条路用同一套语义，不能各自发明存储规则 |
| D3 | 召回走 agentic，不走机械注入 | enclave 暴露 `index`（返回紧凑列表：id/summary/type/status/last_active）和 `fetch(ids)`（返回选中卡的全文）。agent 读 index → 选 → fetch → 不够再 fetch。向量/RRF 降级为**可选引擎**，仅当卡量大到 index 塞不下时，作为"index 预筛门卫"启用 |
| D4 | 内容偏逐字存储，不预先摘要 | 面向人机恋用户"记得过程而不只是结论"。这一条沿用你 MD 里 JSONB 明文元数据 + 密文体的结构 |
| D5 | schema 由"操作认领"反推，不照抄任何库 | 见 §3。Ombre 的 Russell 情感坐标、memU 的 typed-item 都不直接搬 |
| D6 | eval 先行 | 完整保留你 MD 阶段1 的思路（golden set + 机器判官 + AI 品味判官、先说理由再打分）。唯一补充：golden set 要纳入"agentic 召回的判断质量"——即 agent 读 summary 能不能正确决定拉哪张 |

---

## 3. schema 草案（可吵的起点，不是定稿）

判准只有一条：**每个字段必须有一个明确"用它的操作"，否则砍掉。**

**承重字段（有操作认领，保留）**

| 字段 | 被谁认领 |
|---|---|
| `verbatim`（密文体） | 深读（fetch 后读全文）+ 产品原则 |
| `summary`（一等、受质量管控、建议单独 eval） | agentic 召回——index 就是 summary 列表，agent 全靠它决定拉哪张。**这是选了 agentic 之后必然变重要的字段** |
| `thread_status`(open/resolved/dormant) + `follow_up` | 主动浮现（"上次那件事怎么样了"这条管线） |
| `status`(active/superseded/contradicted/pinned) + `superseded_by` | integrate 状态机 + 召回（不返回 superseded）+ 衰减（pinned 豁免） |
| `importance` / `last_active` | 衰减 |
| `last_surfaced` / `surface_count` | 浮现（避免重复推同一条） |
| `provenance`(committer / confidence / source_ts) | 矛盾消解（谁可信）+ eval + 调试 |

**待砍 / 待重定位（找不到操作认领）**

| 字段 | 处理建议 |
|---|---|
| `card_type` 完整枚举(milestone/preference/fact/...) | 真正有行为分支的只有 open_thread。建议收成"是否 open loop" + 一个软 topic tag |
| `about`(self/relationship/world) | 没有操作因它走不同逻辑，建议砍或并进 tag |
| `milestone_signals`（原 Garden 三信号） | 它是 **propose 判断时**的启发式，不是存进卡的字段。降级为候选启发式之一，不写进 schema |
| `emotion_tags`（富情感分类） | 暂不上整套分类法。浮现若只需要"挑情绪重的优先推"，一个 `intensity`/`salience` 标量就够 |

---

## 4. 未确定 / 必须接着对齐的开放问题

下面这几个不是"以后再补"，而是**会反向改写上面已确定部分**的，按优先级排：

**Q1 · 明文 / 密文边界（最高优先，卡住召回）**
agentic 的紧凑 index（summary 列表）到底存明文元数据还是密文体？
- 明文：index 可零解密廉价提供，但是泄漏面，和"留存0"的品牌打架。
- 密文：每次建 index 都要把全部卡解密，成本回来了。
- 这个不敲定，**D3 的召回方案严格说还没落地**。候选方向：summary 用一个单独的轻信封 + enclave 内缓存解密后的 index。需要你评估 enclave 能否缓存解密态、以及现有 JSONB 里哪些字段已是明文。

**Q2 · 冷启动 onboarding 大文件提炼（产品目标#1，独立机制）**
API 用户开局上传一坨大文件（人设卡、导出的完整聊天记录）。这和稳态"一次一张"的落卡是**两套机制**：大文件提炼有截断问题（前面记到、后面丢），需要分块 + 两遍提炼 + 覆盖校验。不要套用稳态落卡。（你之前在 Pili 那边碰过类似的大文件落卡截断问题，可以接上。）

**Q3 · 用户可控 + 版本 / history（护城河）**
目前全程只讨论了"agent 怎么改库""平台怎么整理库"，没讨论**用户自己**能不能删 / 改 / 钉 / 看见某条记忆何时因何被覆盖。这俩合起来正对核心痛点"Patch Breakup / 记忆丢失"：版本让被覆盖的旧卡可回滚而非真删，用户可控让用户在模型漂移时能自救。Ombre / memU 都没认真做，是我们该做成差异化的地方。

**Q4 · 矛盾消解策略**
我们定了状态（superseded / contradicted），但没定**规则**：agent 提了 supersede 但判断错了怎么办？是覆盖、还是并存打 contradict 标记、还是需要二次确认？谁仲裁？这条决定 integrate 契约的细节。

**Q5 · 次级（点了名、未设计完，不卡骨架）**
提炼策略、上下文注入（选中卡如何进 agent 上下文：token 预算 / 排序 / 怎么告诉 agent"这是记忆"）、衰减豁免、做梦策略。
- 注意衰减**必须给某些类型豁免**，否则会复现 Ombre 那个永久桶被一视同仁衰减、掉到检索阈值以下的 bug。

---

## 5. 全景图：记忆生命周期与覆盖状态

| 组 | 环节 | 状态 |
|---|---|---|
| 写入 | 触发 capture | 部分（route-dependent propose） |
| 写入 | 提炼 extraction | 缺口 |
| 写入 | 冷启动 onboarding | **缺口（Q2）** |
| 写入 | 落卡 integrate | 已对齐（commit 契约） |
| 写入 | 矛盾消解 | 缺口（Q4） |
| 存储 | schema 字段 | 进行中（§3） |
| 存储 | 明文/密文边界 | **缺口（Q1）** |
| 存储 | 版本 / history | 缺口（Q3） |
| 读取 | 召回 recall | 已对齐（index/fetch，待 Q1 落地） |
| 读取 | 主动浮现 | 部分（已点名，未设计策略） |
| 读取 | 上下文注入 assembly | 缺口 |
| 维护 | 衰减 decay | 部分（注意豁免 bug） |
| 维护 | 做梦 consolidation | 部分（dream 也走 commit 契约，作为又一个提议者） |
| 维护 | 删除 / 留存0 | 缺口 |
| 横切 | eval | 已对齐（沿用阶段1） |
| 横切 | 用户可控 / 透明 | **缺口（Q3）** |
| 横切 | provenance / 信任 | 部分 |

---

## 6. 对原 MD 的勘误与再定位（建设性，不是否定）

**C1 · 许可证更正：imprint-memory 是 MIT，不是 AGPL。**
原 MD 红线里"别碰 imprint/memU-server(AGPL)"把两个混了。`Qizhan7/imprint-memory` 明确是 MIT；AGPL 的是 `memU-server`（这条你对）。影响不小：**imprint 恰恰已经实现了你想在阶段2/3 造的东西，且能合法借鉴**——FTS5 + 向量 + RRF 融合 + jieba 中文分词（正好治"project 翻不出东方Project"），以及 find_duplicates / find_stale / decay 原语。结论应反过来：imprint 是这轮最该精读的参考。

**C2 · 架构再定位：4 阶段是在加固"中心化管道"，需和 V2 对齐。**
原 MD 的 capture（平台分6类）、选卡（平台关键词）、整理（平台 worker）都是平台侧大脑，这是 V1 中心化模型，和 V2"agent 拥有判断、平台退成薄加密层"有张力。重定位：中心化管道服务 **route B / hosted 用户**是合理的（它们的通用 LLM 跑不了后台判断），但要把"这是一个选择"显性写出来，且 integrate 契约必须路径无关。

**C3 · "整理" 的定位变了。**
原 MD 把整理当后台保洁（每 N 轮去重/标矛盾）。新架构里它是 **commit 写入路径的一部分**：dream/巩固也只是又一个走同一个 commit 契约的提议者，不是特例。扩展现有 recap worker（app.py:9987-10070）这条路仍然成立，但定位从"旁路保洁"升级为"写入主路径之一"。

**C4 · 召回方向改变 → 一个阻塞决策可能作废。**
原 MD 的"关键词优先、不够再向量补"是顺序回退；新方向是 agentic（agent 读 index 自选），向量降为可选引擎。**好消息**：你 MD 里 §3 / §7 escalate 给 leader 的"embedding 在哪算"那个决策，在 agentic 路线下核心路径不需要在 enclave 里算 embedding，因此可能直接作废或降级为可选——少一个阻塞项。

**C5 · 明确保留、且做得对的部分（继续）。**
代码逐行定位的工程纪律；eval 先行；"加密不挡 RAG"（检索在 enclave 内对解密明文做，enclave_app.py:733-770）这个判断正确且关键；schema 全 JSONB 免迁移；写入对账已存在（缺的只是跨卡对账）；route A 收口本轮别做。这些不动。

---

## 7. 建议的基本路径

按"会不会反向改写已有设计"排，而不是按生命周期顺序：

1. **Step 0**：对齐本文档的架构母题——确认 propose/integrate 拆分、agentic 召回方向。
2. **Step 1**：敲 **Q1 明文/密文边界** → 让召回方案真正落地（含 summary 字段形态、enclave 如何廉价维护 index）。
3. **Step 2**：**eval 地基**（保留你阶段1，golden set 纳入 agentic 召回判断质量）。
4. **Step 3**：**commit 契约 + schema 定稿**（integrate 状态机 + Q4 矛盾消解规则）。
5. **Step 4**：**冷启动 onboarding** 独立设计（Q2 大文件提炼）。
6. **Step 5**：**用户可控 + 版本**（Q3 护城河）；浮现 / 做梦 / 衰减豁免逐个填。
7. route A 收口：本轮仍不做。

---

## 8. 需要你（hx）先回答的几个代码侧问题

1. 现有 JSONB 里，summary / 元数据具体哪些是明文、哪些是密文？（直接决定 Q1）
2. enclave 能否在内存里缓存"解密后的 index"？资源够不够？（决定 agentic 召回的成本）
3. 把 `context_memory_selection.py` 改成 `index` / `fetch` 两端点、把 recap worker（app.py:9987-10070）扩成 commit 契约的一部分，工作量你估多少？哪个更重？
4. commit 契约要做成 enclave 内的确定性校验（无 LLM），现有 hosted_runtime / enclave_app 的结构支持吗？

---

*本文是架构母题层的对齐，不含逐行实现。Q1–Q4 对齐后再进实现细节。*
