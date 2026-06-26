# Agent 记忆 · 6 个学习 tab 内容整理

> 来源:hx 收集的 6 个 agent / 记忆主题 tab(2026-06)。
> 用途:给 IO 记忆系统迭代做参考。配套:`IO-memory-迭代-讲稿.md`。

## 速览

| # | tab | 一句话 | 对 IO 借鉴 |
|---|---|---|---|
| 1 | imprint-memory(GitHub) | Claude Code 自动记忆,hook 驱动,混合检索 | 检索方法(但 AGPL,只能参考) |
| 2 | Ombre-Brain(GitHub) | Claude 情绪长期记忆,衰减 + 状态机(永不删只降权) | 整理/对账(MIT,可直接用) |
| 3 | 拆 Agent Harness(博客) | agent = 执行/状态/治理三层;记忆维护交给独立分身 | 整理工做成独立程序 |
| 4 | The prompting playbook(Claude 官方视频) | agent prompt 要管 plan/act/adapt | 写 capture/整理 prompt 的方法 |
| 5 | Evals for taste(Claude 官方视频) | 怎么给 AI 打分:固定考卷、可重放、5 维、飞轮 | eval 整套方法 |
| 6 | AI Boyfriend Market(ChatGPT 分享) | 重度 AI 陪伴用户 = 主权 agent 早期高价值市场 | 人设评估靠人工;找 design partner |

---

## 1. imprint-memory(GitHub,Claude Code 记忆系统)

**一句话:** 给 Claude Code 装一个完全自动、不靠 LLM 自觉的本地记忆系统。

**核心要点:**
- 记忆捕获靠 **hook**,不靠 prompt:Stop hook(每个 turn 结束自动存)、UserPromptSubmit hook(提问前自动浮现 recall)、各渠道一个 `log_message()` 入口。
- 检索 = **混合搜索**:FTS5/BM25(关键词)+ 向量 cosine(语义)+ RRF 融合,跨 memory/bank/chunk 三池。
- 两步搜索规则:先精确(chunk 索引)→ 失败再回退(全文倒排),不许直接说"不记得"。
- 27 个 MCP 工具,但 curation 可选(平时不调也能用)。
- **协议:AGPL-3.0** → 不能拷进闭源后端,只能读懂算法重写。

## 2. Ombre-Brain(GitHub,Claude 情绪长期记忆)

**一句话:** 给 Claude 装一个有情绪标记 + 自然遗忘 + 主动浮现的关系记忆。

**核心要点:**
- 情绪用 Russell 环形模型:**valence(效价)+ arousal(唤醒度)** 两个连续维度。
- **自然遗忘**:`e^(-λ·days)` 衰减;高 arousal 衰减更慢;短期(≤3天)"时间 70%+情感 30%",长期反过来。
- **lifecycle 状态机**:active ×1.0 / resolved ×0.05 / digested ×0.02 / pinned ×999 / feel 固定 —— **永不删,只降权**。
- **主动浮现(surfacing)**:未解决 + 高 arousal 的记忆权重 ×1.5,对话开头自动推。
- **Dream + Feel 双机制**:每次新会话开头 `dream()` 自省,把放下的标 resolved、有沉淀的写 feel(模型自己的感悟,独立 domain,不衰减)。
- 只 6 个 MCP 工具:breath / hold / grow / trace / pulse / dream。
- **协议:MIT** → 可直接改用。`decay_engine.py`(衰减+权重)完全独立。

## 3. 拆 Agent Harness(博客,剖析 Claude Code)

**一句话:** 把 Agent Harness 从概念讲成工程语言——三层结构 + 记忆派系 + Claude Code 源码细节。

**核心要点:**
- **Harness 三层**:执行层(文件/浏览器/解释器="能跑")、状态层(prompt/skills/memory/context="能跑对")、治理层(多 agent/权限隔离="能跑稳")。
- **Memory 三派**:知识图谱(作者明确不喜欢)、Unix files+Markdown+Agent 驱动(Claude Code 走这条,作者推)、模型内化 fine-tune(太贵)。
- **Claude Code 源码工程细节**:上下文压缩策略;**Fork Agent / auto-dream**(每轮结束 fork 一个 agent 复用 KV cache 做记忆维护,主对话不掺和);skill 用 YAML + 一行 description,先读 description 再按需加载。
- **好 Harness 公式**:好的 context space + 好的 action space + **less** prompt control(模型越强,prompt 硬控越该退后)。
- **"Bash is all you need"**:CLI 在预训练语料里占比远超 MCP,所以 CLI 工具的任务完成率 > MCP。

## 4. The prompting playbook(Claude 官方 YouTube,33:48)

**一句话:** agent-style prompting 不同于 Q&A——要管 plan / act / adapt。

**核心要点:**
- agent 要会:plan(先规划再执行)、act(用工具行动)、adapt(按反馈调整)。
- 是 Claude 官方系列视频之一,配套阅读:Anthropic《Building effective AI agents》blog。
- (视频无 transcript,以上为要点;要细节直接看视频/blog。)

## 5. Evals for taste(Claude 官方 YouTube,39:15)

**一句话:** 一套 rubric 驱动、可重放、5 维信号、6 小时一轮的 eval 系统,核心是"用户不满"驱动的开发飞轮。

**核心要点:**
- **rubric-driven**(评分卡,不是单一数字)。
- **replayable**(拿真实用户项目重放,不是合成数据)。
- **5 维信号**:quality / cost / latency / error / token。
- **<6 小时一轮**,方便频繁跑。
- **飞轮**:用户不满的样本变成新 golden set,eval 从测试工具变成开发飞轮。
- **核心论点:eval 不是收尾步骤,是 agent 工程的飞轮。**

## 6. AI Boyfriend Market Insights(ChatGPT 分享)

**一句话:** 把重度 AI 陪伴用户当作主权个人 agent 的早期高信号市场。

**核心要点:**
- 这群人**主动学技术**(fine-tune / memory / agent stack)保 persona,不是普通聊天用户。
- 不是大众市场,但**高付费 + 高粘性 + 高信号**;公开 framing 别用"AI boyfriend",应是"give your personal agent a home"。
- **技术三选项**:A 纯 persona 蒸馏(轻,但漂)/ B 纯 fine-tune(行为深,tool calling 弱)/ **C 蒸馏 + fine-tune 结合(推荐)**。
- **persona drift 没客观 benchmark** → 需要 community human eval + pairwise + 可能 per-user RLHF + 严格版本追踪。
- **Memory 四层**:imported historical(旧对话导入)/ distilled personality(persona spec)/ live contextual(实时设备/位置/屏幕)/ fine-tuned model memory(行为编码进权重)。
- 隐私即价值主张本身。

---

## 对 IO 的可用性结论(库 license + 借鉴)

| 库 | 协议 | 拿来做 | 状态 |
|---|---|---|---|
| **Ombre-Brain** | MIT ✅ | 整理:衰减引擎 + 状态机 | 主力,可直接改 |
| **MemPalace** | MIT ✅ | 检索:混合 recipe + 跑分 | 主力,可直接用 |
| **memU(核心)** | Apache ✅ | 补:分层摘要 + 充分性检查 | 借设计,核心库可用 |
| imprint-memory | AGPL ⚠️ | (被 MemPalace 替代) | 弃 |
| memU-server | AGPL ⚠️ | — | 别碰 |
| Bibliotheca | — | 社区索引 / 找 design partner | 不是代码 |

**打法:** eval 打地基 → MemPalace 补检索 → Ombre 做整理 → memU 收口。
