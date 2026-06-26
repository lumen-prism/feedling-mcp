# IO Memory v1 · 测试用例

> 2026-06-26 · CC · 两部分:**A) 自动化(Codex 跑)** + **B) 真机(hx 跑,api + vps)**。
> 基线:feedling-mcp `test`;skill `io-onboarding/test`。

---

## A. 自动化测试(Codex 在带 PG 的环境跑)

### A.0 现状(CC 本地已跑)
- VPS/route-A 套件:**95 passed / 4 skipped / 0 failed**。
- agent-runtime 套件:**38 passed**。
- ⚠️ **3 个 model_api 老测试 fail = 断言过时**(不是回归):`test_model_api_path.py` 里
  `test_model_api_memory_repair_archives_noisy_cards_only_after_replacements` /
  `test_candidate_pipeline_renders_high_value_cards_without_generic_tasks` /
  `test_candidate_render_merges_similar_cards_filters_sensitive_claims_and_sorts_newest_first`
  仍断言老字段 `card["type"]`/`card["description"]`,而 `_render_candidates_to_memory_cards`
  已产出 v1 卡(bucket/threads/summary/content)→ `KeyError: 'type'`。**→ Codex TODO:把这 3 个断言更新成 v1 字段。**

### A.1 全量回归(命令)
```bash
DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres \
FEEDLING_TEST_PG=postgresql://postgres:test@127.0.0.1:55432/postgres \
python -m pytest tests/ -q   # 需 pip install: backend/requirements.txt + pytest + requests + pytest-asyncio
```
重点套件:`test_identity_init_server_encrypt` · `test_memory_*` · `test_bootstrap_gates` ·
`test_history_import_identity` · `test_model_api_path` · `test_agent_runtime_*` · `test_litellm_gateway`。

### A.2 必须覆盖的 v1 行为(单测层)
- [ ] identity init **明文分支**:`{identity, days_with_user, relationship_anchor_evidence}` → 201、可解密;pre-built envelope 仍 201;二选一/非 dict→400;build 失败→409。
- [ ] memory 写:add / supersede(soft,不硬删)/ delete;服务端建信封。
- [ ] memory 读:index(无 content)→ fetch(有 content,enclave 解密);`bucket`/`thread` filter;follow_thread。
- [ ] A' gate:0 记忆可 init identity;无 per-tab floor / verify gate。
- [ ] 迁移:老卡经 `to_v1_card` 有 importance/pulse/status 默认。
- [ ] onboarding 导入:`_render_candidates_to_memory_cards` 产出 **v1 卡**(bucket/threads/三段content/importance/pulse)。**← 顺手修 A.0 那 3 个断言。**

---

## B. 真机测试(hx)—— API + VPS 两条

### B.1 API 形式(app:选模型 + 填 key + 传材料)
1. [ ] 注册 / 登录,进 setup 屏。
2. [ ] 选 provider + model + 填 API key → 测试连接通。
3. [ ] 上传材料(`feedling-mcp-ios/Docs/onboarding-raw-fixtures/*.txt`:人设/资料/记忆摘要/聊天记录)。
4. [ ] history_import 跑完 → 进 Memory Garden 看:卡是 **v1 形**(bucket/thread/summary/三段content/importance/pulse),不是老 type/tab。
5. [ ] identity card 生成(名字/自我介绍/维度)、有开场白。
6. [ ] 发一条消息 → 正常回复;agent 能在相关时引用导入的记忆。
7. [ ] 改一条卡 / 让它"忘记"某事 → supersede(老卡不消失、链新卡)。

### B.2 VPS 形式(route A:自己 agent + consumer)
> skill 入口(test):`https://raw.githubusercontent.com/teleport-computer/io-onboarding/test/skill.md`
> consumer 代码:feedling-mcp **`origin/test`**;env:`FEEDLING_API_URL` / `FEEDLING_API_KEY` / `FEEDLING_ENCLAVE_URL`。
1. [ ] agent 读 test skill,Step 0 关系锚点 → 写 **identity(明文,服务端建信封,无需 crypto)**。
2. [ ] 起 consumer service(从 origin/test),`feedling_chat_verify_loop` 通。
3. [ ] `feedling_onboarding_validate` → `resident_consumer` / `live_loop` / identity 全 passing(**0 记忆也能完成**)。
4. [ ] 第一条 IO Chat 问候出现;发普通消息 → 自然回复一次。
5. [ ] 对话中 agent **主动** search→fetch 记忆并自然织入(不复述卡面);**无 ambient 自动注入**。
6. [ ] 断点处 agent 自己落卡(0-2 张,v1 形);改口用 supersede。
7. [ ] Garden 看到 VPS 写入的卡 = v1 形。

### B.3 两条都要确认的 v1 不变量
- [ ] identity 先行、0 记忆合法、无 floor/verify 门槛。
- [ ] 卡 = bucket/threads/summary/三段content/importance/pulse(无老 type/tab 主结构)。
- [ ] 读 = agent 主动取(无 ambient/recall 兜底)。
- [ ] 写 = supersede 不硬删;落卡克制。
- [ ] agent 全程不碰密钥(写=服务端建信封,读=enclave 解密)。

---

## C. 已知非阻塞项(测试时会遇到,别误判为 bug)
- 3 个 model_api 老断言 fail(A.0)—— 测试过时,功能对的。
- `litellm` 不在 backend/requirements.lock;本地不装会让部分 model_api/agent-runtime 用例报错(CI/部署环境有)。
- 官方导入(official_import)路线的 `skill-chat-client.md` 已删(MCP 清理),不在本次测试范围。
- 敏感 gating flag 默认关(休眠),本次不测 flag-on。
