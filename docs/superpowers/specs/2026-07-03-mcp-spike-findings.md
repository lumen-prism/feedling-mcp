# MCP spike findings — claude + codex CLI (Phase K)

日期:2026-07-03
配套:`feedling-mcp-ios` `Docs/superpowers/specs/2026-07-03-mcp-client-design.md` §11。
环境:
- claude CLI 2.1.196,node v24,`@modelcontextprotocol/server-everything`(stdio 测试 server)。
- codex CLI 0.142.5,现有远程 HTTP MCP server(只调用只读工具;未记录 URL/token)。

## 结论(claude,已实测)

用 `--mcp-config` 挂一个 stdio MCP 测试 server,让无人值守 `claude -p` 调它的 `echo` 工具:

```
claude -p --strict-mcp-config --mcp-config <cfg.json> \
  --allowed-tools "mcp__everything" \
  "Call the echo tool with 'spike-ok' ..."
```
`cfg.json` = `{"mcpServers":{"everything":{"command":"npx","args":["-y","@modelcontextprotocol/server-everything"]}}}`

**结果**:`{"result":"Echo: spike-ok", "permission_denials":[]}`。

### 由此确认(回填 spec §6/§12 的开放点)
1. **allowlist 用 server 前缀即可整站放行**:`--allowed-tools "mcp__<slug>"`(**不带工具名**)就授权了该 server 的全部工具 —— echo 成功执行、`permission_denials` 为空。**spec §6 假设的 `mcp__<slug>` 整站放行成立(claude)。** 不必逐个工具列。
2. **无人值守免批准**:`claude -p` 在工具被 `--allowed-tools` 授权时**不弹交互批准**直接调用(`permission_denials:[]`)。符合 io 无人值守 agent 的需求。
3. **工具命名** = `mcp__<slug>__<tool>`(模型调用的即此形态;`mcp__everything__echo`)。
4. **`--mcp-config <json>`** schema = `{"mcpServers":{"<slug>":{...}}}`;`--strict-mcp-config` 只用该文件的 server(隔离,推荐)。
5. **HTTP + headers**:`claude mcp add --transport http <slug> <url> --header "Authorization: Bearer ..."`(help 实证支持);等价 `--mcp-config` JSON 为 `{"mcpServers":{"<slug>":{"type":"http","url":"...","headers":{"Authorization":"Bearer ..."}}}}`。远程 HTTP MCP + 自定义头 = io 需要的形态,claude 支持。

## 结论(codex,已实测)

用现有 `CODEX_HOME/config.toml` 里的远程 HTTP MCP server,跑:

```
codex exec --skip-git-repo-check --json \
  --dangerously-bypass-approvals-and-sandbox \
  "Use the <server> MCP tool <read-only-tool> once..."
```

结果 JSONL 出现 `type:"mcp_tool_call"`, `server:"<slug>"`, `tool:"<tool>"`, `status:"completed"`,随后 agent final 返回 OK。说明:

1. **远程 HTTP MCP 可被 `codex exec` 加载并调用**。当前机器 `codex mcp list` 能识别 HTTP server,`codex exec` 真实发起并完成了 MCP tool call。
2. **无人值守可行**。在 agent-runtime 已采用的 `--dangerously-bypass-approvals-and-sandbox` 模式下,只读 MCP tool 未弹交互批准,直接执行完成。Codex 没有 claude 那种 `--allowed-tools` 参数;授权主要由 Codex 自身 approval/sandbox + MCP server/tool 配置决定。
3. **不要用 `-c mcp_servers.<slug>...` 动态塞 MCP**。`codex exec --strict-config -c 'mcp_servers.test.type="http"'` 会报 unknown configuration field。运行时应写入每用户 `CODEX_HOME/config.toml`。
4. **Codex CLI 自己写的 HTTP MCP schema 是 `url` + `bearer_token_env_var`**。`codex mcp add <slug> --url <url> --bearer-token-env-var TOKEN_ENV` 生成:

```
[mcp_servers.<slug>]
url = "https://example.com/mcp"
bearer_token_env_var = "TOKEN_ENV"
```

现有旧配置里 `type = "http"` 可被非 strict 运行加载,但 runtime renderer 应优先跟随 CLI 当前写法,不依赖 `type`。
5. **安全观察**:`codex mcp list` 会原样打印 HTTP MCP URL,query token 不会打码。io runtime 不应把 token 推荐放 URL query;Codex 侧 bearer token 应放环境变量并用 `bearer_token_env_var` 引用。Claude 侧 headers 写入 0600 的 per-user mcp config 文件。

## 仍未测
- Codex 对“任意自定义 headers(非 Authorization Bearer)”的原生支持未确认。CLI help 只暴露 `--bearer-token-env-var`;v1 renderer 先支持 Authorization Bearer → env var。需要非 Bearer header 的 MCP server 应先走 claude,或后续单独做 Codex 兼容 spike。

## 对 Phase I 的影响
- claude renderer + allowlist(I2/I3)可按上面的确认形态实现:`--mcp-config` 文件 + `--allowed-tools "mcp__<slug>"`(每个 enabled server 一个)。
- codex renderer(I1)写 `CODEX_HOME/config.toml`,把 LiteLLM gateway 的 `[model_providers.feedling_gateway]` 和用户 `[mcp_servers.<slug>]` 合并在同一个文件,不能互相覆盖。
- codex Authorization Bearer token 不写入 `config.toml`,而是写入 per-child env(`FEEDLING_MCP_<SLUG>_BEARER_TOKEN`)并由 `bearer_token_env_var` 引用。
- disable/delete 后,下一次 materialize home 必须 prune stale `config.toml` / `mcp.json`,避免旧 token 留在持久化 home。

## 成本记录
一次 `claude -p` opus 实测 ≈ $0.32。
