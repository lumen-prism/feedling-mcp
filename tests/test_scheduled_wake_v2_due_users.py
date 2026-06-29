import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import db
from proactive import scheduled_wake_v2 as sw


@pytest.fixture
def _clean_user_logs():
    with db.get_pool().connection() as conn:
        conn.execute("DELETE FROM user_logs WHERE stream = %s", (sw.SCHEDULED_WAKE_STREAM_V2,))
    yield


def _timer(user_id, timer_id, doc, *, ts=0.0):
    # Persist a raw scheduled-wake-v2 timer the same way the store does: one
    # user_logs row keyed by item_key=timer_id. Decoupled from the record
    # dataclass — due_user_ids only reads doc->>'due_at'/'status'/'claim_expires_at'.
    db.log_append(user_id, sw.SCHEDULED_WAKE_STREAM_V2, doc, ts=ts, item_key=timer_id)


def test_due_user_ids_returns_users_with_due_pending_timers(_clean_user_logs):
    now = 5_000.0
    _timer("u_due", "t1", {"due_at": now - 10, "status": "pending"})      # due
    _timer("u_future", "t2", {"due_at": now + 600, "status": "pending"})  # not yet
    out = sw.due_user_ids(now=now)
    assert "u_due" in out
    assert "u_future" not in out


def test_due_user_ids_includes_expired_claim(_clean_user_logs):
    now = 5_000.0
    _timer("u_claim", "t4", {"due_at": now - 10, "status": "claimed", "claim_expires_at": now - 5})
    assert "u_claim" in sw.due_user_ids(now=now)


def test_due_user_ids_excludes_fired_latest_row(_clean_user_logs):
    now = 5_000.0
    _timer("u_fired", "t3", {"due_at": now - 10, "status": "pending"})
    # supersede with a fired status on the latest seq (the store patches in place)
    sw.DBScheduledWakeStoreV2()._patch_guarded(
        "u_fired", "t3", {"status": "fired", "updated_at": now},
        statuses={"pending", "claimed"})
    assert "u_fired" not in sw.due_user_ids(now=now)
