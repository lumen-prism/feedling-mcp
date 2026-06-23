from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from unittest.mock import patch


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

import tools.chat_resident_consumer as crc  # noqa: E402
from memory import context_layout  # noqa: E402


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = body
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


def test_route_a_memory_recall_flag_off_does_not_call_backend(monkeypatch):
    monkeypatch.setattr(crc, "ROUTE_A_MEMORY_RECALL_ENABLED", False, raising=False)

    def fail_post(*_args, **_kwargs):
        raise AssertionError("memory recall backend should not be called when flag is off")

    monkeypatch.setattr(crc.httpx, "post", fail_post)

    assert crc._memory_recall_for_message("武松是什么猫？") == ("", [])


def test_route_a_memory_recall_fetches_and_formats_top_cards(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(crc, "ROUTE_A_MEMORY_RECALL_ENABLED", True, raising=False)
    monkeypatch.setattr(crc, "ROUTE_A_MEMORY_RECALL_LIMIT", 50, raising=False)
    monkeypatch.setattr(crc, "ROUTE_A_MEMORY_RECALL_TOP_K", 2, raising=False)

    def fake_post(url, *, headers, json, timeout):
        calls.append((url, dict(json or {})))
        if url.endswith("/v1/memory/index"):
            return _FakeResponse({
                "items": [
                    {"id": "mem_cat", "summary": "武松是橘猫。", "score": 0.92},
                    {"id": "mem_name", "summary": "武松名字来自武松打虎。", "score": 0.88},
                    {"id": "mem_other", "summary": "其他低相关记忆。", "score": 0.1},
                ]
            })
        if url.endswith("/v1/memory/fetch"):
            assert json == {"ids": ["mem_cat", "mem_name"]}
            return _FakeResponse({
                "items": [
                    {"id": "mem_cat", "summary": "武松是橘猫。", "verbatim": "武松其实是橘猫。"},
                    {"id": "mem_name", "summary": "武松名字来自武松打虎。"},
                ],
                "missing_ids": [],
                "unavailable_ids": [],
            })
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(crc.httpx, "post", fake_post)

    block, ids = crc._memory_recall_for_message("武松是什么猫？")

    assert ids == ["mem_cat", "mem_name"]
    assert "[IO 长期记忆" in block
    assert "武松是橘猫。" in block
    assert "武松名字来自武松打虎。" in block
    assert "其他低相关记忆" not in block
    assert calls[0][1] == {"query": "武松是什么猫？", "limit": 50, "include_sensitive": False}


def test_route_a_process_messages_injects_memory_block_before_agent_call(monkeypatch):
    crc._seen_ids.clear()
    crc._seen_ids_order.clear()
    msg = {"id": "msg-memory-recall", "role": "user", "content": "武松是什么猫？", "ts": 12345.0}

    monkeypatch.setattr(
        crc,
        "_memory_recall_for_message",
        lambda content: ("[IO 长期记忆 · 供参考，用户当前说法优先]\n- 武松是橘猫。", ["mem_cat"]),
    )
    monkeypatch.setattr(crc, "_screen_context_for_message", lambda content: ("", [], []))
    monkeypatch.setattr(crc, "_resident_chat_runtime_v2_enabled", lambda: False)

    with patch.object(crc, "call_agent", return_value="它是橘猫。") as mock_agent, \
         patch.object(crc, "post_reply"):
        result_ts = crc._process_messages([msg])

    sent = mock_agent.call_args.args[0]
    assert result_ts == pytest.approx(12345.0)
    assert "武松是什么猫？" in sent
    assert "[IO 长期记忆" in sent
    assert "武松是橘猫。" in sent


def test_route_a_memory_recall_defaults_match_backend_context_layout():
    assert crc.ROUTE_A_MEMORY_RECALL_LIMIT == context_layout.LONG_TERM_MEMORY_INDEX_LIMIT_DEFAULT
    assert crc.ROUTE_A_MEMORY_RECALL_TOP_K == context_layout.LONG_TERM_MEMORY_TOP_K_DEFAULT
    assert crc.ROUTE_A_MEMORY_RECALL_MAX_CHARS == context_layout.LONG_TERM_MEMORY_MAX_CHARS_DEFAULT
