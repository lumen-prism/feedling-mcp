# IO v1 · 分支收敛 plan(一块 v1 功能散在 5 条分支)

> 2026-06-26 · CC · 状态:**待 Codex review**。
> 背景:v1 是**一块功能**,但散在 5 条分支、2 个 repo,且有 **2 处并行重复(会冲突)**。
> 目标:收敛成 **「后端 test(v1 全)+ io-onboarding main(skill v1)」**,丢 2 条 stale,留文档。

---

## 1. 全景:v1 这块功能现在散在哪

### 后端 `feedling-mcp`(5 个 worktree = 1 个 repo)
| 分支 | 内容 | 决定 |
|---|---|---|
| `test` @ 8b7c39d | schema + A' 脱钩 + identity-init 加密 | — (集成线,领先 main 188) |
| `feat/memory-v1-clean-schema` | v1 schema | ✅ 已在 test(本地分支可删) |
| `feat/memory-v1-onboarding-gate-decouple` | A' | ✅ 已在 test(可删) |
| `feat/identity-init-server-encrypt` | identity 明文建信封 | ✅ 已在 test(可删) |
| **`feat/memory-m-readwrite-consistency`**(3) | route-A recall+actions 统一(`b078d7f`)、敏感 fetch gate(`40c3659`)、flag off(`20f8add`) | ⬇ **保留 → 合 test** |
| **`feat/hosted-memory-tools`**(12) | 11 个 `docs:`(v1 设计/plan/给 zhihao·Seven·Codex 交接/ambient 清理/identity)+ 1 个代码 `433d9af` | docs **留**;`433d9af` **丢** |

### skill `io-onboarding`
| 分支 | 内容 | 决定 |
|---|---|---|
| `main` | 线上老 skill | — |
| **`feat/skill-memory-v1`**(7, 6-26, 过 2 轮 Codex grep) | **完整 v1 skill 重写** | ⬇ **保留 → 合 main** |
| **`feat/memory-m-http-skills`**(2, 6-24) | 早期 route-A HTTP read/write 规则(只改 skill.md + skill-resident-agent.md) | **大概率被 skill-memory-v1 吞了 → 删(待验)** |

---

## 2. 两处"并行重复"(会冲突,要拍)

### 2.1 后端 route-A recall —— 两份独立实现
血缘:两者**独立**切自 test 的不同点(hosted @ `050a1fe`,memory-m @ `574b1ed` 更新),**互不包含**。

| | `433d9af`(hosted-memory-tools)"inject route A memory recall" | `b078d7f`(memory-m)"unify route A memory recall and actions" |
|---|---|---|
| hosted_runtime.py | **-114 大改**(含 `hosted_runtime_state`/`model_api_correction` = **route B territory**) | 几乎不动(+3) |
| 新增 | `memory/action_schema.py`(+175)、`context_layout.py` | `memory/routes.py` recall(+83)、`route_a_agent_first_memory_smoke.py` |
| chat_resident_consumer.py | +259 | +220 |
| 测试 | conformance +86、route_a_memory_recall +118 | conformance +121、recall_db +114、recall_route +145、consumer +81 |

- **重叠**:两者都改 `chat_resident_consumer.py` + `tests/test_memory_action_conformance.py` → 直接合**会冲突**。
- **判断**:**保 `memory-m`,丢 `433d9af`** —— memory-m 更新(切自更新 test)、route-A 聚焦(不碰 route B)、测试更全;hosted 的旧、且**缠 route B(已弃)**,把弃用的 hosted_runtime 一起改了。

### 2.2 skill —— 两份
- `feat/skill-memory-v1`(6-26,全量 v1,过 2 轮 Codex) vs `feat/memory-m-http-skills`(6-24,只 route-A HTTP 规则)。**互不包含**。
- **重叠**:都改 `skill.md` + `skill-resident-agent.md` → 会冲突。
- **判断**:**保 `skill-memory-v1`,删 `memory-m-http-skills`** —— v1 更新、更全、已 review;http-skills 是它之前的窄版,很可能已被吞(**待 Codex 验**)。

---

## 3. 收敛后(5 散 → 2 落点)
- **后端 test**:schema + A' + identity-init(已)+ **Memory M(recall/actions/敏感 gate)**(待合)。
- **io-onboarding main**:**v1 skill**(待合,先測法 A 验)。
- **丢**:`433d9af`、`memory-m-http-skills`。**留**:docs(hosted-memory-tools 的 11 个 doc commit)。

---

## 4. 落地顺序(建议)
1. **memory-m → test**:rebase 到当前 test(`8b7c39d`)→ Codex review 代码 → 合。(它切自旧 test `574b1ed`,要 rebase;和 identity-init 不同文件,应无冲突。)
2. **docs**:把 hosted-memory-tools 的 11 个 doc commit 摘到干净分支(**不带 `433d9af`**),留参考 / 可合。
3. **skill → main**:确认 skill-memory-v1 吞了 memory-m-http-skills → 測法 A 验 → 合 main;删 memory-m-http-skills。
4. 之后 **test → main 大发布**。

---

## 5. 请 Codex 重点验这 2 个"丢"的判断
1. **memory-m 的 recall 是否功能上覆盖/取代 hosted 的 `433d9af`?** 有没有 433d9af 里有、memory-m 没有的能力(尤其 `memory/action_schema.py` / `context_layout.py` 那部分有没有 memory-m 缺的)?若有,先摘进 memory-m 再丢 433d9af。
2. **skill-memory-v1 是否真覆盖了 memory-m-http-skills 的两条 route-A HTTP read/write 规则?** 若没全覆盖,把差的那点摘进 skill-memory-v1 再删。

> 这两条是仅有的"可能丢东西"的风险点,其余收敛是纯整理。
