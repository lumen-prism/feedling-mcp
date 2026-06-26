# identity 初始化建不了 —— 请 zhihao + Andrew 拍两件事

> 2026-06-26 · CC · 一页版(完整 plan:`docs/memory/IO-identity-init-服务端加密-plan-给codex.md`)

## 问题(route A 的最后卡点)

route A(用户自己 agent,HTTP-direct,MCP 已删)现在**建不了 identity**:

- `/v1/identity/init` 只收**已经建好的加密信封(pre-built envelope)**。
- 以前是 **MCP server 帮 agent 建这个信封**;MCP **6-19 删了** → 没人建了。
- **memory 写 + identity 改** 都已经有"后端帮建信封"(server-build),**唯独 identity init 这一个没有** → route A 的 agent 写不进身份。
- (iOS / API 导入 / 官方导入 **都不受影响**,它们各有自己建信封的路;**只有纯 agent 那条断了**。)

## 方案(plan 已写好、过了 Codex round-1)

给 `/v1/identity/init` 加一条**收明文**的分支:agent 发明文身份 → **后端用 `_build_shared_envelope_for_store` 建信封再存**(和 `memory.add` / identity 改 **同一个函数**)。pre-built envelope 路径保留,**iOS 一行不动**。

- 改动很小:就 `backend/identity/routes.py` 加个 `envelope | identity` 二选一分支。
- 本质 = **把删 MCP 时没迁完的那一步补上**(memory / identity 改 早就迁了)。

## 需要你们拍两件事

**1. 安全 posture(你 + Andrew)**:跑 `build_envelope` 的那个 **backend(Flask)进程在 CVM/TEE 内吗**?
- 在 → identity 明文只在 CVM 内出现,和 memory 现状**同档**,直接做。
- 不在 → 那 memory 现在也是 server-side plaintext;要么一起评估,要么 identity init 改走 **consumer 本地建**(方案 b)。
- ⚠️ 这是**确认现状/部署边界**,不是本方案引入的新风险 —— memory + identity 改 早就这么建。
- 旁证(CC):仓库以 CVM image 部署、enclave 是独立进程,**看着像在 CVM 内**,但需你们坐实。

**2. 谁写**:plan 已经精确到文件/字段了,**CC 写最快**;但这是你的 Feedling 后端,**你想自己写还是 CC 写?** 你定。

## 回我两句就行
- posture:在 CVM 内 ✅ / 不在 ❌(不在的话我们再议 a/b);
- 谁写:CC 写 / 你写。

posture OK,我这边就能直接推进(写码 → Codex review 代码 → 合 test)。
