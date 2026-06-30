# Onboarding raw fixtures

这些文件用于手动测试 Genesis v2 foreground-fast onboarding。格式保持和 App 导入一致：

```text
2026-01-05 21:30 我: 今天加班到现在，累死了
2026-01-05 21:31 小柒: 辛苦了，先喝口水。是上线那个项目吗？
```

- 用户固定叫 `我`。
- TA 固定叫 `小柒`。
- 每份都有时间跨度，方便测试 `days_with_user`。
- 每份都埋了高信号信息：关系锚点、宠物/家人/朋友、偏好/边界、健康或目标。

## 文件说明

| 文件 | 主要验证点 | 希望前台 core 选到 |
|---|---|---|
| `relationship_pet_wusong.md` | 关系锚点 + 宠物 + 陪伴边界 | 第一次相识、猫武松、崩溃时先陪伴不要建议 |
| `health_boundary_sleep.md` | 健康 + 边界 + 情绪照顾方式 | 偏头痛、睡眠问题、不喜欢被催、需要温和提醒 |
| `family_friend_care.md` | 家人/朋友 + 责任感 + 饮食偏好 | 妈妈膝盖、朋友阿泽、爱喝热汤、不吃香菜 |
| `goals_growth_design.md` | 目标成长 + 工作压力 + 价值观 | 转 AI/agent、想做 onboarding 系统、讨厌空话、需要结构化计划 |

## 手测建议

1. 先用其中一份完成 onboarding。
2. 进入 Garden，看前台是否很快出现 3-5 条核心记忆。
3. 看 greeting 是否像“读过这份历史”，而不是通用问候。
4. 等一段时间刷新 Garden，确认后台继续补更多记忆。
5. 检查前台核心记忆没有被后台重复写一遍。

