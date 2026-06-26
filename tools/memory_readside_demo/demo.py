from __future__ import annotations

import json
from dataclasses import asdict

from tools.memory_readside_demo.adapter import (
    InMemoryMemoryCore,
    MemoryCard,
    MemoryMoment,
    Salience,
    memory_moment_to_card,
    to_jsonable,
)


def sample_legacy_memory_moment() -> MemoryMoment:
    return MemoryMoment(
        id="mem_old_advice",
        type="quote",
        title="不要先给建议",
        description="用户不喜欢在难过时被立刻给建议。",
        her_quote="你每次一给步骤我就觉得你不在我身边。",
        context="一次情绪崩溃的聊天",
        linked_dimension="陪伴方式",
    )


def sample_insert_card() -> MemoryCard:
    return MemoryCard(
        id="mem_ritual_moon",
        summary="用户希望睡前被叫“小月亮”,这是亲密关系里的睡前仪式。",
        verbatim="以后睡前你都叫我小月亮好不好?",
        bucket_refs=["关系仪式", "睡前陪伴"],
        source_type="quote",
        context="用户主动提出的关系仪式",
        salience=Salience.HIGH,
        follow_up="睡前场景可以自然使用“小月亮”,不要在正式或无关场景硬提。",
    )


def sample_superseding_card() -> MemoryCard:
    return MemoryCard(
        id="mem_new_advice_boundary",
        summary="用户崩溃时先需要陪伴和在场感;稳定后可以一起分析问题。",
        verbatim="不是永远不要建议,是我崩溃的时候不要先给步骤。等我缓过来,你可以陪我一起想办法。",
        bucket_refs=["安抚方式", "情绪崩溃"],
        source_type="quote",
        context="用户纠正了旧记忆:不是拒绝所有建议,而是区分情绪阶段。",
        salience=Salience.HIGH,
        follow_up="类似场景先表达在场,等用户稳定后再询问是否一起处理。",
    )


def build_demo_payload() -> dict[str, object]:
    legacy_moment = sample_legacy_memory_moment()
    legacy_card = memory_moment_to_card(legacy_moment, salience=Salience.HIGH)
    legacy_card_v1 = to_jsonable(legacy_card)
    core = InMemoryMemoryCore([legacy_card])

    initial_index = core.index()
    initial_fetch = core.fetch([initial_index[0].memory_id])

    inserted_card = core.insert(sample_insert_card())
    superseding_card = core.supersede("mem_old_advice", sample_superseding_card())

    active_index = core.index()
    active_fetch = core.fetch([item.memory_id for item in active_index])
    old_fetch_default = core.fetch(["mem_old_advice"])

    return {
        "legacy_memory_moment": asdict(legacy_moment),
        "legacy_card_v1": legacy_card_v1,
        "initial_readside": {
            "index": to_jsonable(initial_index),
            "fetch": to_jsonable(initial_fetch),
        },
        "writeside": {
            "inserted": to_jsonable(inserted_card),
            "superseding": to_jsonable(superseding_card),
        },
        "after_commit": {
            "active_index": to_jsonable(active_index),
            "active_fetch": to_jsonable(active_fetch),
            "old_fetch_default": to_jsonable(old_fetch_default),
        },
        "product_reading": {
            "legacy_card_v1": "旧 MemoryMoment 不迁移,读取时适配成 MemoryCard v1。",
            "index": "agent 先看的目录,不包含 verbatim 原话。",
            "fetch": "agent 选中后再拿正文和上下文。",
            "insert": "新记忆能进入卡库,并出现在 active index。",
            "supersede": "新理解替代旧理解;旧卡保留但默认不再召回。",
        },
    }


def main() -> None:
    print(json.dumps(build_demo_payload(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
