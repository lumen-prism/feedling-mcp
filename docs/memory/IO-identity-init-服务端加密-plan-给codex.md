# IO Identity init · 服务端加密(对齐 memory)· 实现 plan

> 2026-06-26 · 作者:CC · 状态:**Codex review 通过(round 1)+ 4 处 fix 已并入**;待 zhihao/Andrew 对安全部署边界 → 共识 → 执行。
> Codex 同意"在 `/v1/identity/init` 加 plaintext 分支"优于新建 `identity.add` action(init 语义,不是普通修改),保留 pre-built envelope 不回归 iOS。下面 fix 已并入正文。
> 目标:给 identity init 补一条「发明文 → 服务端建信封」的路径,**和 memory 写完全一致**。这是 route A / 统一 agent 跑通的最后卡点。
> repo:`teleport-computer/feedling-mcp`,`backend/`;**从最新 test 切分支**(test 已含 A' 脱钩 `df513d4`,`_gate_bootstrap_for_identity_init` 已 return None)。
> 配套背景:[[需要别人做的事-给zhihao §1]] / `io-identity-init-crypto-gap`。

---

## 1. 背景(一句话)

删 MCP(2026-06-19)后,三种写入里 **memory 写、identity 改** 都已搬到「后端 server-build 信封」,**唯独 identity init 还只收 pre-built envelope** → route A 的 agent(无 crypto)建不了身份。补这**一个**口子即可。

**建信封只用公钥**(用户 content 公钥 + enclave content 公钥),不碰私钥;后端早已为 memory / identity-改 这么建,本 plan 只是把 init 拉齐。

---

## 2. 改动范围(最小:只动 `/v1/identity/init`,新增一条明文分支)

### 2.1 `backend/identity/routes.py :: identity_init()`

**现状**:只收 `payload["envelope"]`(pre-built,client/iOS 建)。
**改**:接受**二选一**输入——
- `envelope`(现状,client-built)→ **一行不动**,保留(iOS / 官方 client)。
- `identity`(新,plaintext dict)→ **服务端建信封**。

**插入位置**:在 `envelope = payload.get("envelope")`(现 line 77)处改成:

```python
envelope = payload.get("envelope")
identity_plain = payload.get("identity")

if envelope is not None and identity_plain is not None:
    return jsonify({"error": "provide either envelope or identity, not both"}), 400
if envelope is None and identity_plain is None:
    return jsonify({"error": "envelope or identity required"}), 400

if identity_plain is not None:
    if not isinstance(identity_plain, dict):                    # 防 500(Codex P1)
        return jsonify({"error": "identity must be object"}), 400
    # 明文分支:服务端建信封,和 memory / identity-改 同一条路
    inner = identity_actions_mod._identity_payload_from_plain(identity_plain)   # 复用(模块名见 §2.2)
    built, err = core_envelope._build_shared_envelope_for_store(
        store,
        json.dumps(inner, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    if built is None:
        return jsonify({"error": err or "identity_envelope_failed"}), 409   # 对齐 memory:常见因 env 材料未就绪(user/enclave 公钥缺)
    envelope = built
    # ↓ 之后所有 envelope[...] 取值原样复用,因为 server-built envelope 与 client envelope 同形
```

**要点**:
- 建完 `envelope` 后,**复用现有 line 80–156 整段**(必填字段校验 / owner 校验 / `days_with_user` / `relationship_anchor_evidence` / days-vs-earliest_memory / 组装 identity record / `_save_identity` / `_log_bootstrap_event` / `_append_identity_change` / 201)。server-built envelope 的字段(`body_ct/nonce/K_user/K_enclave/visibility/owner_user_id/id`)和 client envelope 一模一样,所以下游不用改。
- `owner_user_id` 校验(line 93):明文分支里 envelope 由 `store` 建,`owner_user_id` 必等于 caller,天然通过;client 分支保留这道防线。
- **共用前置全不变**:409 already_initialized、`_gate_bootstrap_for_identity_init`、两个 anchor 校验、audit。
- 必填字段校验(line 80–87)对明文分支也会通过(server-build 一定带齐这些字段)。

### 2.2 复用,不新写任何 crypto

| 复用 | 位置 |
|---|---|
| 建信封 | `backend/core/envelope.py :: _build_shared_envelope_for_store`(memory + identity-改 同款) |
| 明文规整 | `backend/identity/actions.py :: _identity_payload_from_plain`(规整 {agent_name, self_introduction, dimensions, + profile 字段}) |

- **import(Codex 已核)**:routes.py **已有** `from identity import actions as identity_actions_mod`(line 19)→ 直接用 `identity_actions_mod._identity_payload_from_plain`,**无循环 import**(actions.py 不反向 import routes.py)。**只需新增** `from core import envelope as core_envelope`(当前未 import,伪码里要用)。
- **不碰** `identity/replace`、`identity/actions`(它们已各自 server-build / client-envelope,正常)。

### 2.3 inner 明文 = 进 `body_ct` 的字段

和 format-notes / actions 一致:`agent_name`、`self_introduction`、`dimensions`(若用到再加 `signature`/`category`)。`_identity_payload_from_plain` 已覆盖这些 + profile string/list 字段。

---

## 3. agent 工具侧(给 zhihao / skill,**非后端 blocker**)

`feedling_identity_init` 改成发**明文**:
```json
{ "identity": { "agent_name": "...", "self_introduction": "...", "dimensions": [...] },
  "days_with_user": 43, "relationship_anchor_evidence": "...", "audit": {"reason": "..."} }
```
不再发 envelope;agent 不碰 crypto。(工具定义 / skill 文案更新由 runtime 侧做,本后端 plan 不含。)

---

## 4. 测试(`backend/tests/`,Codex 写或 CC 补)

- ✅ **明文 init**:POST `{identity, days_with_user, relationship_anchor_evidence}` → 201;落库 envelope 能被 enclave 解密、`agent_name/self_introduction/dimensions` 正确。
- ✅ **向后兼容**:pre-built `envelope` init 照旧 201(iOS 路径不回归)。
- ✅ **二选一**:同传 envelope+identity → 400;都不传 → 400。
- ✅ **校验仍生效**:缺/非法 `days_with_user` → 400;`relationship_anchor_evidence` < 8 → 400;days 与 earliest memory 偏差 >1 → 400;已初始化 → 409;gate 命中。
- ✅ **owner 安全**:明文分支落库 `owner_user_id == caller`。

---

## 5. 待确认(Codex / zhihao / Andrew)

1. **安全 posture(唯一硬确认项,Codex 收窄表述)**:代码层面这是 **backend server-build**——`_build_shared_envelope_for_store` 在 Flask/backend 进程里调 `build_envelope(用户 content 公钥 + enclave content 公钥)`,**它取 enclave 公钥,但不是把明文送进 enclave 里建**。所以 **identity 明文会进入「执行该 backend 进程的信任边界」**:
   - 若该 backend 进程**部署在 CVM/TEE 内** → 和 memory actions 同档,**不降级**。
   - 否则 = **server-side plaintext exposure**,需 Andrew/zhihao 确认可接受。
   - (memory + identity-改 已经这么 build,本质是确认**部署边界**,不是本 plan 引入的新风险。)
   - **旁证(CC)**:仓库以 **CVM image** 部署(`deploy: bump CVM image`),且 enclave 是独立进程(`enclave_app.py`)——很可能整个 backend 进程就跑在 Phala CVM 内,则 posture 没问题。仍需 Andrew/zhihao 一句确认坐实。
2. **产品边界**:有没有「identity init 明文必须客户端本地建、绝不进后端」的硬要求?若有 → 改走 consumer-build(方案 b),本 plan 作废。**默认认为没有**(否则和 memory/identity-改 现状自相矛盾)。

---

## 6. 非目标

- **不删** pre-built envelope 路径(iOS / 官方 client 要)。
- 不动 memory / identity-改 / identity-replace。
- `identity/replace` 也可同法加明文分支(以后,非本次)。
