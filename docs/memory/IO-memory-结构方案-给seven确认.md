# IO 记忆 v1 · 给 Seven(Garden UI + 提示词)

> 2026-06-25 · CC 重写 · 结构已定稿、后端已实现并合 test。
> 本文 = 你接 **Garden + 提示词** 要知道的 v1 结构 + 你的活。
> 准确结构唯一真相:`IO-memory-v1结构定稿-bucket-thread.md`(本文是面向你的精简版)。
> ⚠️ **本文已整体重写**:6/24 旧稿那套 `kind / emotion_weight / relationship-vs-fact 两层` **已废**(见文末 §6),别再拿旧名实现。

---

## 1. v1 记忆卡(一种卡,不分 type,**没有 kind / relationship 类**)

| 字段 | 是什么 |
|---|---|
| `bucket` | **主话题,单选**(`我们的关系` / `工作` / `妈妈`…)。平铺、复用现有桶 |
| `threads` | **线索,多选**(`工作压力` / `蛋子` / `冷战`…)。横穿 bucket,`follow_thread` 靠它 |
| `summary` | 一句话(目录/列表用) |
| `content` | 正文 MD **三段**:记忆 / 上下文 / 使用提示 |
| `importance` | **0–1:看不看**(对长期理解用户多重要),写时定、客观 |
| `pulse` | **0–1:回忆时情绪强度**。**不进检索排序**,只影响表达色彩 + 气氛灯挑选 |
| `status` | active / superseded / archived |
| `source` | chat / screen |
| `occurred_at` / `last_referenced_at` | 时间;**decay 读时从 `last_referenced_at` 派生**,被用到就回升 |

→ **没有 `kind`、没有 `emotion_weight`、没有 6 type、没有 3 tab。** "我们的关系"现在只是**一个 bucket**,不是一种卡。

---

## 2. 三层
- **identity** — 常驻每轮、用户控制、系统不偷改(名字/称呼/边界/关系维度)。
- **memory** — 事件卡(上面这种),bucket 归类 + thread 横穿。
- **推理层 / 画像** — agent 对用户的猜测(聊天+屏幕),**v1 不做**,和 eval 一起、上线后(怕一本正经说错猜测)。

---

## 3. 检索(关键纠正:气氛灯**不靠"关系类"**)
- **agent-first**:agent 自己 `index`(选 bucket/thread)→ 看目录 → `fetch` 取正文 → `follow_thread` 跨桶串。
- **气氛灯 ambient**:每轮 runtime 推几条底色 = **importance × pulse × recency,任意 bucket**。
  - pulse 高的(情绪/关系时刻)**自然更容易被挑成底色** → 陪伴感还在,**但机制是 pulse,不是"挑 relationship 类卡"**。这是和旧稿最大的区别。
- 不做 recall 兜底 / preflight。

---

## 4. Garden = 用户控制台(你重做)
- 按 v1 渲染:**bucket / threads / summary / content**,**不再 type/tab**。
- 看 / 删 / 纠正记忆 + 编辑 identity。隐私 v1 先简化(能删就够,自动门禁后置)。

---

## 5. 你(Seven)的活
1. **Garden UI v1 重做** + iOS `MemoryMoment` model 升级。合并后 Garden 会显示**空白 v1 卡**(不崩,iOS 兼容解码兜底)→ 等你重做样式。
2. **提示词**:`backend/memory/prompts_v1.py`(已接进 route B,**占位文本**)—— 你整段替换/迭代。现有桶/线注入(resolve-before-create)已接好,你调措辞即可。
3. **2 个待你拍**(结构定稿 §9):① bucket 平铺 vs 层级(建议平铺);② pulse 进不进检索排序(现定:不进,只影响表达色彩)。

---

## 6. 相对 6/24 旧稿改了什么(别拿旧的)
| 旧稿(已废) | v1(现在) |
|---|---|
| `kind`(relationship / fact)| **删**;改 `bucket`(单选)+ `threads`(多选) |
| "关系记忆 = 气氛灯,靠 kind=relationship 每轮带" | 气氛灯 = **importance × pulse × recency,任意桶**;pulse 偏向情绪/关系时刻 |
| `emotion_weight`(一个分) | 拆成 **importance(看不看)+ pulse(情绪强度)** |
| "改口 = 新写 + 靠日期判断" | **supersede soft 回归**(旧卡转 superseded、链新卡、不硬删) |
| 6 type / 3 tab(故事/关于我/TA在想) | 全删 |
