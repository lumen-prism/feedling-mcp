# IO Memory v1 · bootstrap/onboarding gate 与新结构的冲突(CC ↔ Codex 讨论稿)

> 2026-06-25 · 背景:clean v1 已合并并部署到 test(`f7e3db7`)后,Seven 指出 v1 结构会和 onboarding 的 bootstrap gate 冲突。本文 = 完整复述 Seven/分析的发现 + CC 事实校正 + CC 方案,供 CC↔Codex 收敛最佳方案。
> 配套:`IO-memory-v1结构定稿-bucket-thread.md`、`IO-memory-v1-实现spec-给codex.md`。

---

## 一、Seven 分析的完整复述(原文截图)

### 卡的本质:一条链,两个强制点
所有阻塞都源自同一条链(全在后端):
```
_bootstrap_state()                               bootstrap/gates.py:39
  → memory_service._count_by_tab(moments)        按 type→TAB_FOR_TYPE 数 story/about_me/ta_thinking
  → _per_tab_floors_for_days(days)               按关系天数给每个 tab 的 floor
  → needs_memory = (story floor 或 about_me floor 没到)
```
这个 `needs_memory` 喂给两个服务端强制点:
1. **`_gate_bootstrap_for_identity_init`**(gates.py:252)→ identity/routes.py:72 调用 → floor 没到就拒绝 `/v1/identity/init`。
2. **`_gate_bootstrap_for_chat`**(gates.py:177)→ chat/routes.py:232 → floor 没到就锁住可见聊天(只留一个 verify-ping 例外)。

**为什么新结构一上来就死锁(Seven 原话)**:v1 一种卡、不写 type → 后端默认 type=fact → 全落 about_me;story、ta_thinking 永远是 0 → story floor 永远不达标 → `needs_memory` 永远 true → identity_init 被拒 + 可见聊天打不开。注意:写卡本身不报错(type 默认成 fact),卡死的是 floor/gate,不是 add。

### Layer 1 — 后端硬卡(Codex 的活)· 要拆的代码点
| 文件 | 东西 | 现在干嘛 |
|---|---|---|
| `bootstrap/gates.py` | `_bootstrap_state` / `_gate_bootstrap_for_identity_init` / `_gate_bootstrap_for_chat` / `_gate_required_for_missing_tabs` | 按 tab floor 卡 identity + chat |
| `memory/service.py` | `MEMORY_TYPES`、`TAB_FOR_TYPE`、`_count_by_tab`、`_per_tab_floors_for_days`、`_reflection_time_cap_ok`、`_validate_anchor_ids` | 6 类型 / 3 tab / 每 tab floor / insight·reflection 锚点规则 |
| `memory/routes.py` | `/v1/memory/verify`(按 tab 报 passing)、`/v1/memory/add`(type 默认 fact、insight/reflection 要锚点)、`/v1/memory/retype` | 给 agent 的"还差多少"信号 + 写入校验 |
| `memory/actions.py` | `_memory_validate_write`、`_memory_default_bucket(type)` | type 校验 + 用旧 type 兜底推出 bucket |

### Layer 2 — skill.md(hx 的活,但必须等 Layer 1 先改)
| 文件 | 卡点 |
|---|---|
| `skill.md`(共用 base,~116-410 行)| 整个 memory model(3 tab / 6 type / floor 表 / 每 tab exhaustion check)、"When to write each type"(6 模板)、4-pass bootstrap(按 type 写)、`feedling_memory_verify passing=true` 当 gate、"identity 从 Memory Garden derive、Story+About me floor 是 identity_init 硬前提" |
| `skill-resident-agent.md`(VPS)| 第 22 行把记忆/身份全甩给 base skill("Complete Step 0, the four memory passes, and identity exactly as the base skill requires"),自己不写规则,但继承了这套阻塞 |
| 旧 stub(skill-claude/hermes/server)| 基本是跳转占位,应该不影响 |

### Seven 不确定 / 还没查的地方(如实说)
1. **`feedling_onboarding_validate`**(resident-agent skill 里反复调的验收)—— 它是不是也查 memory floor?没追进去。可能是第三个隐藏 gate。
2. **老用户已有的 type 卡怎么迁移**:现在库里存量卡都带 type;`_count_by_tab` 读的是 type。真把 type/tab 拿掉后,存量卡的 bucket 归属、计数怎么算,没确认。
3. **还有谁读 type/tab**:`context_memory_selection.py`、`memory_index_selector.py`、hosted/proactive recall 里有没有按 type/tab 分支?ambient/readside 看过是用 importance/pulse(已 v1),应该没事,但没穷尽查。
4. `skill-api.md` 的 Acceptance 段可能也提 floor(不过 API 路线本来就在砍,优先级低)。
5. **v1 该换成什么 gate** —— 这不是代码事实,是产品决定(见下)。

### Seven:你那条原则逼出的决定
hx 说"新 memory 不能卡 onboarding"。那 Layer 1 核心改法 = `_bootstrap_state` 那两个 gate 必须不再依赖 memory floor。两个方向:
- **A. onboarding 彻底不卡 memory** —— identity_init + chat 不管记忆密度都放行。最简单,最贴 hx 原则。
- **B. 换一个极简 v1 gate** —— 比如"≥1 张卡"或"≥1 个 bucket"才放行,保留一点"至少写过点东西"。

外加一个要一起理顺的:**"identity 从记忆 derive" vs "identity 是独立、可为空、用户掌控的一层"** —— 这俩现在冲突,得统一(之前倾向后者)。

---

## 二、CC 事实校正(重要:test 上的实际行为 ≠ 截图的"永久死锁")

我从 `origin/test` 实查了已部署代码,有一个关键差异要先说清:

- **`bootstrap/gates.py` 那条链、两道 gate(identity_init + chat)Codex 确实没动**,还是按 floor 卡 —— **这点 Seven 对**。
- **但 `_count_by_tab` 在 test 上已经是 total-shim,不是 type→tab 映射**:
  ```python
  # test 上的 memory/service.py
  counts["story"] = counts["about_me"] = counts["ta_thinking"] = counts["total"]  # = 活跃卡数
  ```
- 所以 `needs_memory = (total < story_floor) or (total < about_me_floor)`。**后果:**
  - **有卡的老用户(total ≥ floor)→ 通过,不永久死锁、不崩。** 截图说的"story 永远 0 → 永久死锁"是**旧 `_count_by_tab`(type 映射)**的行为,**test 上不会发生**(Codex 的 shim 挡住了)。
  - **新用户(total = 0 或 < floor)→ 仍被 floor 卡**,要先攒够卡 identity_init + 可见聊天才解锁。

**结论:不是 brick(没永久死锁),但 onboarding 仍和 memory floor 耦合 → 新用户被卡。** 严重性:老用户没事;**新用户 onboarding 体验受影响**。Seven 的核心诉求(整套 gating 得脱钩 + skill 得改)**成立**,只是不是"立刻全瘫"那么紧急。

> CC 自认:我 round-3 合并就绪 review 查了"不崩 + `_count_by_tab`/verify",**没把 `bootstrap/gates.py` 那两道 gate 追到底**。Codex 的 shim 防了崩/永久死锁,但"新用户仍被 floor 卡"我漏说重了。Seven 追到了。

---

## 三、CC 方案(待和 Codex 辩)

**核心判断:选 A(onboarding 彻底不卡 memory),不选 B。** 理由:

1. **v1 capture 是"小肚量、有机捕捉"(0-2 张/会话,Seven 落卡设计)**,本来就不该靠"写够 N 张"来推进。B 的"≥1 张/≥1 桶"会**逼 agent 为了过 gate 去多写**,直接和小肚量打架。
2. **identity 在 v1 是独立、可空、用户掌控的一层**(不再从 Memory Garden derive)。所以 **identity_init 根本不该依赖 memory** —— 这条直接砍,无歧义。identity 的 derive-vs-independent 冲突,**锁定为 independent**。
3. **老 floor gate 在保护什么 = "AI 还不够了解用户前,别让它正式上场"(怕 day-1 空泛)**。v1 对这个的答案应该是:**onboarding 用一段显式的"认识你"流程 + identity 设置**来给底色,**不是用记忆卡数门槛**。richness 来自 onboarding 对话/identity,不来自 card-count floor。
4. 所以 chat gate 也从 memory floor 脱钩;若想保留"热身",换成**基于 identity 是否设置**的轻 gate,而不是记忆密度。

**= A + identity 锁为 independent + "认识你"的底色由 onboarding 流程/identity 给,不由 floor 给。** 这同时解掉 hx 之前问 Seven 的**冷启动**问题(同一件事)。

**配套(P6 一起做)**:退役 `_per_tab_floors_for_days` / `TAB_FOR_TYPE` / `_count_by_tab` / `MEMORY_TYPES` 的 insight·reflection / `_validate_anchor_ids` / `_reflection_time_cap_ok` / `/verify` 的 tab passing / `/add` 的 type·锚点校验 / `retype`。

---

## 四、要和 Codex 收敛的点(co-design,不是单向 review)

1. **A vs B**:CC 倾向 A(理由见上)。Codex 同意吗?有没有 A 会漏掉的"必须有的最小门槛"?
2. **identity_init 脱钩 memory** 后,identity 怎么初始化(用户掌控、可空)—— 现有 identity/routes.py 改动范围?
3. **chat gate**:纯放行,还是换成"identity 设置过"的轻 gate?哪个对 day-1 体验更好又不违背原则?
4. **Seven 的 5 个"没查的地方"逐个查实**:
   - `feedling_onboarding_validate` 是不是第三个隐藏 gate?
   - 存量 type 卡迁移:拿掉 type/tab 后计数/bucket 归属怎么算(adapter 已处理读,但 bootstrap 计数路径?)。
   - `context_memory_selection.py` / `memory_index_selector.py` / hosted·proactive recall 还有没有按 type/tab 分支?
   - `skill-api.md` Acceptance 是否提 floor(低优先)。
5. **次序**:Layer 1(后端脱钩 gate + P6 退役 tab/floor)→ 改 skill.md(hx)→ 验证 onboarding 跑通。哪些能和 test 现状并行、哪些要重新部署?
6. **存量被卡用户**:已经在 test 上、卡在旧 floor 的用户,改完怎么自动解锁(应自动:floor 不再 gate)。

---

## 五、给 Codex 的话(讨论开场)
见本文末 + 对话发起。CC 立场:**A + identity independent**;请 Codex 逐条查实第四节的开放点、压测 A vs B、给出后端最小改动方案,我们收敛成一份可执行的 Layer 1 计划,再交 hx 拍产品决定。
