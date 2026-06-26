# IO Memory v1 · 需要别人做的事(给 zhihao 为主)

> 2026-06-26 · CC 整理 · **给 zhihao 读为主**(含跨人依赖,谁的活都标了)。
> 大前提:**route B(hosted)废弃 → 全部走 VPS / route A 形式。** memory v1 后端**已合 test**,写已是 v1;skill v1 + 文档已对齐(纯 agent-first 读,无 ambient)。
> 工作流:凡需要"一起定"的,都是 **CC 写方案 → Codex 审 → 你(zhihao)对 → 共识 → 执行**。本文只是把待办摊开,不是已定方案。

---

## TL;DR — 4 件待办 + 1 个归属待定

| # | 事 | 谁的活 | 状态 |
|---|---|---|---|
| 1 | 🔴 **identity-init crypto 坑** | 一起定 + 你实现 consumer 侧(或 hx 后端) | **route A 跑通的最后卡点** |
| 2 | ⏳ **route B 下线 = 一锅端死代码** | 你(route B owner) | 等你下线 route B 时一起扫,**现在别删** |
| 3 | 🔌 **tool gateway + 鉴权 token** | 你 runtime/consumer + hx 对 token 边界 | route A agent 怎么调到 IO 工具 |
| 4 | 🧭 **onboarding 在新架构下怎么设计** | **归属待定(你 or Seven?)** | 架构换了(route B→VPS),要拍归属 |

> Seven 的活(**不是你的**,列在附录防混):Garden UI v1 渲染 + `prompts_v1` 接入。
> 老数据迁移(M2 卡 → bucket/thread adapter)是 **hx 后端**的活,已在计划里,不用你管。

---

## 1. 🔴 identity-init crypto 坑(route A 最后卡点)

**现象**:route A 是 **HTTP-direct**(agent 直接打后端 HTTP 端点,MCP 已删)。
- **记忆写**没问题:agent 把**明文** action 提交 `/v1/memory/actions`,**服务端用「用户 content 公钥 + enclave content 公钥」建 shared envelope 再存**(`core/envelope.py:_build_shared_envelope_for_store`;**建信封只用公钥、不碰私钥**,enclave 私钥留作之后读取解密)→ agent 完全不碰 crypto。✅
- **identity init 卡住**:`/v1/identity/init` 现在收的是一个**已经建好的信封(pre-built envelope)**,**没有"服务端帮建信封"的路径**。route A 的 agent **没有 crypto / 没有密钥**,建不出这个信封 → **identity 在 route A 下写不进去**。

**两个解法(待一起定)**:
- **(a) 后端加"服务端建信封"的 identity init**(和记忆对称):agent 提交**明文** identity,后端用同样的「用户公钥 + enclave 公钥」建 envelope 再存。→ agent 永远不碰 crypto,和记忆写一个模型。**hx 后端活。**
- **(b) consumer 本地建 identity 信封**:consumer 本来就在本地给 chat 建信封(手上有所需 key material),复用它给 identity 建。→ **你(zhihao)consumer 活。**

**关键决策点 = identity init 是否允许走 server-build-envelope**(**不是**"identity 私钥住哪"):
- 建 envelope **只需要收件人公钥**(用户 content 公钥 + enclave content 公钥),**不需要用户私钥**。所以技术上后端完全能像记忆一样帮 identity 建信封——(a) 没有密码学障碍。
- 真正的问题是**产品/安全边界**:identity init 准不准复用记忆那条 server-build-envelope 路径?
  - 准 → 选 **(a)**,最顺(和记忆一个模型,agent 永不碰 crypto)。
  - 产品/安全要求 identity 必须由**客户端/consumer 本地建** envelope → 选 **(b)**。
- **→ 需要你(zhihao)确认:identity init 能否复用 memory actions 的 server-build-envelope 边界**(而非"identity 私钥是否在 enclave")。

**下一步**:CC 写详细方案(含 (a)/(b) 取舍 + 端点/数据流)→ Codex 审 → 你对密钥托管 → 共识 → 谁实现谁实现。

---

## 2. ⏳ route B 下线 = 一锅端死代码(别现在删)

route B 废了之后,下面这些**全是死代码**,**等你下线 route B 时一起扫掉**(现在 route B 可能还在跑,**提前删会把线上 route B 搞挂**):

- `backend/context_memory_selection.py` —— 服务端按聊天历史**自动注入**记忆(= 老的"读")。
- chat history 响应里的 `context_memories` 字段。
- `memory_readside_core.py` 里的 `ambient` 参数 / `ambient_score`(气氛灯,本来就没人 push,死代码)。
- `coerce_runtime_action`(route B 的 action 归一)。
- `hosted/context.py`、`hosted/turn.py`(route B hosted 回合)。
- route A/B 等价逻辑(两条 route 行为一致那套)。
- `onboarding_validate` 的 `model_api` 分支。

**为什么不现在删**:route B 还在线上 → 它依赖 `context_memories` 等。**等 route B 真下线(或确认没人用)再删**,一锅端。
**读现在怎么办**:skill + 文档已改成 **纯 agent-first**(agent 自己 search/fetch),所以**行为层面已经对了**;上面只是后端残留代码,不影响新行为。
**你原本"把读接进 route B loop"那条 = moot 了**(route B 没了,没 loop 可接;读全靠 agent 主动调工具)。

---

## 3. 🔌 tool gateway + 鉴权 token(route A 怎么调到 IO)

route A 的 agent 靠 **HTTP-direct** 调 IO 记忆工具:`/v1/memory/index | fetch | actions | buckets | threads`。VPS 形式下,你 runtime/consumer 要保证:
- **端点对 agent 可达**(agent 带用户 key 直接 HTTP 调)。
- **鉴权翻译**:runtime token → 用户的 tool gateway 鉴权(走 route A)。
- **key 复用**:已定复用 `FEEDLING_API_KEY`,CLI 模式继承 env(已在)。

**待对接 = 鉴权 token 边界(hx × zhihao)**:用什么 token、scope 多大、谁签发。这条 CC 文档里一直挂着"待对接",需要你和 hx 拍一下。

---

## 4. 🧭 onboarding 在新架构下怎么设计(归属待定)

架构换了:**memory 不再是 onboarding gate(A')**,identity 是最低基线,0 记忆用户合法,onboarding 完成 = **identity + live-loop**(不是记忆门槛)。后端 A' 已合 test。

但**新 VPS 架构下整个 onboarding 流程**(route B→VPS、HTTP-direct、identity-init crypto 见 §1)需要重新设计。

**归属问题(要拍)**:
- 架构那半(route B→VPS 接入、identity init 怎么走、consumer bootstrap)= 偏 **你(zhihao)** runtime/consumer。
- 产品/UX 那半(用户第一次怎么建身份、第一屏体验)= 偏 **Seven** 产品。
- **→ 请 hx/Seven 拍:onboarding 重做主要谁牵头。** §1 的 identity-crypto 解法直接影响这块。

---

## 附:Seven 的活(不是 zhihao,列出防混)

- **Garden UI v1 渲染**:老 type/title/tab 渲染 → **bucket / thread / summary / content**。合并后 Garden 会坏到 Seven 重做为止(hx 用 P7 在 iOS 端临时隐藏 Garden 过渡)。
- **`prompts_v1` 接入**:`backend/memory/prompts_v1.py` 是给 Seven 的 hook。CC review 标的两项交接给她:(a) prompts_v1 inert /(b) resolve-before-create 词表注入(把现有 bucket/thread 列表塞进写入提示逼复用)。
- Seven 还要拍:**bucket 平铺 vs 层级**(CC 建议平铺)。

---

## 附:hx 自己的活(给你对账,不用你管)

- 老数据迁移 adapter(M2 卡 → bucket/thread)+ 后台回填口。
- P6 cleanup:verify/retype/TAB_FOR_TYPE/MEMORY_TYPES legacy、admin floor 文案、4 个 skipped tests。
- skill v1 合并到 io-onboarding(等全改完 hx 决定;main 是线上,无 test 分支,暂不合)。
