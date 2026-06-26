# IO Memory v1 · 完整总结(做了什么 + TODO)

> 2026-06-26 · 作者:CC(hx）· 面向团队宣讲 / router 推送。
> 范围:memory v1 + onboarding 脱钩 + route A(VPS)读写闭环。**route B(hosted/API)正在弃用,全部收敛到 VPS / agent 形式。**
> 状态约定:✅ 已完成且在 `test` 测过绿 · 🟡 在分支/待合 · 🚧 TODO。

---

## 0. 一句话

把记忆系统从"6 种 type / 3 个 tab / 服务端定期扫上下文 / 气氛灯自动注入"**重做成 v1**:**一种卡(bucket + thread)、agent 自己读、agent 自己在对话里记、服务端只负责加解密与存储**;并把 **onboarding 从"记忆门槛"脱钩成"identity 先行"**,补回了 route A 建身份的能力。后端 v1 已全部合入 `test` 并测过绿。

---

## 1. 架构(VPS / route A,三个角色)

```
用户聊天
  │
  ▼
Consumer(常驻服务)        ← 聊天传输 + 解密。轮询后端 → 解密用户消息 → 交给 agent → 把回复加密写回
  │
  ▼
Agent(用户自己的 Claude Code / Hermes = 大脑)  ← 判断 + 调 IO 记忆工具(HTTP 直连后端,带 per-user key)
  │
  ▼
IO 后端 + Enclave          ← 端点/工具 + 写时建信封 + 读时 enclave 解密 + 存储
```

- **agent 发明文、做判断;加解密全在服务端/enclave;agent 不碰密钥。**
- route B(hosted/model_api)那套"服务端跑整个 loop"正在弃用,统一到上面这条。

---

## 2. 读流程(纯 agent-first)

```
每轮: identity 常驻带上(人设)
长期记忆(agent 觉得相关才查):
  feedling_memory_search(query?/bucket?/thread?) → 看目录(bucket/threads/summary/视标,不含正文)
  → 挑 1-3 张 → feedling_memory_fetch(ids)取正文(enclave 解密后返回)
  → 要来龙去脉 → follow_thread(某thread)跨桶串
```
- **没有 ambient / 气氛灯、没有 recall 兜底、没有 context_memories 自动注入、闲聊不查。** 该查 agent 自己调。

## 3. 写流程(agent 在 loop 里自己记)

```
用户聊天 → agent 判断「这轮有没有长期值得记的」
        → feedling_memory_write(明文: add / supersede / delete)
        → 后端用「用户 content 公钥 + enclave content 公钥」建加密信封(server-build)→ 存
```
- **没有服务端定期扫上下文 capture**(那是 route B 的;route A 没有)。
- agent 在**自然断点**(冷场 / 聊若干轮 / 用户收尾)判断,**0-2 张 / 次,并优于增**(Seven「落卡」baseline,写在 skill 里)。
- **改口 = supersede(soft)**:旧卡软退场、链新卡、**永不硬删**;只有用户明确"删/忘"才真删。
- identity 同一套:agent 发明文 → 后端建信封。

---

## 4. v1 卡模型(一种卡,统一格式)

| 字段 | 是什么 |
|---|---|
| `bucket` | 主话题(**单选**,平铺、可复用):`我们的关系`/`工作`/`妈妈`… |
| `threads` | 线索(**多选**,横穿桶):`工作压力`/`蛋子`/`冷战`…;`follow_thread` 靠它跨桶串 |
| `summary` | 一句话:这卡是啥(agent 一眼判断要不要读正文) |
| `content` | 正文,MD 三段:**记忆 / 上下文 / 使用提示**;fetch 才返回 |
| `importance` | 0-1:**看不看**(对长期理解用户多重要),写时打、不随时间变 |
| `pulse` | 0-1:这件事在 AI 自己心里激起多大波动(**不进检索排序**,只影响表达色彩) |
| `status` / `source` / `occurred_at` / `last_referenced_at` | active/superseded/archived · chat/screen · 发生时间 · 上次被用到(decay 从它派生) |

- **decay**:读时派生(`(now - last_referenced_at)/half_life`),被用到就回升;综合排序 ≈ 相关性 × importance ×(1-decay)。
- **resolve-before-create**:写卡时把现有 bucket/thread 词表喂给模型逼复用(防词表膨胀)。

---

## 5. ✅ 已完成(都在 `test`,测过绿)

| # | 事 | 位置 / 备注 |
|---|---|---|
| 1 | **v1 卡 schema**(bucket/thread + importance/pulse + supersede soft + decay 派生) | `feat/memory-v1-clean-schema` 已合 test |
| 2 | **5 个端点** `index / fetch / actions / buckets / threads`(读写闭环) | `backend/memory/routes.py` |
| 3 | **读 = 纯 agent-first**(index→fetch→follow_thread;无 ambient/recall/context_memories) | readside core |
| 4 | **写 = agent in-loop**(actions: add/supersede/delete;服务端建信封;无服务端定期 capture) | `backend/memory/actions.py` |
| 5 | **onboarding 脱钩(A')**:memory 不再是门槛,identity 先行,0 记忆合法 | `df513d4` 已合 test(69 passed) |
| 6 | **identity-init 服务端加密**:`/v1/identity/init` 收明文(server-build)或 pre-built 信封(iOS);补回删 MCP 后 route A 建身份的缺口 | `8b7c39d` 已合 test + **已部署 test CVM** |
| 7 | **prompts_v1**(Seven 读写提示词,接入 capture/hosted) | `backend/memory/prompts_v1.py` |
| 8 | **老数据自动迁移**(`to_v1_card` 每次 load 跑 + enclave 内层适配);老卡无缝兼容 | `backend/memory/service.py` |
| 9 | **API 模式 onboarding 导入 → 产出 v1 卡**(用户传材料 → 模型解析 → bucket/thread 卡 + identity) | `backend/hosted/history_import.py` |
| 10 | **ambient / 气氛灯 全废**(Seven):文档 + skill 清干净 | 全局 |
| 11 | **per-user API key 鉴权**(所有端点) | `accounts/auth` |
| 12 | **加密模型**:agent 不持密钥;写=后端建信封(双公钥),读=enclave 解密 | `core/envelope.py` + enclave |
| 13 | **route A skill v1**(一种卡 / Seven 落卡 baseline / 无 ambient / HTTP-direct / identity-first;2 轮 Codex review) | io-onboarding `feat/skill-memory-v1`(已推 origin,test-flavored) |
| 14 | **测试**:v1 套件对当前 test = **100 passed / 4 skipped / 0 failed** | identity init(8 新)/ readside / write / bootstrap / conformance / import |

---

## 6. 🟡 在分支 / 待合(不在 test)

| 分支 | 内容 | 处置 |
|---|---|---|
| `feat/memory-m-readwrite-consistency` | route-A recall(已弃)+ **敏感 fetch gate** | recall 丢;敏感 gate(flag 默认关、休眠)以后单独 cherry-pick;分支基本可弃 |
| `feat/hosted-memory-tools` | **全部 v1 文档** + 1 个旧代码 `433d9af`(route-B 缠绕,丢) | docs 保留;433d9af 丢 |
| io-onboarding `feat/skill-memory-v1` | v1 skill(已推 origin,**test-flavored**:指 origin/test + ENCLAVE_URL) | 测完上 io-onboarding main(见 TODO) |
| io-onboarding `feat/memory-m-http-skills` | 早期窄版 route-A HTTP skill | VPS 接线摘进 skill-memory-v1 后删 |

---

## 7. 🚧 TODO

### 7.1 Memory Garden 页面设计(**hx 来做**)
- 老渲染(type/title/tab → Story/About me/TA在想)**要重做成 v1 卡**:`bucket / threads / summary / content(三段)/ importance / pulse`。
- 合并后 Garden 会坏到重做为止;过渡手段:P7(iOS 端临时隐藏 Garden)。
- bucket 平铺展示 + thread 横穿(同一 thread 串不同桶);pulse 只影响表达,不做排序。

### 7.2 测试用例(待补 / 让 Codex 跑端到端)
- **后端单测**:已绿(100 passed)。
- **端到端**(待 Codex 执行):
  - API 模式:register → setup → test → 传材料 → history_import → 验证产出 **v1 卡**(bucket/threads/三段content/importance/pulse)+ identity + greeting。
  - identity-init 明文分支:真打端点 → 201、可解密;pre-built 信封路径仍 201(向后兼容)。
  - route A(測法 A):agent 指向 `feat/skill-memory-v1` raw URL + FEEDLING_API_URL/KEY/ENCLAVE_URL → test,跑 bootstrap → 验 v1 行为。
- **v1 行为 checklist**:identity 先行 / 0 记忆可完成 / 无 floor/verify gate / 卡是 bucket-thread / 无 ambient / supersede 不硬删 / 落卡克制(0-2)。

### 7.3 下一版本 eval(下一阶段重点)
- **目标**:把"记得准不准、想得起来不"变成可量化。
  - **写 eval**:该记的有没有记(起点 bug:用户自然陈述的持久事实如"狗叫蛋子"曾漏记)、不该记的有没有克制、supersede 有没有正确纠错。
  - **读 eval**:该想起时 agent 有没有主动查到、检索相关性。
- **推理层 / 画像(deferred → 这一版才做)**:agent 对用户的"猜测/画像"(聊天 + 屏幕都喂),单独一层,和 eval 一起、上线后做。
- eval 驱动调"判断"(prompts_v1 + skill 的写入指引),而不是改 schema。

### 7.4 route A skill 上正式版(io-onboarding main)
> 现在是 test-flavored,合 main(线上;该 repo **只有 main、无 test 分支**)前要:
1. 把 `origin/test` 翻回正式发布分支(现在为测试临时指 test)。
2. 修 `skill-api.md` / `skill-chat-client.md` 里残留的 main 自引(若那两条路也上 v1)。
3. 从 `memory-m-http-skills` 摘:`io_cli.py perception` 注册为 runtime native tool、memory HTTP readiness/smoke、`agent_name` vs runtime label 分离。
4. 删 `memory-m-http-skills`,合 skill-memory-v1 → main。

### 7.5 test → main(最终合并,**非特别动作,现在不用操心**)
- **当前主线就是 `test`**:这一版相当于在 test 上开发一个新项目,所有功能都合 test、只在 test 测。
- **`main` 本身也是内测版**,不是面向大众的正式生产。
- 最终上线**可能就是 `test` 直接合 `main`** —— 顺手一步,**不是"大发布"、不需要专门协调**。
- → 现在**只管在 test 测好**就行。

### 7.6 route B 死代码清扫(等 route B 真下线一锅端)
- `context_memory_selection.py` / `context_memories` 字段 / readside `ambient` 参数 / `coerce_runtime_action` / `hosted/context.py` / `hosted/turn.py` / model_api running-capture / route A/B 等价逻辑 / onboarding_validate model_api 分支。
- **现在别删**(route B 可能还在跑);下线后扫。

### 7.7 杂项收尾
- `prompts_v1.py` 的 `MEMORY_CONTEXT_FRAMING_V1` 还残留一句 "Ambient memories are background color" → 清(Seven 拥有该文件)。
- 敏感 fetch gate(memory-m `40c3659`)在开 sensitive flag 前 cherry-pick 进 test。
- P6:verify/retype/TAB_FOR_TYPE/MEMORY_TYPES legacy、admin floor 文案、4 个 skipped tests。

---

## 8. 分工速查

| 谁 | 负责 |
|---|---|
| **hx** | 后端 v1 地基(schema/端点/迁移/identity-init)· **Memory Garden 页面设计** · 下一版 eval · skill 收口 |
| **Seven** | prompts_v1 文案拥有/调优 · Garden v1 产品方向 · 落卡 baseline 源 |
| **zhihao** | runtime / consumer / tool gateway(VPS)· route B 下线 + 死代码清扫 · consumer 自更新(已上 test) |
| **Andrew** | TEE / CVM 部署边界(已确认 enclave 内操作明文 OK) |
| **Codex** | review + 跑端到端测试用例 |

---

## 9. 关键决策(口径,别再反复)
- **route B 弃用,全收敛 VPS / agent 形式。**
- **读 = 纯 agent-first**(无 ambient / 无 recall 兜底 / 无 context_memories)。
- **写 = agent in-loop**(无服务端定期 capture;断点判断、0-2 张、supersede 不硬删)。
- **memory 不是 onboarding 门槛,identity 先行,0 记忆合法(A')。**
- **agent 不碰密钥**:写=后端建信封,读=enclave 解密。
