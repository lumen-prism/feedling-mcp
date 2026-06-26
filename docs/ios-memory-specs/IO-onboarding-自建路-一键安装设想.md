# IO onboarding 减摩擦 · route A 一键安装设想

> 2026-06-17 · 想法稿(brainstorm,非定稿)。
> 针对:route A(自建)onboarding 太重——要起服务器 + 配 agent + 跑 Consumer + 喂 skill。
> 目标:把它压成"一条命令 + 对话式填 + 当场绿灯"。

---

## 0. 一句话

> **route A 现在劝退,是因为用户要手抄一堆环境变量、自己写 agent 启动命令、自己 systemd、还出错没提示。解法 = 一个交互式 CLI(`io setup`)把这些替用户做掉;更进一步,干脆让用户自己的 agent 自装自己。**

---

## 1. 问题:route A 现在用户要做的 4 步(每步都有坑)

1. **有/找一台一直开机的机器**(VPS / Mac mini)
2. **上面有个能用的 agent**(Hermes / Claude Code…,通常本来就有)
3. **跑 Consumer**:复制配置块 → 手填 `FEEDLING_API_URL/KEY`、解密源、**agent 启动命令** → `systemctl` 起服务
4. **把 skill URL 发给 agent** → 跑 bootstrap

**最大的坑在第 3 步的"agent 启动命令"**:用户得自己写 `AGENT_CLI_CMD`,还要懂 `--resume`、`HERMES_HOME`、session 选择这些(tools/README 里一大段都在教怎么别写错)。写错了 → 聊天永远不应声,还没提示。

---

## 2. 方案一:`io setup` 交互式安装(主推)

一条命令走完,**关键是替用户做掉"他根本不知道怎么填"的部分**:

```
$ npx @feedling/io-setup

① 粘贴你从 app 复制的连接串:  io://...
   ✓ 解析出:账号 / API 地址 / MCP 地址 / key   ← 不用手抄 4 个变量

② 你的 agent 是哪种?
   1) Claude Code  2) Hermes  3) OpenClaw  4) OpenAI 兼容 HTTP  5) 自定义
   > 1
   ✓ 检测到 `claude` 在 PATH,自动套已知正确命令:
     claude --print --output-format json "{message}"   ← 治最大的坑

③ 解密源(回车用推荐 enclave):  ↵

④ 当场自测…
   ✓ API key 有效
   ✓ 真去调了一次你的 agent,拿到真实回复:"嗯,我在"
   ✓ 解密源通

⑤ 装成常驻服务?[Y/n]  Y
   ✓ feedling-consumer 已安装并启动

⑥ 就绪!把这句发给你的 agent 开始建记忆:
   "读 <skill URL>,照着把咱俩的记忆建起来"
```

### 四个关键设计点(每个对着一个现有坑)

1. **解析连接串自动填** → 不用手抄 4 个环境变量(现在最易抄错)。
2. **按 agent 类型套模板** → 用户只选菜单,wizard 内置每种 agent 的"已知正确命令"(治第 3 步最大的坑)。能检测 PATH 里有没有 `claude`/`hermes` 自动认。
3. **每步当场验证** → 尤其第 ④ 步"真去调一次 agent 看有没有真回复"——直接消灭"装完了但 agent 命令是错的、永远不应声"这个哑炮。
4. **最后自检绿灯** → 复用已有的 `check_chat_pipeline.py`,绿了才算完。

---

## 3. 方案二:让用户自己的 agent 自装自己(更 on-brand)

IO 本来就是个 **agent 产品**,那就让用户的 agent 自己把自己装上。

用户只干一件事——把一句话 + 连接串粘进他的 Claude Code:
> "这是我的 IO 连接串和安装 skill,帮我把 IO 装好、跑起来、建好记忆。"

然后 Claude Code(本来就能跑 shell)自己:写 env → 装 Consumer → 起服务 → 跑自检 → 跑 bootstrap。**全程用户就粘了一句话。**

- 好处:极致零摩擦,而且特别自洽(agent 产品理应能让 agent 自装)。
- 风险:依赖 agent 够强 + 有 shell 权限;要配一份**很紧的"安装 skill"**别让它跑偏,每步留校验。
- 建议:**两者结合**——`io setup` 作稳妥默认路;"agent 自装"作给 Claude Code/Hermes 用户的高级捷径。

---

## 4. 为什么可行(不是空想)

**大部分零件已经有了**,wizard 主要是"把现成零件包成友好流程",**低风险、高收益,不动核心架构**:
- Consumer:`tools/chat_resident_consumer.py`(已有)
- 自检脚本:`tools/check_chat_pipeline.py`(已有)
- env 模板:`deploy/chat_resident.env.example`(已有)
- 每种 agent 的正确启动命令:`tools/README.md` 里都写了(已有,搬进 wizard 模板即可)

---

## 5. 建议 + 分工

- **先做方案一(`io setup` wizard)**:性价比最高,把 route A 从"读文档抄变量 + 手写命令 + 自己 systemd + 出错没提示"压成"一条命令 + 选菜单 + 当场绿灯"。
- 方案二(agent 自装)作下一步"惊艳版"。
- 性质:偏 **CLI/工具 + 一点后端**,hx(做 CLI/MCP/工具)适合牵头。

---

## 6. 附:别让大众误入 route A

route A 本就面向"已有 agent 的技术玩家"。**大众用户应被引导到 route B(托管):没服务器、没 Consumer,填个 key + 传文件就行。** onboarding 优化的第一道,其实是**路线分流做对**——大众进 B,技术玩家进 A 再用 `io setup` 把 A 的摩擦削平。
