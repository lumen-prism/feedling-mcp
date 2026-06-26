# IO Memory · 下个版本计划(prompt engineering + eval + 联想)

> 2026-06-26 · CC · v1 已上 test(读写闭环、agent-first、identity-init)。下版**不动 schema**,主攻"记得准 + 想得起 + 会联想"。

---

## 1. 记忆部分的 Prompt Engineering(主线)

v1 的判断都在提示词里(`backend/memory/prompts_v1.py` = Seven 拥有 + skill 读写指引),下版重点调它,**不改结构**。

### 1.1 写入(capture)
- **该记的别漏**:起点 bug —— 用户自然陈述的持久事实(如"我家狗叫蛋子")曾被当闲聊漏掉。提示词要把"事实/关系/边界/习惯/转折"抓全,而不只抓"偏好"。
- **不该记的克制**:闲聊/临时情绪/玩笑/角色扮演/未确认猜测/只是引用已有 —— 继续压。0-2 张/断点。
- **resolve-before-create**:写卡时把现有 bucket/thread 词表喂进去逼复用,防词表膨胀/分裂(蛋子≠狗狗)。调注入格式 + 阈值。
- **importance / pulse 打分校准**:让两个值更稳、更可分。

### 1.2 读取(retrieval + 表达)
- **该想起时想得起**:何时去查(skill 读取指引)、用什么 bucket/thread 进 index。
- **怎么织进回复**:`MEMORY_CONTEXT_FRAMING_V1`(Seven)—— 证据式自然织入、跟随每卡"使用提示"、冲突信当前消息、弱相关不断言。继续打磨。

### 1.3 产出
- prompts_v1 文案迭代(Seven 主笔,CC/eval 支撑)+ skill 同步。

---

## 2. Eval(量化"记得准 / 想得起")

把记忆质量变成可跑的评估,驱动上面的 prompt 调整(而不是拍脑袋)。

| 维度 | 评什么 | 例 |
|---|---|---|
| **写-召回(recall)** | 该记的有没有记 | "狗叫蛋子"这类持久事实有没有落卡 |
| **写-精度(precision)** | 不该记的有没有克制 | 闲聊/玩笑没被落卡 |
| **读-相关性** | 该想起时 agent 有没有查到、是否相关 | 聊到宠物时 fetch 到蛋子卡 |
| **supersede 正确性** | 改口纠错有没有用 supersede、没乱删 | 旧事实更新 → 老卡 superseded、新卡 active |
| **bucket/thread 复用** | 有没有复用现有词表、没膨胀 | 同一线一个名 |

- **形态**:固定一组对话 fixtures + 期望(该记什么/该召回什么)→ 跑 agent → 比对。复用现有测试基础设施。
- **门槛**:先建 baseline 数字,再用它评 prompt 改动的好坏。

---

## 3. 推理层 / 画像(deferred → 这版做)

- agent 对用户的**猜测/画像**(聊天 + 屏幕都喂),**单独一层**,与"grounded 记忆"分开(别混进卡库当事实)。
- 和 eval 一起、上线后做(v1 明确延后)。

---

## 4. 联想功能(Seven baseline 里的 "Dream")

让记忆**互相连**、会"想起来",而不只是被动召回。来源:Seven 落卡/Dream baseline + v1 结构定稿 §8。

- **follow_thread(已有基础)**:一条 thread 横穿不同 bucket → 把散落的卡串成一条线(关系弧、某个人、某件事)。下版强化"何时主动联想"。
- **做梦/Dream(加法 + 闸门,永不删)**:
  - 合并近义 bucket / thread(降词表熵)。
  - 久不用的卡退化为 dormant(不删,降权)。
  - 把多张相关卡**抽象成一张更高层的卡**(模式/洞察)。
  - 提醒 `open_thread`(没收尾的线索)→ 主动关心。
- **定位**:这是"联想/反思"层,跑在低频(类似 dream),**加法为主、永不硬删**,和 §3 推理层是邻居。

---

## 5. 不做 / 边界
- **不改 schema**(bucket/thread/importance/pulse/supersede 保持)。
- embedding 语义召回、自动敏感分类、Garden 高级渲染 —— 视情况,非本版核心。
- 主线 = **prompt 调准 + eval 量化 + 联想成型**;画像/Dream 是这版的"进阶"。
