# Memory Readside Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, no-iOS, no-backend demo that converts existing `MemoryMoment`-shaped data into agent-readable `IndexItem` and `FetchResult` outputs.

**Architecture:** Add an isolated Python standard-library module under `tools/memory_readside_demo/`. It models the current iOS/backend `MemoryMoment` shape, maps it into read-side adapter outputs, and includes tests plus a runnable demo script. It does not modify iOS app code, backend APIs, database schema, encryption, or current Memory Garden rendering.

**Tech Stack:** Python 3 standard library, `dataclasses`, `unittest`, JSON demo output.

---

## File Structure

- Create: `tools/memory_readside_demo/__init__.py`
- Create: `tools/memory_readside_demo/adapter.py`
- Create: `tools/memory_readside_demo/demo.py`
- Create: `tools/memory_readside_demo/test_adapter.py`
- Create: `Docs/IO-memory-readside-demo-说明.md`

## Product Reading

专业说法：这个 demo 是 read-side adapter，不是 storage migration。  
人话：旧 memory 不动，只是在读取时临时变成“目录 + 详情”。

专业说法：不需要跑 iOS。  
人话：你用一条命令就能看到转换前后 JSON 长什么样。

---

### Task 1: Adapter Types and Mapping

**Files:**
- Create: `tools/memory_readside_demo/__init__.py`
- Create: `tools/memory_readside_demo/adapter.py`
- Test: `tools/memory_readside_demo/test_adapter.py`

- [ ] **Step 1: Write tests for current MemoryMoment to IndexItem / FetchResult mapping**

```python
from tools.memory_readside_demo.adapter import (
    MemoryMoment,
    memory_moment_to_fetch_result,
    memory_moment_to_index_item,
)


def test_memory_moment_to_index_item_prefers_description_and_linked_dimension():
    moment = MemoryMoment(
        id="mem_123",
        type="quote",
        title="不要先给建议",
        description="用户不喜欢在难过时被立刻给建议。",
        her_quote="你每次一给步骤我就觉得你不在我身边。",
        context="一次情绪崩溃的聊天",
        linked_dimension="陪伴方式",
    )

    item = memory_moment_to_index_item(moment)

    assert item.memory_id == "mem_123"
    assert item.summary == "用户不喜欢在难过时被立刻给建议。"
    assert item.bucket == "陪伴方式"
    assert item.type == "quote"


def test_memory_moment_to_fetch_result_keeps_verbatim_and_context():
    moment = MemoryMoment(
        id="mem_123",
        type="quote",
        title="不要先给建议",
        description="用户不喜欢在难过时被立刻给建议。",
        her_quote="你每次一给步骤我就觉得你不在我身边。",
        context="一次情绪崩溃的聊天",
        linked_dimension="陪伴方式",
    )

    result = memory_moment_to_fetch_result(moment)

    assert result.memory_id == "mem_123"
    assert result.summary == "用户不喜欢在难过时被立刻给建议。"
    assert result.verbatim == "你每次一给步骤我就觉得你不在我身边。"
    assert result.context == "一次情绪崩溃的聊天"
    assert result.source_type == "quote"
```

- [ ] **Step 2: Run tests and verify they fail before implementation**

Run: `python3 -m unittest tools.memory_readside_demo.test_adapter -v`

Expected: FAIL because `tools.memory_readside_demo.adapter` does not exist yet.

- [ ] **Step 3: Implement adapter types and mapping**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryMoment:
    id: str
    type: str
    title: str = ""
    description: str = ""
    her_quote: str = ""
    context: str = ""
    linked_dimension: str = ""


@dataclass(frozen=True)
class MemoryIndexItem:
    memory_id: str
    summary: str
    bucket: str
    type: str


@dataclass(frozen=True)
class MemoryFetchResult:
    memory_id: str
    summary: str
    verbatim: str
    context: str
    source_type: str
    bucket: str


def _summary_for(moment: MemoryMoment) -> str:
    return moment.description.strip() or moment.title.strip()


def _bucket_for(moment: MemoryMoment) -> str:
    return moment.linked_dimension.strip() or moment.context.strip() or "uncategorized"


def memory_moment_to_index_item(moment: MemoryMoment) -> MemoryIndexItem:
    return MemoryIndexItem(
        memory_id=moment.id,
        summary=_summary_for(moment),
        bucket=_bucket_for(moment),
        type=moment.type or "unknown",
    )


def memory_moment_to_fetch_result(moment: MemoryMoment) -> MemoryFetchResult:
    return MemoryFetchResult(
        memory_id=moment.id,
        summary=_summary_for(moment),
        verbatim=moment.her_quote.strip(),
        context=moment.context.strip(),
        source_type=moment.type or "unknown",
        bucket=_bucket_for(moment),
    )


def to_jsonable(value: Any) -> dict[str, Any]:
    return asdict(value)
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python3 -m unittest tools.memory_readside_demo.test_adapter -v`

Expected: PASS.

---

### Task 2: Demo Script

**Files:**
- Create: `tools/memory_readside_demo/demo.py`
- Test: `tools/memory_readside_demo/test_adapter.py`

- [ ] **Step 1: Add demo output test**

```python
from tools.memory_readside_demo.demo import build_demo_payload


def test_demo_payload_contains_legacy_index_and_fetch_sections():
    payload = build_demo_payload()

    assert payload["legacy_memory_moment"]["id"] == "mem_123"
    assert payload["index_item"]["memory_id"] == "mem_123"
    assert payload["fetch_result"]["memory_id"] == "mem_123"
    assert "her_quote" in payload["legacy_memory_moment"]
    assert "verbatim" not in payload["index_item"]
    assert payload["fetch_result"]["verbatim"]
```

- [ ] **Step 2: Implement demo script**

```python
from __future__ import annotations

import json
from dataclasses import asdict

from tools.memory_readside_demo.adapter import (
    MemoryMoment,
    memory_moment_to_fetch_result,
    memory_moment_to_index_item,
    to_jsonable,
)


def sample_memory_moment() -> MemoryMoment:
    return MemoryMoment(
        id="mem_123",
        type="quote",
        title="不要先给建议",
        description="用户不喜欢在难过时被立刻给建议。",
        her_quote="你每次一给步骤我就觉得你不在我身边。",
        context="一次情绪崩溃的聊天",
        linked_dimension="陪伴方式",
    )


def build_demo_payload() -> dict[str, object]:
    moment = sample_memory_moment()
    index_item = memory_moment_to_index_item(moment)
    fetch_result = memory_moment_to_fetch_result(moment)

    return {
        "legacy_memory_moment": asdict(moment),
        "index_item": to_jsonable(index_item),
        "fetch_result": to_jsonable(fetch_result),
        "product_reading": {
            "index_item": "agent 先看的目录，只放摘要和分类，不暴露原话正文。",
            "fetch_result": "agent 选中后再拿的详情，包含原话和上下文。",
        },
    }


def main() -> None:
    print(json.dumps(build_demo_payload(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run tests**

Run: `python3 -m unittest tools.memory_readside_demo.test_adapter -v`

Expected: PASS.

- [ ] **Step 4: Run demo**

Run: `python3 -m tools.memory_readside_demo.demo`

Expected: JSON output with `legacy_memory_moment`, `index_item`, and `fetch_result`.

---

### Task 3: Product-facing Explanation

**Files:**
- Create: `Docs/IO-memory-readside-demo-说明.md`

- [ ] **Step 1: Write explanation doc**

```markdown
# IO Memory Read-side Demo 说明

## 一句话

这个 demo 不改线上 memory，只演示如何把当前 `MemoryMoment` 读成 agent 可用的 `index + fetch` 两层结构。

## 现在的输入

当前 Memory Garden 里的旧结构更像展示卡：

```json
{
  "id": "mem_123",
  "type": "quote",
  "title": "不要先给建议",
  "description": "用户不喜欢在难过时被立刻给建议。",
  "her_quote": "你每次一给步骤我就觉得你不在我身边。",
  "context": "一次情绪崩溃的聊天",
  "linked_dimension": "陪伴方式"
}
```

## 转换后的 index

```json
{
  "memory_id": "mem_123",
  "summary": "用户不喜欢在难过时被立刻给建议。",
  "bucket": "陪伴方式",
  "type": "quote"
}
```

人话：这是给 agent 先看的目录。

## 转换后的 fetch

```json
{
  "memory_id": "mem_123",
  "summary": "用户不喜欢在难过时被立刻给建议。",
  "verbatim": "你每次一给步骤我就觉得你不在我身边。",
  "context": "一次情绪崩溃的聊天",
  "source_type": "quote",
  "bucket": "陪伴方式"
}
```

人话：这是 agent 选中后再拿的详情。

## 怎么看效果

运行：

```bash
python3 -m tools.memory_readside_demo.demo
```

如果输出里同时有 `legacy_memory_moment`、`index_item`、`fetch_result`，就说明旧 memory 已经可以被读成新结构。

## 这一步不做什么

- 不改数据库
- 不改 iOS
- 不改真实后端
- 不做 eval
- 不做 commit/supersede/merge

## 下一步

拿这个 demo 给 zhihao 看，确认真实后端里 `index/fetch` 应该在 backend 还是 enclave 生成。
```

- [ ] **Step 2: Verify doc can be read**

Run: `sed -n '1,220p' Docs/IO-memory-readside-demo-说明.md`

Expected: The doc explains input, index output, fetch output, and how to run the demo.

---

### Task 4: Final Verification

**Files:**
- Verify: `tools/memory_readside_demo/adapter.py`
- Verify: `tools/memory_readside_demo/demo.py`
- Verify: `tools/memory_readside_demo/test_adapter.py`
- Verify: `Docs/IO-memory-readside-demo-说明.md`

- [ ] **Step 1: Run all demo tests**

Run: `python3 -m unittest tools.memory_readside_demo.test_adapter -v`

Expected: PASS.

- [ ] **Step 2: Run demo and save output for review**

Run: `python3 -m tools.memory_readside_demo.demo`

Expected: JSON output showing current input, index output, and fetch output.

- [ ] **Step 3: Check git diff**

Run: `git diff --stat`

Expected: Only demo files and explanation doc changed.

---

## Time Estimate

- Adapter + tests: 1.5h
- Demo script: 1h
- Product-facing doc: 0.5h
- Verification + fix loop: 1h

Total: 4h normal pace, 2-3h high-intensity if no environment issues.

## Self-review

Spec coverage:
- Covers read-side adapter only.
- Does not implement commit/supersede/merge/decay because user requested the smaller step 2.
- Includes a way to view effect without iOS.

Placeholder scan:
- No TBD/TODO placeholders.

Type consistency:
- `MemoryMoment`, `MemoryIndexItem`, `MemoryFetchResult`, `memory_moment_to_index_item`, and `memory_moment_to_fetch_result` are used consistently across tests, implementation, and demo.

