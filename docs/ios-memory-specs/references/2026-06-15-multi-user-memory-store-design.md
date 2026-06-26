# 多用户加密 Memory 存储设计

- 日期：2026-06-15
- 状态：待评审
- 形态：独立 Rust crate `memory-store`（`MemoryStore` trait + Postgres 实现），不含网络层

## 1. 背景与目标

claw-code 现有的 memory 机制（`rust/crates/runtime/src/prompt.rs`）从本地文件系统读取 `CLAUDE.md` 等指令文件：从 cwd 逐级向上走到最近的 git 根，按目录层级"就近优先"加载，按内容哈希去重，受字符预算约束，最后渲染进系统提示。

本设计把这套"层级 + 就近优先 + 去重 + 预算"的语义搬到一个**独立后端**上，满足以下前提：

- **多用户**：一套存储服务多个租户，按 `user_id` 隔离。
- **服务端静态加密**：memory 正文以**已加密的不透明 blob** 形式存储，服务端永不解析正文。加密/解密过程不在本设计范围内——存储层只收发密文字节。
- **不使用向量库**：检索完全靠明文元数据（scope 层级 + 显式标签/键），不做语义检索。
- **PostgreSQL** 为存储后端，scope 用 `ltree` 建模层级。

非目标：

- 不实现加解密、不管理密钥。
- 不提供 REST/gRPC 网络层（先定义库接口，网络层后续单独设计）。
- 不做语义/相似度检索。

## 2. 设计支点

原机制有三处依赖"能读到明文"。在「正文是不透明密文」前提下，全部改为**明文元数据驱动**，服务端不解析正文：

| 原机制（明文） | 本设计（密文 + 元数据） |
|---|---|
| 按内容哈希去重 | 写入时由客户端附带 `content_hash`（明文哈希），存为元数据，去重在元数据层完成 |
| 按字符预算截断 | 写入时附带 `plaintext_chars`；读取时按元数据选出落入预算的条目。单条超限的截断在**加密前**由写入方负责，store 在写入时拒绝超限条目 |
| 目录层级（cwd→git 根） | `ltree` scope 的祖先匹配（`@>`） |

核心不变量：**任何明文（memory 正文）都不进入存储层**。存储层只见到密文 blob 和写入方主动提供的明文派生元数据（哈希、字符数）。

## 3. 数据模型

### 3.1 表结构

```sql
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- 让 (user_id, scope) 进同一 GiST 索引
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- 提供 gen_random_uuid()

CREATE TABLE memory_entries (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL,
    scope           LTREE       NOT NULL,            -- 如 'global'、'proj_abc.frontend'
    entry_key       TEXT        NOT NULL,            -- (user,scope) 内的逻辑名，如 'CLAUDE.md'
    source_type     TEXT        NOT NULL,            -- 对应原 instruction_file_source
    tags            TEXT[]      NOT NULL DEFAULT '{}',
    content_cipher  BYTEA       NOT NULL,            -- 不透明加密 blob
    content_hash    BYTEA       NOT NULL,            -- 明文哈希，去重 + 变更检测
    plaintext_chars INT         NOT NULL,            -- 预算用，明文字符数
    precedence      INT         NOT NULL DEFAULT 0,  -- 显式优先级覆盖
    version         BIGINT      NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);
```

### 3.2 索引

```sql
-- upsert 目标：同一 (user, scope, key) 下只有一条活跃记录
CREATE UNIQUE INDEX uq_active_entry ON memory_entries (user_id, scope, entry_key)
    WHERE deleted_at IS NULL;

-- 祖先查询（含租户键），依赖 btree_gist
CREATE INDEX ix_scope ON memory_entries USING GIST (user_id, scope);

-- 标签过滤
CREATE INDEX ix_tags  ON memory_entries USING GIN (tags);

-- 去重 / 变更检测
CREATE INDEX ix_hash  ON memory_entries (user_id, content_hash);
```

### 3.3 字段语义

- `scope`：层级路径。约定根段表示作用域类别（如 `global`、`proj_<id>`、`session_<id>`），更深段表示项目内子路径。
- `entry_key`：在 `(user_id, scope)` 内唯一标识一条 memory，是 upsert 的逻辑键（类比文件名）。
- `source_type`：对应原 `instruction_file_source` 的取值（`claude_md`、`claw_md`、`agents_md`、`rule_file` 等），仅作元数据与渲染分类，不影响检索逻辑。
- `content_hash`：明文哈希，由写入方计算。用于去重与变更检测；存储层不验证其与密文的对应关系（信任写入方）。
- `precedence`：默认 0；非 0 时覆盖默认的"按 scope 深度排序"，用于人为提升/压低某条优先级。

## 4. 多租户隔离与静态加密

- **应用层**：每条查询都带 `user_id`。
- **RLS 纵深防御**：开启行级安全，策略 `USING (user_id = current_setting('app.user_id')::uuid)`，`PgMemoryStore` 在每个事务开始时 `SET LOCAL app.user_id = $uid`。即使某条 SQL 漏写 `WHERE user_id`，也不会跨租户泄露。
- **静态加密**：
  - 应用层：`content_cipher` 本身即客户端密文（最强的一层）。
  - 存储层：依赖 Postgres 的落盘加密（TDE / 磁盘卷加密）作为额外一层，属运维范畴。
- **日志纪律**：禁止打印 `content_cipher`、`content_hash` 原文。

## 5. 核心检索：复刻"往上走 + 就近优先"

会话提供：`user_id` + 当前 scope（如 `proj_abc.frontend.components`）+ 可选标签过滤 + 预算。

```sql
SELECT id, scope, entry_key, source_type, content_cipher, content_hash, plaintext_chars, precedence
FROM   memory_entries
WHERE  user_id = $uid
  AND  scope @> $session_scope          -- scope 是会话 scope 的祖先或自身 → 取出整条路径
  AND  ($any_tags IS NULL OR tags && $any_tags)
  AND  deleted_at IS NULL
ORDER BY precedence DESC, nlevel(scope) ASC, scope, entry_key;
```

- `scope @> $session_scope`：一次取出 `global` + `proj_abc` + `…frontend` + `…components`，正是原"从 git 根走到 cwd"的集合。
- `nlevel(scope) ASC`：浅层（全局）在前、最具体的在后，与原 `render_instruction_files` 渲染顺序一致。
- 行取出后交给**纯函数选择器**（第 7 节）做去重 + 预算选择。

### 标签过滤语义

`TagFilter` 三态，默认 `None`：

- `None`：不按标签过滤，纯靠 scope。
- `Any(tags)`：`tags && $tags`（交集非空）。
- `All(tags)`：`tags @> $tags`（包含全部）。

## 6. `MemoryStore` trait 与模块边界

新建独立 crate `memory-store`：

```
memory-store/
├── src/lib.rs        # trait + 导出
├── src/types.rs      # Scope, UserId, MemoryId, 记录类型, MemoryError
├── src/query.rs      # SessionQuery / TagFilter / Budget + 去重&预算选择器（纯函数，无 DB 可测）
├── src/postgres.rs   # PgMemoryStore（sqlx）
├── src/memory.rs     # 内存实现，给测试用
└── migrations/0001_init.sql
```

### 6.1 trait

```rust
#[async_trait]
pub trait MemoryStore: Send + Sync {
    /// 按 (user, scope, entry_key) upsert，命中则 bump version。
    async fn put(&self, req: PutMemory) -> Result<MemoryRef, MemoryError>;

    /// 软删除（置 deleted_at）。未命中返回 Ok(false)。
    async fn delete(&self, user: &UserId, scope: &Scope, key: &str) -> Result<bool, MemoryError>;

    /// 按 id 取单条（含密文）。未命中返回 Ok(None)。
    async fn get(&self, user: &UserId, id: MemoryId) -> Result<Option<MemoryRecord>, MemoryError>;

    /// 会话加载：祖先匹配 + 标签过滤 + 去重 + 预算选择（第 5、7 节）。
    async fn load_for_session(&self, q: SessionQuery) -> Result<MemorySelection, MemoryError>;

    /// 管理用：列出某用户在某 scope 前缀下的所有条目元数据（不含密文）。
    async fn list(&self, user: &UserId, prefix: Option<&Scope>) -> Result<Vec<MemoryMeta>, MemoryError>;
}
```

### 6.2 关键类型

```rust
/// 校验过的 ltree 路径：每段匹配 [A-Za-z0-9_]+，'.' 连接。构造时校验，防 ltree 注入。
pub struct Scope(String);

pub struct PutMemory {
    pub user_id: UserId,
    pub scope: Scope,
    pub entry_key: String,
    pub source_type: String,
    pub tags: Vec<String>,
    pub content_cipher: Vec<u8>,   // 不透明密文
    pub content_hash: Vec<u8>,     // 明文哈希
    pub plaintext_chars: u32,      // 明文字符数
    pub if_match_version: Option<u64>, // 乐观并发，可选
}

pub struct SessionQuery {
    pub user_id: UserId,
    pub scope: Scope,
    pub tags: TagFilter,           // None / Any / All，默认 None
    pub budget: Budget,            // 仅 total_chars；单条上限是写入时的 store 配置，不在读取路径
}

pub struct MemorySelection {
    pub entries: Vec<SelectedEntry>, // 已去重、已排序、落入预算，含密文 + 元数据
    pub overflow: bool,              // 是否有条目因预算被丢弃
    pub dropped: Vec<DropReason>,    // 被丢弃条目的原因（去重/超预算）
}

pub enum MemoryError {
    EntryTooLarge { plaintext_chars: u32, limit: u32 },
    Conflict { expected: u64, actual: u64 },
    InvalidScope(String),
    Backend(String),
}
```

`SelectedEntry` 携带 `content_cipher` 与元数据，由调用方解密；存储层不解密。

## 7. 去重 + 预算选择器（纯函数）

`query.rs` 内的纯函数，输入排序好的 `Vec<MemoryMeta>`，无 DB 依赖、单独可测，复刻原 `dedupe_instruction_files` + `render_instruction_files` 的预算逻辑：

1. **去重**：按 `content_hash` 去重，保留排序中**第一个**出现的（即更浅/更靠前的 scope），其余记入 `dropped`（原因 `Duplicate`）。
2. **预算填充**：按排序累加 `plaintext_chars`，累计不超过 `budget.total_chars`；超出后的条目记入 `dropped`（原因 `BudgetExceeded`），置 `overflow = true`。
3. 排序由 SQL 的 `ORDER BY precedence DESC, nlevel ASC, scope, entry_key` 保证，选择器不再重排。

单条超限（`plaintext_chars > budget.per_entry_chars`）**不在此处截断**——截断责任在加密前的写入方；store 在 `put` 时即拒绝超过配置上限的条目。

## 8. 数据流

**写入**：客户端加密正文 → 计算明文哈希 + 明文字符数 → `put(PutMemory)` 按 `(user, scope, key)` upsert。store 校验 `plaintext_chars` 不超过配置上限（否则 `EntryTooLarge`），命中则 bump `version`。

**读取（会话开始）**：客户端构造 `SessionQuery(scope, tags, budget)` → store 执行第 5 节 SQL（祖先 + 标签 + 未删除，排序）→ 纯选择器去重 + 填预算 → 返回 `MemorySelection`（密文 + 元数据 + overflow）→ 客户端逐条解密 → 渲染进系统提示（类比 `render_instruction_files`）。

## 9. 错误处理

- 单条 `plaintext_chars` 超过配置上限 → `EntryTooLarge`，拒绝写入。
- `put` 携带 `if_match_version` 且不匹配 → `Conflict`。
- `get` / `delete` 未命中 → `Ok(None)` / `Ok(false)`。
- 非法 scope（label 不合规）→ 构造 `Scope` 时即 `InvalidScope`。
- 后端错误统一包进 `MemoryError::Backend`。

## 10. 测试策略

- **纯选择器单测**（`query.rs`）：排序后去重保留最浅条目、预算填充与 `overflow` 标记、`dropped` 原因正确、空输入。
- **trait 契约测试**：一套契约同时对 `memory`（内存实现）和 `postgres` 跑，保证行为一致。
- **Postgres 集成测试**（testcontainers 或事务回滚 fixture）：
  - `scope @> session_scope` 祖先查询正确取出整条路径。
  - 三种 `TagFilter` 语义。
  - **跨用户 RLS 隔离**：用户 A 的查询拿不到用户 B 的数据。
  - upsert 命中 bump version；`if_match_version` 冲突。
  - 软删除后不再被 `load_for_session` / `list` 返回。

## 11. 与现有 claw-code 的关系

本 crate 独立、不修改现有 `prompt.rs`。未来若要接入 claw-code，客户端可在解密 `MemorySelection` 后构造等价的 `ContextFile` 列表喂给现有渲染逻辑——但这属于后续集成工作，不在本设计范围内。

## 12. 待定 / 后续

- 网络层（REST/gRPC）单独设计。
- 加解密与密钥管理由调用方负责，本设计仅约定字节进出。
- 配置项（`per_entry_chars` 上限、默认 `total_chars` 预算）的来源（环境变量/配置文件）后续确定。
