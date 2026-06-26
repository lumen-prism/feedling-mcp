# IO 记忆系统 · 背景 / 方案迭代 / 定稿与结论

> 截至 2026-06-18。给协作 agent / 同事冷读用:看完能understand IO 记忆这件事**怎么一步步演化到现在的定稿**,以及**当前该照什么干**。
> 代码事实读自 `/Users/hx/Projects/feedling-mcp`(已拉最新),带 `文件:行`。

---

## 0. 一页速览

- **目标**:优化 IO 的"记忆"(让 AI 真的记得住用户、记得准、不堆矛盾)。
- **结论(现在)**:leader 出了 **IO 记忆 spec v1**(下文 §3),把这一路讨论收敛成**可开干的设计**;大部分开放问题已拍成决定。**接下来:先搭 eval,再照 spec 落地。**
- **核心架构(spec 定的)**:加密的"用户可见卡库";**写 = propose(agent 判断)+ integrate(死规矩 commit,enclave 执行,不走 LLM)**;**读 = agentic(agent 自己读索引挑卡)**;向量/RAG 降为可选;永不硬删。

---

## 1. 背景:IO 是什么,为什么记忆是核心

IO = 给用户"已经在用的 AI"装一具常驻 iPhone 的身体:看屏幕、锁屏推送、**长期记忆(记忆花园)**、人格(身份卡)。卖点不只是能记,而是**数据 TEE 加密、用户能验证**。
- 三 repo:`feedling-mcp-ios`(iOS)、`feedling-mcp`(后端+enclave+合约)、`io-onboarding`(skill 文档)。
- 三条接入路线:**A 自建/VPS**(用户自带 agent + 跑 Consumer 中转)、**B API/托管**(填 key,后端当 agent)、~~C 官方App~~(已弃)。
- **记忆是产品的承重墙**:关系真不真实,全看记忆做得好不好。所以这轮聚焦记忆。

---

## 2. 方案迭代历程(怎么走到定稿的)

### 2.1 第一步:摸清现状(代码确认)
- 写(capture)✅、存(加密)✅、写入对账✅;但**检索纯关键词零向量**、**无跨卡整理**、**无记忆 eval**。
- 关键判断:**加密不挡 RAG**——检索本来就在 enclave 内对解密后的明文做。

### 2.2 第二步:hx 的 v2 方案(四阶段)
顺序:**① eval → ② 检索(加向量/RAG)→ ③ 整理(对账)→ ④ route A 收口(不碰)**。
- 借两个 Anthropic 官方视频方法:《The Prompting Playbook》(Generate-Evaluate-Repair 小循环、话说两头、硬能力给工具)、《Evals for taste》(eval 是飞轮、两种判官、LLM 判分先列理由再打分、QA 找茬循环)。
- 评估了开源库:Ombre-Brain / MemPalace / memU / imprint。
- 产出推 router + 开了 PR #9。

### 2.3 第三步:leader 的架构对齐(把 v2 重新框)
leader **没否**,但重定位:
- **草稿(agent 私有,永不浮现) vs 卡库(用户可见,唯一召回源)**。
- **写卡拆 propose + integrate**;integrate 是**死规矩 commit 契约**,enclave 只做校验+状态迁移+decay,**不走 LLM**。
- **召回改 agentic**(agent 读索引自挑),**向量降为可选** → 副作用:**"embedding 在哪算"这个原阻塞项基本作废**。
- 把 hx 的"4 阶段平台管道"判为偏 V1 中心化,要往 V2(agent 判断、平台薄校验)对齐。
- 抛出 Q1–Q5 开放问题 + 4 个要 hx 回的代码题。

### 2.4 第四步:leader 的 spec v1(定稿)+ 两份相邻文档
- **`io-memory-spec-v1`** = leader 把上面全部收敛成**可实现的 spec**(标"骨架已对齐,可进入实现/eval")。**Q1–Q5 大多从"开放问题"变成了"决定"。** 这是**当前权威设计**(§3 详述)。
- 另外两份是**相邻系统**(claw-code 的"指令文件记忆"),不是 IO 卡库本身(§5)。

> 一句话演化:**摸现状 → hx 四阶段 v2 → leader 重框(propose/integrate + agentic)→ leader spec v1 定稿。**

---

## 3. 当前定稿:IO 记忆 spec v1 要点

> 标注:【已定】可实现 ·【待 eval 定参】机制定了、参数 eval 后拍 ·【延后】不阻塞。

**第一性原则:**
1. **propose 与 integrate 分离**:propose(什么值得记=taste,路径分叉)/ integrate(怎么入库=契约,路径无关,enclave 确定性执行,**不走 LLM**)。
2. **草稿 → 卡片 = 提炼发布**:会话内是草稿(私有、永不浮现);卡片是从草稿里"值得的"提拔进库。
3. **不变加密**:enclave 内 per-request 解密,外部永无明文。**ChaCha20 解密 GB/s 级,可以边解;真瓶颈是 LLM 上下文预算,不是解密。**
4. **永不硬删**(用户主动删除除外):supersede/做梦只软删,保留链 → 历史/回滚/"记得过程"自动得到。

**卡 schema v1(每个字段都有一个用它的操作,否则砍):**
- `verbatim`(密文,深读 fetch)、`summary`(一行 label,agentic 索引的接口本身)、`is_open_thread`+`follow_up`(主动浮现)、`topic_tags`、`bucket_refs`(所属桶)、`status`(active/superseded/contradicted/archived)、`superseded_by`/`supersedes`、`valid_from`/`valid_to`(有效期窗口,借 MemPalace)、`importance`/`last_active`/`last_surfaced`/`surface_count`、`salience`、`pinned`/`decay_exempt`、`provenance`{route,committer,confidence,source_ts}。
- 砍掉的装饰字段:card_type 枚举(只留 is_open_thread)、about(移到索引轴)、emotion 分类法(先用单维 salience)。

**索引分两层:**
- **检索层(给 agent)**:扁平、每用户实时生长的"桶集合"(实体和话题混在一起,如 `妈妈`/`工作烦恼`/`我们的纪念日`)。桶类型不作轴。
- **展示层(给用户)**:粗轴 **我们 / 你 / 你的世界**(从原 6 词收敛);花苞=open_thread、常钉=pinned、四季=时间线 是"镜头"不加轴。
- **桶生命周期(IO 自建维护)**:**resolve-before-create**(落卡先读现有桶、能复用就复用);enclave commit 时驳回"太像已有桶的新桶";做梦顺手合并近义桶、降权,**永不删**。

**写入路径:**
- 落卡时机:**会话内不落卡(只草稿)**;落卡在轻巩固(自然停顿/会话收尾/每 N 轮);深做梦更重;"用户说记住这个"=跳质量门、立即落、高 confidence。
- integrate = `commit(op,{...})`,op ∈ insert/supersede(target)/merge(targets);两条路同一 commit 工具;enclave 确定性执行。
- 矛盾时解:默认"更新+更高 confidence"当 active、旧的转 superseded;**用户亲口 > agent 推测**;硬冲突两张都留 + contradicted + 主动浮现问用户。

**语气规格 / Agent Persona**:定位"**常驻、每轮有性格**"(区别于按需检索的卡);**7 维身份卡 = 它的派生展示**。分 0–6 层(核心契约/声音语气/在乎的/相处边界/想要的关系/雷点/在场方式)。

**读取:**
- 公开 recall = **agentic**:`index()`(enclave 解密返回紧凑索引)→ agent 锁范围挑 → `fetch([ids])`(解密 verbatim)→ 精读。**向量 = 可选 scale-out 兜底,非 enclave 内嵌模型**(走 eval 决定要不要上)。
- 上下文注入:每轮预算 ~2600 token(语气规格 ~700 + 索引摘要 ~400 + 召回卡 ~1500)。
- `surface()`:输出"该浮现哪些卡 + 为什么 + 建议语气",是**记忆的主动浮现**,时机决策归 Proactive System(接口已定,逻辑延后)。

**维护:** decay(importance+last_active,affect 加权,pinned/未解决 open_thread 豁免——防 Ombre 永久桶被衰减 bug)【参数待 eval】;做梦 consolidation(轻巩固多跑/深做梦少跑,在 enclave 内,作为 proposer 走 commit;**可视红线:只加法+鸟瞰,永不删/永不静默改用户可见卡**)。

**eval**:**顺序铁律——eval 最先出**。机器判(漏记/记错/类型/R@5+安全死线)+ AI 判(先列理由再打分、两两对比);golden set 还要覆盖 scoping 准确度、label 质量、浮现相关性、**语气规格遵循度**。

### ⚠️ 之前的开放问题,spec 里已拍的关键一条
**Q1(索引存明文还是密文)→ 已决定:保持全加密,enclave 每次请求解密来建索引,不另搞明文摘要。** 理由:ChaCha20 解密很便宜,瓶颈是 LLM 预算不是解密。
→ 即"摘要在密文里、每次解就行",**不再是阻塞**。

---

## 4. 记忆系统现状 vs spec(代码确认)

| 能力 | 现状(代码) | spec v1 要的 |
|---|---|---|
| 写 capture | ✅ 6 类,app.py:9444;prompts.py:75-105 | propose(verbatim+summary+bucket_refs) |
| 存 | ✅ JSONB,明文元数据+密文体;alembic/0001_baseline.py:57-64 | 同款 + 加 schema 字段(进 JSONB,免迁移) |
| 写入对账 | ✅ 0.85/0.55;app.py:8587-8589 | 升级成 commit 契约(insert/supersede/merge) |
| 跨卡整理 | ❌ 仅去重 app.py:7251;有 recap 骨架 app.py:9987-10070 | 做梦 consolidation 走 commit(扩 recap) |
| 检索 | ⚠️ 纯关键词在 enclave 内;context_memory_selection.py | 改 index()/fetch() 两端点,agentic |
| eval | ❌ 仅 proactive_gate_eval.py | 先搭记忆 eval |
| 加密/enclave | ✅ enclave 解密 enclave_app.py:733-770;无 ML 库 | 维持;向量若上不内嵌模型 |

**关键事实**:摘要/标题现在都在密文 `body_ct` 里(没有明文摘要);加字段直接进 JSONB 免迁移;enclave 无 ML 库、不调外部模型。

---

## 5. 相邻系统:claw-code 的"指令文件记忆"(另两份文档)

> 这两份讲的是**另一种记忆**(指令文件/CLAUDE.md 那套),不是 IO 卡库,但**架构同源**,可作 IO 存储层参考。

- **`memory-instruction-files`(现状)**:claw-code 现在怎么用 `CLAUDE.md`/`AGENTS.md`/`.cursorrules` 当记忆——从 cwd 往上找到 git 根逐层收集 → 哈希去重 → 按层级渲染进系统提示,带字符预算(单文件 4000/总 12000)。**纯目录约定,无语义无 embedding。**
- **`2026-06-15-multi-user-memory-store-design`(改造)**:把上面做成**独立的多用户加密存储 crate**(Rust+Postgres)。套路:**服务端永不见明文**(正文客户端加密 blob,去重靠客户端哈希、预算靠客户端字符数)、**多租户 RLS 隔离**、层级用 `ltree`、检索靠祖先匹配+标签+预算,**不走向量**。

**与 IO 的关系**:**不是同一种记忆,但"加密+多用户+服务端零明文+元数据驱动"的套路一模一样。** 可借鉴它的**存储层做法**(RLS 隔离、哈希去重、元数据预算);但 IO 卡 schema 更重(状态机/桶/decay/索引),**不是拿来直接存 IO 卡**,是"同族表亲/范式参考"。
**值得问团队**:IO 卡库的存储层要不要和这个 crate **对齐范式甚至共用底座**?

---

## 6. 开源库借鉴结论(spec 已收口)

| 库 | 协议 | IO 拿什么 |
|---|---|---|
| Ombre-Brain | MIT | affect 加权 decay 公式、主动浮现思路、lifecycle schema 灵感 |
| MemPalace | MIT | 两段式"粗筛→精读"形状、有效期窗口漂移链模型(桶是文件夹派生不维护——IO 桶实时自建维护) |
| imprint-memory | **MIT**(spec 确认) | jieba 中文分词(解"东方Project")、dedup/decay 命名;混合检索 RRF 是其强项但 IO 走 agentic 所以不用 |
| memU | — | "充分性检查"一个点;其 folder 当存储是反面教材(破坏统一契约),当索引 OK |
| ex-skill | MIT | onboarding 蒸馏聊天→分层 persona、冲突记录优先、merge-不覆盖+correction-加权 |

> 许可证更正:**imprint 是 MIT**(原 hx 文档误标 AGPL;AGPL 的是 memU-server)。

---

## 7. 我的最终结论与建议

1. **spec v1 是当前权威设计,照它落地。** 它收敛了全部讨论,且把 Q1 等拍成了决定(加密+每次解,不贵)。
2. **eval 仍是第一步**(spec 也是"铁律")。golden set 除了漏记/记错/R@5,要加 **scoping 准确度 + label 质量 + 语气规格遵循度**;AI 判分先列理由再打分、两两对比。
3. **实现落点优先级**(我的判断):① 搭 eval → ② commit 契约 + schema 字段(JSONB 免迁移)→ ③ 召回改 index()/fetch()(扩/重写 context_memory_selection.py)→ ④ 做梦走 commit(扩 recap worker)。decay/做梦参数留给 eval 定。
4. **存储层范式**:评估 IO 卡库存储是否对齐 claw-code 的 memory-store crate 范式(RLS/哈希去重/零明文),能统一底座更省事。
5. **向量/embedding 不急**:agentic 召回主路径不需要;作为可选 scale-out 兜底,走 eval 决定再上——少一个阻塞。
6. **产品差异化(护城河)**:用户可控 + 版本/回滚 + 永不硬删,正打 #keep4o"记忆被抹"痛点;别做"对抗大厂审查"(政策风险),要自由引导用户自带开源模型 key。

---

## 8. 术语速查

| 词 | 人话 |
|---|---|
| 卡库 | 用户可见的记忆花园,AI 召回/浮现只看它 |
| 草稿 | agent 自己脑子里的私有记忆,永不浮现 |
| propose / integrate | 提议(LLM 判断记不记/记成啥)/ 归档(死规矩落库,enclave 执行,无 LLM) |
| commit 契约 | 死板提交窗口:递操作单(insert/supersede/merge)→ 照规矩校验+执行 |
| agentic 召回 | agent 自己读索引(目录)挑卡,而非平台搜好喂它 |
| index / fetch | 目录(桶名+一行摘要+状态) / 取详情(解密 verbatim) |
| 桶 bucket | 卡片归属的实体/话题(妈妈/工作烦恼);resolve-before-create |
| status 状态机 | active/superseded/contradicted/archived;永不硬删 |
| decay | 旧卡权重慢慢降;pinned/未解决 open_thread 豁免 |
| enclave | CPU 保险柜,唯一能解密的地方,里面没有 LLM |
| 语气规格 | 常驻人格(0-6 层),7 维身份卡是它的派生展示 |

---

## 附:产出物 / 待办

- **已产出**:PR #9(`teleport-computer/feedling-mcp-ios`)收 3 份文档;均同步 Router。
- **待办**:① 搭记忆 eval(golden set + 打分脚本)② 回 leader 4 个代码题(Q1 已可答:摘要在密文,spec 已决定每次解)③ 照 spec 实现 commit 契约 + index/fetch ④ 评估 IO 存储层是否对齐 memory-store crate 范式。
