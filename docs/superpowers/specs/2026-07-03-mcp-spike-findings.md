# MCP spike findings — claude CLI (Phase K)

日期:2026-07-03
配套:`feedling-mcp-ios` `Docs/superpowers/specs/2026-07-03-mcp-client-design.md` §11。
环境:claude CLI 2.1.196,node v24,`@modelcontextprotocol/server-everything`(stdio 测试 server)。**codex CLI 不在本机 → codex 半未测。**

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

## 仍未测(交 Codex / xyn)
- **codex**:`codex exec` + `config.toml [mcp_servers.<slug>] type="http"` 能否连远程 MCP、无人值守调用、授权语义(codex 可能走 `approvals`/`sandbox` 而非 `--allowed-tools`)。本机无 codex CLI,未测。
- **config.toml 合并**:MCP 的 `[mcp_servers]` 与现有 gateway `config.toml`(`_codex_gateway_config`)共存不互相覆盖 —— codex 侧,未测。

## 对 Phase I 的影响
- claude renderer + allowlist(I2/I3)可按上面的确认形态实现:`--mcp-config` 文件 + `--allowed-tools "mcp__<slug>"`(每个 enabled server 一个)。
- codex renderer(I1)仍需在有 codex CLI 的环境先小验一次授权语义,再实现。

## 成本记录
一次 `claude -p` opus 实测 ≈ $0.32。
