# IO Memory v1 · 全景 + 执行总纲(理清所有内容 · 给 Codex review)

> 2026-06-25 · CC · 目的:把记忆 v1 的【产品现状 / 架构 / tool 层 / 设计 / 问题 / 执行 / 分工】一份理清,供 Codex review + 团队对齐。
> 详细分册:`IO-memory-v1结构定稿-bucket-thread.md`(结构)、`IO-memory-v1-vps接入与onboarding脱钩-执行方案.md`(VPS 三步)、`IO-memory-v1-bootstrap-gate死锁-与codex讨论.md`(gate)、`IO-memory-v1-给zhihao交付-工具与调用流程.md`(zhihao)、Seven《落卡+Dream 完整方案》。

---

## 一、产品现状(用户视角)
**IO = 给用户的 AI 一个 iPhone "身体"**:看屏幕(感知)、推锁屏、**长期记住用户(记忆花园)**、有人设(身份卡)。全程端到端加密(enclave 解密)。

**用户三种接法(route):**
| route | 怎么接 | 谁跑 AI |
|---|---|---|
| C 官方 app | 装 app 直接聊 | IO 全托管 |
| B 模型 API key | 给 key,IO 替跑 | IO hosted runtime |
| **A 自己 VPS** | 自己跑 Claude Code/Hermes 接 IO | **用户自己**(本文主线)|

**onboarding** = 第一次设置:AI 认识用户(建身份 + 初始记忆)→ 接通聊天 loop → 发第一句问候。
**hx 在做 = route A(VPS)+ 记忆 v1。**

---

## 二、架构(组件 + 放哪)
```
用户 iPhone 发消息
   │
 [backend feedling-mcp]  端点 /v1/* + 加密 + 事实源(消息/记忆/身份/屏幕)
   │ /v1/chat/poll
 [consumer]  feedling-chat-resident(VPS,tools/chat_resident_consumer.py)= 转发管子
   │ 喂消息 / 收回复(帮聊天建 envelope)
 [agent]  用户的 Claude Code / Hermes(VPS)= 大脑:读 skill → 思考 → 调端点 → 回复
   │ 调
 [工具层]  现在 HTTP-direct(MCP 已删)→ agent 自己 curl 端点;未来 = zhihao tool gateway
   │
 回到 backend /v1/*
```
- **backend**:端点 + 加密 + 事实源。能力源。
- **consumer**:纯转发(poll → 喂 agent → 回写 `/v1/chat/response`),帮聊天回复建 envelope。**不定行为。**
- **agent**:大脑。**所有"判断 + 调用"都在这,按 skill 走。**
- **skill**:`io-onboarding` repo 的 `skill.md`(base)+ `skill-resident-agent.md`(route A 档)。agent 从 GitHub raw 拉。**= AI 的使用说明书。**
- **记忆和感知是同一套 `feedling_*` 工具的兄弟**(memory→/v1/memory/*,screen→/v1/screen/*)。

---

## 三、tool 层澄清(端点 vs 工具 vs gateway)—— 关键
- **端点**(`/v1/memory/index`)= 能力(hx 写)。agent **不能按名字直接调**。
- **注册成"agent 能按名调的工具"**(`feedling_memory_search` + schema + 执行翻译)= 要 **MCP server 或 tool gateway** 干。
- **现在 = HTTP-direct(MCP 6/12 已删,无 gateway)**:**没有注册层**;agent 靠 **skill 读懂"查记忆=`POST /v1/memory/index`",用自己的 curl/HTTP 去打**。
- **所以现在 hx 暴露的 = 裸端点 + skill,不是打包工具。**skill 在 HTTP-direct 下**充当工具定义**(讲清端点/鉴权 X-API-Key/payload/何时调/加密)。
- **未来**:zhihao tool gateway 把端点重新注册成工具(名字 + auth 翻译 runtime-token→用户 + payload 翻译 `write{op:add}→{type:memory.add}`)。

| 时代 | 谁把端点变工具 | agent 怎么用 |
|---|---|---|
| 老 MCP(已删)| MCP server | 注册工具,按名调 |
| **现在 HTTP-direct** | **没人,skill 顶** | agent 读 skill → curl 端点 |
| 未来 gateway | zhihao gateway | 重新注册,按名调 |

**核实(git + 代码)**:MCP server 确实删了(commit `e4c8bbc`/`454b923`,repo 无 mcp .py);**感知不是特殊 tool,也是 HTTP 端点**(`/v1/screen/*`),连原"MCP-only"的 decrypt 都迁成 HTTP(`/v1/screen/frames/<id>/decrypt`,在 enclave;changelog "MCP→enclave")。
> **感知 = 活证据 + 现成模板**:它现在就靠"端点 live + skill 教 + agent curl"在线上跑。**记忆 v1 照感知改 skill 即可** —— 坐实 hx 直接 HTTP-direct 上 VPS 可行,不需要 MCP/gateway。
**为什么 route A 用 HTTP+skill 而非塞 tool**:VPS 的 agent 是【用户自己的 runtime】,我们进不去、不能直接注册 tool;只能从外面给(MCP 装进去 / HTTP+skill 让它自己 curl),现选后者省 server 成本。route B 因 runtime 是 IO 自己的,才能直接注册 tool —— 这是 A/B 的分水岭。

---

## 四、v1 记忆设计(一句话各块)
- **卡 = 1 种**(无 type/tab):`bucket`(单选话题)+ `threads`(多选线索)+ `summary` + `content`(MD三段)+ `importance`(看不看)+ `pulse`(AI 自己被触动多大,不进排序)+ status/source/occurred_at/last_referenced_at(decay 读时派生)。
- **读 = 两段式(镜像感知 analyze→decrypt)**:`search`(看目录,无 content)→ `fetch`(取正文,回写 last_referenced_at)+ 每轮 `ambient`(气氛灯 importance×pulse×recency)。`follow_thread`=search(thread=X)。
- **写 = 断点 + 小肚量(0-2)**:`add` / `supersede`(soft,不硬删);`merge→supersede`(揉成更全的再覆盖)、`noop→skip`;**supersede 必须真实 target_id**(凭空不许)。先 `buckets`/`threads` 拿词表复用(resolve-before-create)。
- **同模式**:consumer 转发 → agent 判断该不该查/记 → 自己调 tool。和感知一模一样。

---

## 五、现状对账(谁已 v1、谁还老)
| 面 | 状态 |
|---|---|
| 后端 schema + 写 | ✅ **v1 已合 test**(`f7e3db7`)、coerce 接 loop、写 live |
| 后端读注入(route B context_memory_selection)| ⚠️ **还老**,等 zhihao 接 v1 读 |
| **VPS skill(route A 说明书)** | ⚠️ **还老**(6 类型/add_moment/4-pass/floor)→ 步骤 1 改 |
| onboarding bootstrap gate | ⚠️ **还按 floor 卡**(新用户卡)→ 步骤 3 改 |
| 工具层 | HTTP-direct(无 gateway)|
| Garden UI / 提示词 | Seven |

---

## 六、bootstrap-gate 问题 + A'(详见 gate 讨论稿)
- **问题**:onboarding 两道门(`/v1/identity/init`、`/v1/chat/response`)+ `/v1/onboarding/validate` 三分支 + `/v1/bootstrap/status` 都按 **memory floor** 卡。v1 小肚量填不满 floor → **新用户卡在门外**(老 `_count_by_tab` 会永久死锁,但 total-shim 挡住了,变成"新用户被卡")。
- **A'(Codex,CC 采纳)**:**Memory 不是开门钥匙,identity 才是。** memory 不卡门;identity 独立先建;chat 留 identity 轻 gate(防 day-1 飘);onboarding 完成 = identity + live-loop,不看记忆数量。
- 后端 5 处脱钩 + P6 两步退役 tab/floor + 存量卡不迁移(读时 adapter→v1)。

---

## 七、执行(三步 + hx 直接上 VPS 的新流程)
**三步:**
1. **步骤 3 后端脱钩 gate(A')= Codex** ← 前提(不脱钩新用户卡)。
2. **步骤 1 skill 改 v1 = hx**:主循环 search/fetch/write(HTTP-direct,**原生 payload `POST /v1/memory/actions`,不碰 /add+type**)+ onboarding 脱钩(去 4-pass/verify-gate,identity 独立)。
3. **步骤 2 提示词 = Seven 内容**(落卡/读取/Dream)搬进 skill + prompts_v1;hx/接线翻译 merge→supersede。

**hx 直接上 VPS 的新流程(本次变更):**
- 原计划:等 zhihao 改完 runtime/consumer 再加 VPS。**太慢、卡测试。**
- **现在:hx 直接 HTTP-direct 上 VPS**(route A 本来就 HTTP-direct,不依赖 gateway)。skill 直接指 v1 端点、agent curl;**agent 只提交明文 action 到 `/v1/memory/actions`,服务端/enclave 建加密 envelope**(Codex 纠正:agent 不自建 envelope、不需 crypto)。
- **能现在做**:改 skill + **老用户测 v1 读写**(老用户有卡,gate 能过)。
- **还得等 Codex**:新用户跑通要 步骤 3 后端脱钩。
- **zhihao 之后**:重构 consumer/runtime/gateway 时,吸收 hx 这版 HTTP-direct。

**部署顺序(简化版,hx 直接):**
```
① 后端 A' gate(Codex)  → ② skill HTTP-direct v1(hx)→ ③ Seven 提示词 → ④ 验收
（gateway 那步推后,归 zhihao 后续重构）
```

---

## 八、分工
| 谁 | 干啥 |
|---|---|
| **hx** | 后端 v1 端点(已)+ **skill 改 v1(HTTP-direct,流程骨架)** |
| **Codex** | 步骤 3 后端脱钩 gate(A')+ P6 退役 tab/floor |
| **zhihao** | tool gateway(端点→工具)+ 统一 runtime/consumer + route B 读接 loop + auth 翻译 |
| **Seven** | 落卡/Dream/读取**提示词内容**(搬进 skill + prompts_v1)+ Garden UI v1 + eval |

> skill 改:**流程骨架 = hx,提示词内容 = Seven**(她文档有提示词、没流程)。

---

## 九、待办 + 验证
**现在能动**:hx 改 skill v1(HTTP-direct)→ 老用户测 search/fetch/write。
**等 Codex**:步骤 3 后端脱钩 → 新用户 0 记忆能 onboarding(identity 建→开聊→主循环跑→bootstrap/status 按 identity+live-loop 判完成)。
**等 zhihao**:gateway + 统一 runtime + route B 读接 loop。
**等 Seven**:提示词 + Garden + eval。

**验收(新用户)**:0 张记忆 → identity 能建 → 开聊不被 floor 卡 → `search/fetch/write` 跑通。
**验收(老用户)**:存量 type 卡 → adapter 读出 v1 → 照常。

---

## 待 Codex review 的点
1. tool 层澄清(三、)对不对:现在确实无 gateway、HTTP-direct、skill 顶工具定义?
2. hx 直接 HTTP-direct 上 VPS(七、)有没有坑:skill 让 agent 原生 curl /v1/memory/actions + 自己建 envelope,可行?老用户测可行?
3. 步骤 3 后端脱钩(A')是新用户唯一硬前提,确认?
4. 分工(八、)有没有错位?
