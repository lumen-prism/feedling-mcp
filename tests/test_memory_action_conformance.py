from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("FEEDLING_API_URL", "http://localhost:5001")
os.environ.setdefault("FEEDLING_API_KEY", "test_key_00000000")
os.environ.setdefault("AGENT_MODE", "http")
os.environ.setdefault("AGENT_HTTP_URL", "http://localhost:8080/chat")
os.environ.setdefault("CHECKPOINT_FILE", "/tmp/feedling_test_checkpoint.json")

import hosted_runtime as runtime  # noqa: E402
import tools.chat_resident_consumer as consumer  # noqa: E402


def _route_b_executor_action(action: dict) -> dict:
    coerced = runtime.coerce_runtime_action(action, [], direct_confidence=0.9)
    assert coerced is not None
    assert coerced["domain"] == "memory"
    return coerced["executor_action"]


def _route_a_executor_action(action: dict) -> dict:
    return consumer._normalize_v2_action_type(action)


@pytest.mark.parametrize(
    "action",
    [
        {
            "type": "memory.create",
            "confidence": 0.95,
            "payload": {
                "memory": {
                    "type": "fact",
                    "summary": "用户有只猫叫武松，是橘猫。",
                    "verbatim": "武松其实是橘猫。",
                    "occurred_at": "2026-06-23",
                    "source": "hosted_runtime_state",
                }
            },
            "reason": "User stated a durable pet fact.",
        },
        {
            "type": "memory.supersede",
            "confidence": 0.96,
            "target": {"memory_id": "mem_old_cat"},
            "payload": {
                "memory": {
                    "type": "fact",
                    "summary": "武松其实是橘猫。",
                    "verbatim": "我记错了，武松其实是橘猫。",
                    "occurred_at": "2026-06-23",
                    "source": "hosted_runtime_state",
                }
            },
            "reason": "User corrected an old cat breed memory.",
        },
        {
            "type": "memory.patch",
            "confidence": 0.93,
            "target": {"memory_id": "mem_patch_cat"},
            "payload": {"patch": {"summary": "武松喜欢贴着用户睡觉。"}},
            "reason": "User refined a memory.",
        },
        {
            "type": "memory.delete",
            "confidence": 0.99,
            "target": {"memory_id": "mem_delete_me"},
            "reason": "User asked to remove this memory.",
        },
    ],
)
def test_route_a_and_route_b_memory_actions_normalize_to_same_executor_action(action):
    assert _route_a_executor_action(action) == _route_b_executor_action(action)
