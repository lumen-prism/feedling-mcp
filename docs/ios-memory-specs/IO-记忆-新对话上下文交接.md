# IO 记忆系统 · 新对话上下文交接

> 用法:把这份**整段粘进新对话**,新 agent 即可接上。再补一句"我接下来想做 X"。
> 截至:2026-06-18。代码事实读自 `/Users/hx/Projects/feedling-mcp`(已拉最新),带 `文件:行`。

---

## 0. 怎么跟我(hx)沟通

- 我 = Teleport 团队的**前端 / agent 优化工程师**(做提示词、skill、CLI 工具)。**不深入服务端运维/密码学细节。**
- **说人话、少堆术语**;陌生概念先用类比/生活例子;**重流程、重全景**;别太抽象;多给具体例子。出现行话请顺手翻译。
- 我会把内容**同步到团队 Router**(有预览确认),也用 git/PR。

---

## 1. 术语速查(我容易卡的词,先备着)

| 词 | 人话 |
|---|---|
| Flask 后端 | 普通 web 服务(Python 版 Express),IO 主后端,跑在普通服务器,**只存密文**。端口 5001 |
| Enclave | 跑在 CPU 硬件保险柜(TEE/Intel TDX)里的小服务,**唯一能解密的地方**,放解密私钥。端口 5003。**里面没有 LLM** |
| MCP 服务端 | 给"会说 MCP 的 AI 客户端"的侧门。端口 5002 |
| Consumer | route A 里跑在用户自己机器上的"传达室"进程(`tools/chat_resident_consumer.py`),收发聊天、转给 agent |
| 卡片盒 / 卡库 | = 记忆花园,用户可见;AI 召回/主动浮现只看它 |
| 明文 / 密文 | 没锁的字 / 锁起来的字(加密) |
| capture(提取) | 聊完天,把"该记的"抽成记忆卡 |
| 召回 / retrieval | 下次回话前,从卡库翻出相关卡 |
| embedding / 坐标 | 把一句话的"意思"压成一串数字,意思近的坐标就近。用一个"小模型"算,不是大 LLM |
| RAG | 翻出相关记忆 → 塞进提示词 → AI 据此回答 |
| propose / integrate | 提议(判断记不记/记成啥=LLM 动脑) / 归档(真正落库=死规矩,不动脑) |
| commit 契约 | 一个死板的"提交窗口":递操作单→系统照规矩校验+执行,不放 AI |
| agentic 召回 | 让 agent 自己读目录挑卡,而不是平台搜好喂给它 |
| index / fetch | 目录(id+一句话摘要+状态) / 取详情(给我这几张的完整内容) |
| status 状态机 | 卡片标签:active(在用)/superseded(被取代)/contradicted(矛盾)/pinned(钉死) |
| decay 衰减 | 旧的、长期没用的卡权重慢慢降,不容易被翻出来 |
| eval(考试) | 给"记忆做得好不好"打分的固定测试集 |

---

## 2. IO 是什么

给用户"已经在用的 AI"装一具常驻 iPhone 的身体:**看屏幕、锁屏推送、长期记忆(记忆花园)、人格(身份卡)**。卖点不只是能记,而是**数据 TEE 加密、链上授权、用户能亲自验证**。

**三个 repo:**
- `feedling-mcp-ios` — iOS 客户端(身体/UI)。
- `feedling-mcp` — 后端 + enclave + 链上合约(Flask:5001 / MCP:5002 / Enclave:5003 / PostgreSQL)。
- `io-onboarding` — 接入文档 + 给 agent 读的 skill。

**信任链一句话:** 数据加密存后端,连机房 root 都解不开;只有你的手机和 enclave(CPU 保险柜)能解;每次发版把代码指纹登记到链上,app 里"审计卡"可一键验证。

---

## 3. 三条接入路线(区别只在"大脑是谁的")

| 路线 | 大脑 | 用户要干啥 | 实时 |
|---|---|---|---|
| **A 自建/VPS** | 用户自带 agent(Hermes/Claude Code…)跑自己机器 | 跑一个 Consumer(传达室)中转 + 喂 skill | ✅ |
| **B API/托管** | 后端 hosted_runtime 拿用户的模型 key 当 agent | 填 key + 传材料,零运维 | ✅ |
| ~~C 官方App/MCP~~ | 官方 App | — | ❌ 2026-06 已弃用 |

**route A 日常消息怎么流转**:app 打字 → 加密发 IO 云后端(存密文)→ Consumer(用户机器)长轮询取到 → 找 enclave 解密 → 同步调用 agent(HTTP/CLI,回复=响应体/stdout)→ Consumer 加密发回后端 → app 收到。**app 和 agent 不直连,中间隔着云后端这个邮局,Consumer 主动来取。**

**route A 的痛点(重要)**:Consumer 现在**不读记忆、不注入**,只转发聊天 + 代转发 agent 的 memory 动作。agent 回话用**自己的记性**,绕过 IO 卡库 → 我们对卡库做的优化在 route A 不生效。这叫"route A 收口"问题(本轮不做)。

---

## 4. 记忆系统:先建心智模型

- **模型本身无记忆**:每次调用即忘。memory 全是模型外的代码在做两件事:**写(提取)+ 读(召回)**。
- **三种"算力"别混**:① 大 LLM(GPT/Claude)= 判断/生成,贵;② embedding 小模型 = 打坐标,便宜,像调函数;③ 纯代码 = 比距离/关键词/排序,免费。
- **加密不挡 RAG**:检索本来就在 enclave 内、对**解密后的明文**做(`enclave_app.py:733-770`)。加向量只是"同一个地方多算一步";单用户数据量小,enclave 内暴力算余弦即可,**不用 pgvector 索引**。
- **卡片盒 vs 草稿**(leader 的框架):IO 卡库 = 用户可见、唯一召回源;agent 自己的记性 = 私人草稿,永不浮现。

---

## 5. 记忆系统现状(全部代码确认)

| 动作 | 现状 | 证据 |
|---|---|---|
| 写 capture | ✅ 自动,6 类(fact/event/quote/moment/insight/reflection) | app.py:9444 `_model_api_run_memory_capture`;prompt model_api_runtime/prompts.py:75-105;每 24 轮 app.py:10191 |
| 存 | ✅ 明文元数据 + 密文体,全在 JSONB `doc` | alembic/0001_baseline.py:57-64;app.py:14138-14156 |
| 写入对账 | ✅ ≥0.85 直写/0.55-0.85 待确认/<0.55 丢 | app.py:8587-8589;hosted_runtime.py:240-262 |
| 翻 检索 | ⚠️ **纯关键词,零向量**,在 enclave 内做 | context_memory_selection.py(全文无 embedding);选 enclave_app.py:916-922 |
| 整理 跨卡对账 | ❌ 只有简单去重;但有 recap worker 骨架可扩展 | _dedupe_memory_cards app.py:7251;recap app.py:9987-10070(每 80 轮) |
| 考试 eval | ❌ 无记忆 eval(只有 proactive 的) | tools/proactive_gate_eval.py |

**关键事实:**
- 一条卡:**明文** = v/id/type/occurred_at/created_at/source/visibility/owner_user_id/anchor_memory_ids;**密文(body_ct)** = title/description/her_quote/context 等。**→ 摘要/标题现在都在密文里,没有明文摘要。**
- 加字段(lifecycle/weight/向量)**直接进 JSONB 即可,免迁移**(app.py:13442 现在就这么干)。
- **enclave 无 ML 库、不调外部模型**。
- 历史导入两遍蒸馏:app.py:7018-7048(候选)、6865-6903(合并);身份派生:app.py:7750-7811。
- Consumer 主循环:chat_resident_consumer.py:3236-3416;代转发 memory 动作 :2179。

---

## 6. 我的方案 v2(四阶段)

顺序:**① eval(先做,地基)→ ② 检索(加向量/RAG)→ ③ 整理(对账)→ ④ route A 收口(本轮不碰)**。每步独立可交付。

借鉴两个 **Anthropic 官方视频**:
- **《The Prompting Playbook》**:① Generate-Evaluate-Repair 小循环(便宜模型拆"生成→评估→修复"打爆贵模型一口气干,0/5→5/5);② 话说两头(也讲反面后果);③ XML 标签分层 + 清过期防御补丁;④ 硬能力(算术/相关性)别用 prompt 硬怼,给工具。
- **《Evals for taste》**:① eval 是飞轮不是收尾;② 两种判官:code grader(数指标)+ model grader(LLM 按 rubric 打分,需校准);③ **LLM 判分要"先列优缺点、最后才打分"**(否则瞎编理由);④ QA 找茬对抗循环(生成→另一 AI 挑刺→改);⑤ pairwise 两两对比。

---

## 7. leader(@sevenflooor)的架构对齐(最新,最重要)

**没否我,但重新框了。三个核心:**

1. **草稿 vs 卡库**:见 §4。卡库是唯一可见、唯一召回源。

2. **写卡拆两步**:
   - **propose(提议)** = 判断"记不记/记成啥/跟哪张卡有关系"。**LLM 动脑,两条路各做各的。**
   - **integrate(归档)** = 把提议落成库状态变化(新增/覆盖/合并)。**死规矩"commit 契约",enclave 只做确定性校验 + 状态迁移 + decay,无 LLM。**
   - 关键:**LLM 提议操作,死规矩执行;不是 LLM 亲手改库。**(类比:你填单转账=提议;银行按流程入账=归档,柜员不替你判断也不让你直接动账本。)
   - 为什么分开:两条路用同一套归档规矩、可审计、不被 LLM 一时兴起乱改。

3. **agentic 召回**:agent 自己读"目录(index:id+一句话摘要+状态)"→ 自己挑 → fetch 取详情 → 不够再取。**服务端无 LLM、enclave 内也无 LLM,挑卡的脑子是 agent 自己。向量/RAG 从主菜降为"卡太多时的可选预筛"。**
   - **副作用(好消息)**:"embedding 在哪算"这个原阻塞项,在主路径上基本作废。
   - 查询有**两次解密**:轻的(目录摘要,要让 agent 读到)+ 重的(挑中卡的完整正文)。

4. 状态机:active/superseded/contradicted/pinned;**整理 = commit 路径的一部分**(dream/巩固只是"另一个走 commit 契约的提议者")。

---

## 8. 开放问题(leader 要一起拍,会反向改设计)

- **Q1(最高优先,卡住召回)**:目录(摘要)存**明文还是密文**?明文=快但泄概要;密文=每次建目录要全解密。**已知部分答案:现在摘要在密文 body 里,所以要么新加单独存的轻量摘要字段、要么每次全解密。**
- **Q2**:冷启动上传**大文件提炼**(API 用户开局传人设/聊天记录)是**另一套机制**(截断问题,要分块 + 两遍提炼 + 覆盖校验),别套用稳态落卡。
- **Q3(护城河)**:**用户自己**能不能加/改/删/看记忆 + **版本/history/回滚**——正对 #keep4o"记忆被抹/patch breakup"痛点。Ombre/memU 都没做。
- **Q4**:矛盾时解**规则**(agent 提了 supersede 但判错怎么办:覆盖还是并存打 contradict)。
- **Q5(次级)**:主动浮现 / 做梦 / 衰减——**衰减必须按类型豁免**,否则重蹈 Ombre 永久桶被衰减的 bug。

### leader 要我回答的 4 个代码题
1. 现有 JSONB 里 summary/元数据哪些明文、哪些密文?(→ §5 已基本答:摘要在密文)
2. enclave 能否在内存缓存"解密后的目录(index)"?资源够不够?(待查)
3. 把 context_memory_selection.py 改 index/fetch 两端点、recap worker 改成 commit 契约一部分,工作量多少、哪个更难?(待估)
4. commit 契约做成 enclave 内确定性校验(无 LLM),现有 hosted_runtime/enclave_app 结构支持吗?(待查)

---

## 9. leader 对原文档的勘误(C1-C5)

- **C1**:imprint-memory 许可证——leader 说 **MIT**,我之前 WebFetch 读到 **AGPL**,**冲突,需看仓库实际 LICENSE 核实**。若 MIT,它的 FTS5+向量+RRF+jieba 值得精读。
- **C2**:我那"4 阶段"偏 V1 中心化(平台啥都自己干),要往 V2(agent 判断、平台薄校验)对齐。
- **C3**:"整理"从"保洁"升级为"落卡主路径的一部分"。
- **C4**:召回改 agentic → "embedding 在哪算"阻塞项基本作废。
- **C5**:我对的部分保留(代码定位、eval、加密不挡 RAG、JSONB 免迁移、写入对账已有、route A 本轮别碰)。

---

## 10. 开源库结论(license 决定能不能用)

| 库 | 协议 | 拿来做 |
|---|---|---|
| Ombre-Brain | MIT ✅ | 整理:decay 引擎 + 状态机(只用元数据,不解密),可直接改 |
| MemPalace | MIT ✅ | 检索:混合 recipe(语义+关键词+时间邻近)+ 跑分法;实现放 enclave |
| memU 核心 | Apache ✅ | 借两招:分层摘要(省 token)+ 充分性检查(够不够再捞) |
| imprint-memory | ⚠️ 待核实 | 若 MIT 则精读其检索;**memU-server 是 AGPL,别碰** |
| Bibliotheca | — | 不是代码,人机恋圈索引(找 design partner) |

---

## 11. 产品视角(#keep4o / 重度 AI 陪伴用户)

早期高价值 design partner。护城河 = **记忆/人格可携带、加密自有、用户可控**(他们最怕"AI 变冷 + 记忆被抹")。
- 记忆提取要保留情感张力(爱称/inside joke/依恋权值=高权重 pinned 卡)——prompt 级增强,用 eval 验证。
- 人格"热插拔"(换模型 persona 继续):route B 的 identity+记忆注入**已实现核心**;做"正向强 persona 注入",**别做"对抗大厂安全/越狱"**(政策风险)。
- TEE 保护的是**存储**那关,**不是模型那关**——主流厂商照样审查。要自由,引导用户**自带无审查的开源模型 key**,IO 不去规避。

---

## 12. 已产出物

- feedling-mcp-ios/Docs/ 下 3 份文档进了 **PR #9**(`github.com/teleport-computer/feedling-mcp-ios/pull/9`):`IO-memory-现状与方向-可行性验证.md`(主)、`Agent记忆-6个tab整理.md`、`IO-onboarding-自建路-一键安装设想.md`。均已同步 Router。

---

## 13. 下一步(待办)

1. **回 leader 的 4 个代码题**(Q1 可先答:摘要现在在密文里)。
2. 评估"enclave 能否缓存解密后的 index"(决定 agentic 召回成本)。
3. **核实 imprint 真实 LICENSE**。
4. **eval 仍是第一步**(leader 也认):golden set 要纳入"agent 读摘要能不能正确挑卡"。
5. onboarding 减摩擦:`io setup` 交互式 CLI(解析连接串 + 按 agent 套模板 + 每步验证)+ 大众分流到 route B。
