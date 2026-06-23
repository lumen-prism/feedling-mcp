"""Canonical memory action schema shared by IO runtime paths.

This module is intentionally pure: it only normalizes model-facing memory
actions into executor-facing actions. It does not call HTTP, DB, enclave, or
memory commit code.
"""

from __future__ import annotations

from datetime import date
from typing import Any


MEMORY_TYPES = {"fact", "event", "quote", "moment"}


def clean_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _payload(action: dict) -> dict:
    value = action.get("payload")
    return value if isinstance(value, dict) else {}


def _target(action: dict) -> dict:
    value = action.get("target")
    return value if isinstance(value, dict) else {}


def _memory_raw(payload: dict) -> dict:
    value = payload.get("memory")
    return value if isinstance(value, dict) else payload


def _memory_type(raw: dict) -> str:
    mem_type = str(raw.get("type") or raw.get("card_type") or "fact").strip().lower()
    return mem_type if mem_type in MEMORY_TYPES else "fact"


def _reason(action: dict, default: str) -> str:
    return clean_text(action.get("reason") or default, 500)


def _memory_id(action: dict, payload: dict, target: dict) -> str:
    return clean_text(
        target.get("memory_id")
        or target.get("id")
        or payload.get("memory_id")
        or payload.get("id"),
        160,
    )


def _add_action(action_type: str, action: dict, payload: dict) -> dict | None:
    raw = _memory_raw(payload)
    summary = clean_text(
        raw.get("summary") or raw.get("description") or raw.get("content") or raw.get("title"),
        2000,
    )
    title = clean_text(raw.get("title") or summary, 180)
    description = clean_text(raw.get("description") or raw.get("content") or summary, 2000)
    if not title or not description:
        return None
    source = "model_api_correction" if action_type == "memory.add_correction" else "hosted_runtime_state"
    return {
        "type": "memory.add_correction" if action_type == "memory.add_correction" else "memory.add",
        "memory": {
            "type": _memory_type(raw),
            "title": title,
            "description": description,
            "summary": summary,
            "occurred_at": clean_text(raw.get("occurred_at") or date.today().isoformat(), 80),
            "source": clean_text(raw.get("source") or source, 80),
            "context": clean_text(raw.get("context"), 1000),
            "her_quote": clean_text(raw.get("her_quote"), 1000),
            "verbatim": clean_text(raw.get("verbatim") or raw.get("her_quote"), 1000),
        },
        "reason": _reason(action, "Memory added from runtime action."),
        "capture_mode": "state",
    }


def _supersede_action(action: dict, payload: dict, target: dict) -> dict | None:
    memory_id = _memory_id(action, payload, target)
    if not memory_id:
        return None
    raw = _memory_raw(payload)
    summary = clean_text(
        raw.get("summary") or raw.get("description") or raw.get("content") or raw.get("title"),
        2000,
    )
    if not summary:
        return None
    memory_payload: dict[str, Any] = {
        "type": _memory_type(raw),
        "summary": summary,
        "verbatim": clean_text(raw.get("verbatim") or raw.get("her_quote"), 1000),
        "occurred_at": clean_text(raw.get("occurred_at") or date.today().isoformat(), 80),
        "source": clean_text(raw.get("source") or "hosted_runtime_state", 80),
    }
    context = clean_text(raw.get("context"), 1000)
    if context:
        memory_payload["context"] = context
    return {
        "type": "memory.supersede",
        "supersedes": memory_id,
        "memory": memory_payload,
        "reason": _reason(action, "Memory superseded from runtime action."),
        "capture_mode": "state",
    }


def _patch_action(action: dict, payload: dict, target: dict) -> dict | None:
    memory_id = _memory_id(action, payload, target)
    if not memory_id:
        return None
    raw_patch = payload.get("patch") if isinstance(payload.get("patch"), dict) else payload
    patch: dict[str, str] = {}
    for key, max_len in (
        ("title", 180),
        ("description", 2000),
        ("summary", 2000),
        ("her_quote", 1000),
        ("verbatim", 1000),
        ("context", 1000),
        ("follow_up", 1000),
        ("type", 80),
        ("occurred_at", 80),
    ):
        if key in raw_patch:
            patch[key] = clean_text(raw_patch.get(key), max_len)
    if not patch:
        description = clean_text(
            payload.get("description") or payload.get("content") or payload.get("summary"),
            2000,
        )
        if description:
            patch["description"] = description
    if not patch:
        return None
    return {
        "type": "memory.content_patch",
        "memory_id": memory_id,
        "patch": patch,
        "reason": _reason(action, "Memory updated from runtime action."),
    }


def _delete_action(action: dict, payload: dict, target: dict) -> dict | None:
    memory_id = _memory_id(action, payload, target)
    if not memory_id:
        return None
    return {
        "type": "memory.delete",
        "memory_id": memory_id,
        "reason": _reason(action, "Memory deleted from runtime action."),
    }


def canonical_memory_executor_action(action: dict) -> dict | None:
    if not isinstance(action, dict):
        return None
    action_type = str(action.get("type") or action.get("action") or "").strip().lower()
    payload = _payload(action)
    target = _target(action)
    if action_type in {"memory.create", "memory.add", "memory.add_correction"}:
        return _add_action(action_type, action, payload)
    if action_type in {"memory.supersede", "memory.replace", "memory.correct"}:
        return _supersede_action(action, payload, target)
    if action_type in {"memory.patch", "memory.content_patch"}:
        return _patch_action(action, payload, target)
    if action_type == "memory.delete":
        return _delete_action(action, payload, target)
    return None
