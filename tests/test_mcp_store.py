from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
from core.store import UserStore  # noqa: E402


def _uid() -> str:
    return f"usr_mcp_{uuid.uuid4().hex[:12]}"


def _record(entry_id: str, *, body_ct: str = "ct", updated_at: str = "2026-07-03T00:00:00") -> dict:
    return {
        "id": entry_id,
        "owner_user_id": "usr_owner",
        "v": 1,
        "body_ct": body_ct,
        "nonce": "nonce",
        "K_user": "k-user",
        "K_enclave": "k-enclave",
        "visibility": "shared",
        "enclave_pk_fpr": "fpr",
        "updated_at": updated_at,
    }


def test_mcp_servers_upsert_replaces_by_id_and_persists():
    uid = _uid()
    store = UserStore(uid)

    saved = store.upsert_mcp_server(_record("mcp1", body_ct="one"))
    assert saved["id"] == "mcp1"
    assert [item["body_ct"] for item in store.mcp_servers] == ["one"]

    store.upsert_mcp_server(_record("mcp2", body_ct="two"))
    store.upsert_mcp_server(_record("mcp1", body_ct="one-edited"))
    assert {item["id"]: item["body_ct"] for item in store.mcp_servers} == {
        "mcp1": "one-edited",
        "mcp2": "two",
    }

    reloaded = UserStore(uid)
    assert {item["id"]: item["body_ct"] for item in reloaded.mcp_servers} == {
        "mcp1": "one-edited",
        "mcp2": "two",
    }


def test_mcp_servers_delete_returns_whether_row_existed_and_persists():
    uid = _uid()
    store = UserStore(uid)
    store.upsert_mcp_server(_record("mcp1"))
    store.upsert_mcp_server(_record("mcp2"))

    assert store.delete_mcp_server("mcp1") is True
    assert [item["id"] for item in store.mcp_servers] == ["mcp2"]
    assert store.delete_mcp_server("missing") is False

    reloaded = UserStore(uid)
    assert [item["id"] for item in reloaded.mcp_servers] == ["mcp2"]
