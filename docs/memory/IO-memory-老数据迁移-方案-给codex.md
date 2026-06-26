# IO Memory · 老数据迁移方案(给 Codex review)

> 2026-06-26 · CC · 状态:方案稿,待 Codex review。
> 范围收敛:**只迁老用户的老记忆卡**。identity 不迁(schema 没变)、新用户两条路已直接产 v1。

---

## 0. 问题边界(先收敛)

| 谁 | 要迁吗 |
|---|---|
| 新用户(API / VPS) | ❌ 初始化已直接产 v1(history_import / v1 skill 都是 v1) |
| 老 identity 卡 | ❌ identity schema 没变,照常读 |
| **老用户的老记忆卡** | ✅ **唯一要迁的** |

**老记忆卡缺什么**:老卡加密 body 里只有 `title/description/her_quote/context/linked_dimension`,**没有 v1 的 bucket/threads/summary/content三段**。现在读时:外层 `to_v1_card` 补 importance/pulse/status 默认;内层走 fallback(summary←title、**bucket←"未归类"**、threads←空)。**能读不崩,但降级(没归类、没线索)。** 真迁移 = 派生出真正的 bucket/threads/summary/content。

---

## 1. 检测:怎么知道某用户有"待迁"老卡

- 加一个**明文版本标记** `card_v`:v1 写入(add + 迁移)都置 `card_v=1`;老卡没有。**待迁 = 记忆卡 `card_v != 1`**。
  - (需确认 v1 add 现在置不置 card_v;不置就加上 —— 明文、不进 body。)
  - 替代:用**每用户标记** `memory_schema_migrated`(迁完置位)更简单,但 per-card `card_v` 更稳(幂等、可断点续迁)。**建议 per-card card_v**。
- 后端暴露一个**待迁条数**(明文扫,不解密)→ 同时驱动 API 弹框 + VPS agent 判断。

---

## 2. 两条路(对应你说的两种用户)

### 2.1 API 形式 → 服务端驱动 + 迁移弹框
1. 老用户(API)进 app → 后端检测待迁条数 > 0 → app 弹**迁移框**:「记忆系统升级了,把你的 N 条旧记忆整理成新格式?[开始 / 以后再说]」。
2. 点开始 → 后端跑**迁移 job(复用 history_import 管道)**:逐张老卡 → 解密 inner → 调**用户自己的 model_api**派生 `bucket/threads/summary/content三段`(resolve-before-create:把现有 bucket/thread 词表喂进去,让老卡落进一致的桶)→ **原地 re-encrypt**(`memory.content_patch` 同款,保留 id/occurred_at)→ 置 `card_v=1`。进度 UI。
3. 完成 → Garden 显示 v1 卡(已归类)。
- **用用户已配的 model_api key,零额外配置;幂等(card_v 标记已迁的跳过)。**

### 2.2 VPS 形式 → agent 驱动 + onboarding 里加一步迁移
1. 老用户(VPS)重新 onboarding(把 agent 指向 v1 skill)。skill 里加一个 **「迁移旧记忆」步骤**:
   - agent 查待迁条数(或 fetch 老卡发现没 bucket)→ 逐张:`fetch`(解密)→ 按 v1 写入指引派生 `bucket/threads/summary/content`(resolve-before-create)→ 写回 v1(原地升级 / supersede)→ 置 `card_v=1`。
   - 作为 onboarding 的一步(在 identity + live-loop 之前或并列)。
- **agent(用户自己的模型)做派生,走 v1 写工具;契合 agent-first。**

---

## 3. 共用机制

- **派生**:LLM 把老 `{title/description/her_quote/context/linked_dimension}` → v1 `{bucket, threads(1-4), summary, content三段}`。复用 `prompts_v1` 写入指引 + resolve-before-create(喂现有桶/线,防膨胀)。
- **写回**:**原地 re-encrypt**(保留 id/occurred_at/source,置 card_v=1)优于 supersede(不产重复卡)。`memory.content_patch` 已有这条路,迁移可复用/扩一个 `memory.upgrade`。
- **幂等**:card_v=1 标记已迁,重跑跳过;失败可续。
- **拒绝(API"以后再说")**:留降级态("未归类",已能读),下次再提示。不强制。

---

## 4. 要 Codex 拍的点

1. **检测标记**:per-card `card_v`(建议)还是 per-user `memory_schema_migrated`?v1 add 现在置 card_v 吗(不置要加)。
2. **写回**:原地 re-encrypt(建议,不产重复)还是 supersede(留审计、但翻倍)?
3. **API 迁移**:复用 history_import 管道(source 改成"已解密的老卡"而非上传文本)够不够,还是单开 `/v1/memory/migrate` job?
4. **VPS**:迁移作 skill 的独立 step,还是并进现有 onboarding pass?顺序(迁移 vs identity 谁先)?
5. **降级 vs 强迁**:同意"拒绝就留降级、不强制"?还是要个**无 LLM 的结构性兜底**(summary←title、content←description、bucket←type 默认)作为即时楼面,LLM 派生作增强?
6. **成本/批量**:一次性批量(建议)还是 lazy 按读迁?批量条数上限 / 进度。

---

## 5. CC 倾向
- per-card `card_v` + 后端暴露待迁条数(小改动)。
- API:服务端 job 复用 history_import 管道,弹框触发,用用户 model_api。
- VPS:skill 加「迁移旧记忆」step,agent 驱动。
- 原地 re-encrypt、幂等、resolve-before-create。
- 拒绝 → 降级兼容(已工作),不强迁;**结构性兜底可选**(让"未归类"至少变成 summary=title、content=description,比纯空好)。
