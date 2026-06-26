# IO 记忆核心 · 工程 spec(下一步)

> **作者:Claude(Opus 4.8)** · 2026-06-19 · 状态:草案,待与 Codex 版对照 + 与 zhihao 对齐接口
> 范围:**只覆盖"下一步该建什么"**——一个本地、纯逻辑的"记忆核心" + 接口契约。**不含 eval、不含加密、不含网络、不含真数据库。**

---

## 0. 一句话

把 spec v1 落成一个**本地内存版的"记忆核心"模块**(明文、纯函数、可测可演示),实现 `commit / index / fetch / decay` 这套语义。它既是**马上能跑的产出**,又是 **zhihao 实现真后端(enclave+Postgres+加密)时照抄的契约/参考实现**。

照 claw-code `memory-store` crate 的分法:**核心只管记忆逻辑,不含加解密/网络/DB**——那一层是 zhihao 的。

---

## 1. 目标 / 非目标

**目标(本 spec 要交付的):**
- 一份**接口契约**(Card 类型 + MemoryCore 方法签名)。
- 一个**内存实现 `InMemoryMemoryCore`**(纯逻辑,无副作用依赖)。
- 一套**单元测试**,证明语义正确。
- (M2,可选)一个 **agent recall 回路 demo**,跑在该核心上。

**非目标(明确不做,交给别人/以后):**
- ❌ 加密 / enclave 解密(zhihao,真实现时包在外层)
- ❌ Postgres / 持久化(zhihao)
- ❌ 网络 endpoint(zhihao 把 core 包成 index()/fetch() HTTP/MCP)
- ❌ eval(后续单独做)
- ❌ 向量 / embedding(spec 里是可选 scale-out,本期不碰)
- ❌ UI

---

## 2. 接口契约(这部分要先和 zhihao 对齐)

### 2.1 Card 类型

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class Status(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    CONTRADICTED = "contradicted"
    ARCHIVED = "archived"          # 仅用户主动删时进入

class Salience(str, Enum):
    LOW = "low"; MEDIUM = "medium"; HIGH = "high"; CRITICAL = "critical"

@dataclass
class Provenance:
    route: str                     # "vps" | "api" | "dream"
    committer: str                 # 谁提议的(agent id / "consolidation")
    confidence: float              # 0..1
    source_ts: float

@dataclass
class Card:
    id: str
    user_id: str
    summary: str                   # 一行,index 用(给 agent 看着挑)
    verbatim: str                  # 全文,fetch 用(深读)
    bucket_refs: list[str]         # 所属桶(实体/话题),如 ["妈妈","工作烦恼"]
    status: Status = Status.ACTIVE
    superseded_by: Optional[str] = None
    supersedes: list[str] = field(default_factory=list)
    is_open_thread: bool = False
    follow_up: Optional[str] = None
    topic_tags: list[str] = field(default_factory=list)
    salience: Salience = Salience.MEDIUM
    importance: float = 0.5        # 0..1,decay 用
    last_active: float = 0.0
    last_surfaced: Optional[float] = None
    surface_count: int = 0
    valid_from: Optional[float] = None
    valid_to: Optional[float] = None
    pinned: bool = False
    decay_exempt: bool = False
    sensitive_scope: Optional[str] = None   # 如 "adult_preference_boundary"
    provenance: Optional[Provenance] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    version: int = 1
```

> ⚠️ 字段原则(spec v1):**每个字段必须有一个"用它的操作",否则砍。** 上面每个都对得上 commit / index / decay / surface / 矛盾时解之一。

### 2.2 MemoryCore 方法签名

```python
@dataclass
class IndexEntry:                  # index() 返回的轻量条目(不含 verbatim)
    id: str
    bucket_refs: list[str]
    summary: str
    status: Status
    salience: Salience
    is_open_thread: bool
    score: float                   # 排序用(importance × decay,见 §3.3)

@dataclass
class IndexFilter:
    buckets: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    keyword: Optional[str] = None          # 粗筛,非精确匹配器
    status_in: list[Status] = field(default_factory=lambda: [Status.ACTIVE])
    open_thread_only: bool = False
    limit: int = 50

class MemoryCore(Protocol):
    # 写:唯一入口,四种 op
    def commit(self, op: "CommitOp") -> "CommitResult": ...
    # 读:两步
    def index(self, user_id: str, f: IndexFilter) -> list[IndexEntry]: ...
    def fetch(self, user_id: str, ids: list[str]) -> list[Card]: ...
    # 维护
    def decay_scores(self, user_id: str, now: float) -> dict[str, float]: ...   # 纯函数,只读
    # 桶
    def resolve_buckets(self, user_id: str, proposed: list[str]) -> list["BucketMatch"]: ...
```

### 2.3 commit op(写入唯一通道,确定性,无 LLM)

```python
CommitOp =
  | Insert(card: Card)
  | Supersede(target_id: str, new_card: Card)
  | Merge(target_ids: list[str], merged_card: Card)
  | Contradict(a_id: str, b_id: str)
```

---

## 3. 行为规约(确定性,可逐条写测试)

### 3.1 commit 语义
- **Insert**:写一张 `ACTIVE` 卡。`resolve_buckets` 先跑,新桶太像旧桶 → 复用旧桶名(不新建)。
- **Supersede(target, new)**:`target.status=SUPERSEDED`、`target.superseded_by=new.id`;`new.supersedes=[target]`、`new` 为 `ACTIVE`。**target 永不删**。
- **Merge(targets, merged)**:所有 `targets.status=SUPERSEDED` 且 `superseded_by=merged.id`;`merged.supersedes=targets`、`ACTIVE`;`merged.bucket_refs` = 并集。
- **Contradict(a,b)**:a、b **都保持 ACTIVE**,互相写 `status=CONTRADICTED`(或加 `contradicted_by` 字段——本期先用 status),并标记需 surface 问用户。
- **永不硬删**:没有 delete op;`ARCHIVED` 只由"用户主动删"设置(本期可加一个 `archive(id)` 辅助,但归用户操作)。

### 3.2 index 语义(粗筛,不是精确匹配器)
- 默认只返回 `status_in=[ACTIVE]`(排除 superseded/contradicted/archived)。
- `buckets` / `tags`:命中其一即入选(交集非空)。
- `keyword`:对 `summary` 做**包含/分词命中**(粗筛缩短清单,**不指望它精确**——精确判断是 agent 读 summary 后挑)。
- `open_thread_only`:只返回 `is_open_thread=True`。
- 返回 **IndexEntry(不含 verbatim)**,按 `score` 降序,截到 `limit`。

### 3.3 decay / score(纯函数,只用元数据,不解密正文)
- `score = importance × recency(last_active, now) × affect(salience)`;`recency` 用 `e^(-λ·days)`(λ 参数先给默认,后续 eval 调)。
- **豁免**:`pinned` 或 `decay_exempt` 或(`is_open_thread` 且未 resolved)→ score 不衰减(固定高)。**这条必须有,否则重蹈 Ombre"永久桶被衰减"的 bug。**

### 3.4 resolve_buckets(resolve-before-create)
- 输入提议的桶名列表,输出每个名字的"已有近义桶"匹配(规范化 + 字符/token 相似)。调用方(propose)优先复用,避免桶爆炸。

---

## 4. 模块结构

```
memory_core/
├── types.py        # Card / Status / Salience / Provenance / IndexEntry / IndexFilter / CommitOp
├── core.py         # MemoryCore Protocol + InMemoryMemoryCore 实现
├── decay.py        # decay/score 纯函数(可移植 Ombre decay_engine)
├── buckets.py      # resolve_buckets 纯函数
└── tests/
    ├── test_commit.py
    ├── test_index_fetch.py
    ├── test_decay.py
    └── test_buckets.py
```

> 语言建议 **Python**(对齐 feedling-mcp,zhihao 可直接复用核心逻辑、只换存储/加密外壳)。纯模块,不引 DB/网络/加密依赖。

---

## 5. 测试清单(证明语义,M1 的验收)

- commit:insert 产 ACTIVE;supersede 后旧卡 SUPERSEDED+superseded_by、新卡 supersedes;merge 桶取并集、targets 全 superseded;contradict 后两卡都 ACTIVE 且标 CONTRADICTED;**任何 op 后旧卡都还在(永不删)**。
- index/fetch:index 排除 superseded;keyword 粗筛命中;open_thread_only 生效;按 score 排序 + limit;fetch 按 id 取回完整卡;fetch 不存在的 id 跳过。
- decay:pinned/decay_exempt/未解决 open_thread 豁免;其余按时间衰减;**最小阈值以下不会被错误吞掉**(防 bug)。
- buckets:近义桶被识别复用,不新建。
- 跨用户隔离:user_A 的 index/fetch 拿不到 user_B 的卡(即使内存里共存)。

---

## 6.(M2,可选)agent recall 回路 demo

一个驱动:给一段对话 →
1. **propose**:产出候选卡(走 `resolve_buckets` + `commit(Insert/Supersede/...)`)。
2. **recall**:形成 `IndexFilter` → `index()` → **agent 读 summary 挑 ids**(这一步可接真 LLM,或先用 stub)→ `fetch(ids)` → 用进回复;不够再 index。

产出 = **本地能演示的"写卡 + agentic 召回"全回路**,无需真后端/eval。

---

## 7. 明文 / 加密边界(写给 zhihao 看)

- **本核心全程跑明文**(内存里的 Card.verbatim 是明文)。
- zhihao 的真实现 = **同一套接口 + 外面包一层**:
  - 存:`commit` 落库前,把 verbatim/summary/坐标 加密成密文,元数据(status/bucket/importance…)明文存 JSONB。
  - 读:`index()` 在 **enclave 内**解密 summary 拼清单;`fetch()` 在 enclave 内解密 verbatim。
  - `decay_scores` 只用元数据 → 可在普通后端跑,不必进 enclave。
- **即:我做明文逻辑核心,你做"加密 + Postgres + enclave 落地"。接口不变,换实现即可。**

---

## 8. 必须与 zhihao 对齐的接口清单(冻结项)

1. **Card 字段 + 类型**(§2.1)——他存 JSONB 的形状。
2. **commit op 集合 + 语义**(§2.3 / §3.1)——他在 enclave 内做确定性执行。
3. **index/fetch 的入参/返回 + 解密在哪**(§2.2 / §3.2)——他建 endpoint。
4. **decay 在哪算**(§3.3)——建议普通后端(只用元数据)。
5. **加密边界**(§7)——核心明文、外层加密的分法。
6. **route B vs route A 怎么共用这套 index/fetch**(后端注入 vs agent 经工具)。

> 流程:这 6 条**半小时对齐冻住** → 你建内存核心 + 测试(M1)、他建真后端,平行 → 最后用真后端替内存实现。

---

## 9. 里程碑

- **M1(下一步,本 spec 主体)**:接口契约 + InMemoryMemoryCore + 测试。产出:可跑可测的记忆核心。
- **M2**:agent recall 回路 demo(propose + index/pick/fetch)。
- **M3(交给 zhihao,平行)**:真后端实现同接口(enclave+Postgres+加密)。

---

## 附:给 Codex 版对照的检查点

两份 spec 重点对这几处差异:
1. **Card 字段集**:谁多谁少?哪些字段没有"用它的操作"(该砍)?
2. **commit op 粒度**:contradict 用 status 还是单独字段?merge 怎么处理桶/正文?
3. **index 是不是"纯粗筛"**:相关性判断到底放 core 还是放 agent?(我这版坚持:core 只粗筛,agent 判断)
4. **decay 豁免规则**是否都覆盖(pinned/exempt/open_thread)。
5. **明文/加密边界**切在哪(core 含不含加密)?
6. **语言 / 模块边界**:是否同意"独立纯核心、不含 DB/网络/加密"。
7. **范围**:有没有人把 eval / 向量 / endpoint 塞进"下一步"(我这版明确踢出)。
