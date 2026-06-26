# Memory（指令文件）机制说明

本文档总结 claw-code 当前的 "memory" 逻辑。这里的 memory **不是**向量数据库式的语义记忆，而是 Claude Code 原生的 **memory files / 指令文件**机制——把 `CLAUDE.md` 一类约定文件按目录层级发现、去重、限额后，渲染进系统提示，作为跨会话的持久上下文。

实现集中在 `rust/crates/runtime/src/prompt.rs`（配置部分在 `rust/crates/runtime/src/config.rs`）。

## 1. 总览

一次会话的 memory 加载分四步：

1. **发现（discover）**：从当前工作目录逐级向上走到最近的 git 根，在每层目录收集约定的指令文件。
2. **读取（read）**：读入文件内容，跳过空文件与目录，存为 `ContextFile { path, content }`。
3. **去重（dedupe）**：按归一化后的内容哈希去重，保留首次出现的那份。
4. **渲染（render）**：按层级顺序拼成 `# Project instructions` 段落，受单文件与总量字符预算约束，注入系统提示。

对外入口：

- `ProjectContext::discover` / `discover_with_rules_import` / `discover_with_git`（`prompt.rs:96` 起）
- `load_system_prompt` / `load_system_prompt_with_context`（`prompt.rs:621`、`prompt.rs:635`）

核心数据结构 `ContextFile`（`prompt.rs:67`）：

```rust
pub struct ContextFile {
    pub path: PathBuf,
    pub content: String,
}
```

提供 `source()`（来源分类）与 `char_count()`（字符数）。

## 2. 发现：目录层级与就近原则

### 2.1 目录遍历范围

`instruction_discovery_dirs`（`prompt.rs:316`）从 cwd 出发，逐级向上收集目录，直到**最近的 git 根**为止（`nearest_git_root`，`prompt.rs:330`，向上找含 `.git` 的目录；找不到则以 cwd 为边界）。

得到的目录序列是 `[cwd, parent, …, git_root]`（cwd 在前）。随后 `discover_instruction_files`（`prompt.rs:289`）将其 `reverse()` 成 `[git_root, …, cwd]`（**根在前、cwd 在后**）再逐层处理——这决定了后续的去重与渲染顺序。

### 2.2 每层目录的候选文件

每层目录尝试读取这些文件（`prompt.rs:298`）：

| 路径 | 来源分类（`instruction_file_source`，`prompt.rs:271`） |
|---|---|
| `CLAUDE.md` | `claude_md` |
| `CLAW.md` | `claw_md` |
| `AGENTS.md` | `agents_md` |
| `CLAUDE.local.md` | `claude_local_md` |
| `.claw/CLAUDE.md` | `claw_claude_md` |
| `.claude/CLAUDE.md` | `claude_claude_md` |
| `.claw/instructions.md` | `claw_instructions` |

此外扫描两个规则目录（`push_rules_dir`，`prompt.rs:357`）：

- `.claw/rules`
- `.claw/rules.local`

规则目录内只收受支持的扩展名文件（`is_supported_rule_file`，`prompt.rs:378`）：`.md` / `.txt` / `.mdc`，并按路径排序后加入。

### 2.3 跨框架规则导入

`push_framework_imports`（`prompt.rs:389`）按配置导入其他 AI 编码工具的规则文件：

| 框架（开关键名） | 导入路径 |
|---|---|
| `cursor` | `.cursorrules`、`.cursor/rules`（目录） |
| `copilot` | `.github/copilot-instructions.md` |
| `windsurf` | `.windsurfrules` |
| `plandex` | `.plandex/instructions.md` |
| `crush` | `.crush/CLAUDE.md`、`.crush/rules`（目录） |

是否导入由 `RulesImportConfig`（`config.rs:170`）控制，来自 `.claw.json` 的 `rules_import` 字段：

- `Auto`（**默认**）：所有框架都尝试导入（`should_import` 恒为 true）。
- `None`：只保留 claw 自有指令文件，不导入外部框架。
- `List([...])`：只导入列出的框架（大小写不敏感）。

### 2.4 读取规则

`push_context_file`（`prompt.rs:342`）：

- 路径是目录 → 跳过。
- 内容为空或全空白 → 跳过（不计入）。
- 文件不存在（`NotFound`）→ 忽略，不报错。
- 其他 IO 错误 → 向上传播。

## 3. 去重

`dedupe_instruction_files`（`prompt.rs:542`）：

- 对每个文件内容先 `normalize_instruction_content`（`prompt.rs:559`：`collapse_blank_lines` 合并连续空行 + `trim`）。
- 用 `DefaultHasher` 算稳定哈希（`stable_content_hash`，`prompt.rs:563`）。
- 内容相同的文件**只保留第一次出现的那份**，其余丢弃。

因为遍历顺序是「根在前」，所以当父子目录存在内容相同的指令文件时，**更靠近根的那份获胜**。

## 4. 渲染与字符预算

`render_instruction_files`（`prompt.rs:519`）把去重后的文件拼成系统提示中的 `# Project instructions` 段落。

### 4.1 预算常量

定义在 `prompt.rs:43`：

| 常量 | 值 | 含义 |
|---|---|---|
| `MAX_INSTRUCTION_FILE_CHARS` | 4000 | 单个文件渲染后最大字符数 |
| `MAX_TOTAL_INSTRUCTION_CHARS` | 12000 | 所有指令文件总字符预算 |

（同文件另有 `MAX_GIT_DIFF_CHARS = 50000`，用于 git diff 截断，与指令文件无关。）

### 4.2 渲染流程

- 维护 `remaining_chars`，初值 `MAX_TOTAL_INSTRUCTION_CHARS`（`prompt.rs:521`）。
- 逐文件处理：
  - 单文件先截断到 `min(MAX_INSTRUCTION_FILE_CHARS, remaining_chars)`（`truncate_instruction_content`，`prompt.rs:582`）。超限时截取并追加 `\n\n[truncated]`。
  - 每个文件输出一个子标题 `## <文件名> (scope: <作用域目录>)`（`describe_instruction_file`，`prompt.rs:569`：文件名取 basename，作用域取该文件所属的最近父目录，找不到则为 `workspace`）。
  - 从 `remaining_chars` 扣减已消耗字符。
- 当 `remaining_chars` 归零，追加一行 `_Additional instruction content omitted after reaching the prompt budget._` 并停止（`prompt.rs:523`）。

## 5. 对外可见的元数据

发现到的文件最终通过 CLI 的 status 输出暴露（契约测试见 `rust/crates/rusty-claude-cli/tests/output_format_contract.rs:1293` 起）：

- `workspace.memory_file_count`：文件数量。
- `workspace.memory_files`：每个文件的来源分类（`source`）等元数据。

## 6. 行为要点小结

- **范围以 git 根为边界**：发现只在 cwd 到最近 git 根之间逐级向上，不读取用户 home 下的全局文件。
- **就近 = 靠近根优先**：去重时遍历顺序为「根在前」，内容相同保留更靠近根的一份；渲染顺序也是根在前、cwd 在后（更具体的指令排在更后面）。
- **不是语义记忆**：没有 embedding、没有相似度召回，纯按目录约定加载文本、全文（受限额）拼入提示。
- **静默限额**：单文件 4000 字符、总量 12000 字符，超出即截断并提示。
- **空文件与不存在文件被忽略**，不会进入上下文，也不计入数量。

## 7. 相关源码索引

| 功能 | 位置 |
|---|---|
| `ContextFile` 结构 | `rust/crates/runtime/src/prompt.rs:67` |
| 发现入口 `ProjectContext::discover*` | `rust/crates/runtime/src/prompt.rs:96` |
| 来源分类 `instruction_file_source` | `rust/crates/runtime/src/prompt.rs:271` |
| 文件发现 `discover_instruction_files` | `rust/crates/runtime/src/prompt.rs:289` |
| 目录遍历 `instruction_discovery_dirs` | `rust/crates/runtime/src/prompt.rs:316` |
| git 根定位 `nearest_git_root` | `rust/crates/runtime/src/prompt.rs:330` |
| 读取 `push_context_file` | `rust/crates/runtime/src/prompt.rs:342` |
| 规则目录 `push_rules_dir` | `rust/crates/runtime/src/prompt.rs:357` |
| 框架导入 `push_framework_imports` | `rust/crates/runtime/src/prompt.rs:389` |
| 渲染 `render_instruction_files` | `rust/crates/runtime/src/prompt.rs:519` |
| 去重 `dedupe_instruction_files` | `rust/crates/runtime/src/prompt.rs:542` |
| 截断 `truncate_instruction_content` | `rust/crates/runtime/src/prompt.rs:582` |
| 系统提示入口 `load_system_prompt*` | `rust/crates/runtime/src/prompt.rs:621` |
| 导入配置 `RulesImportConfig` | `rust/crates/runtime/src/config.rs:170` |
