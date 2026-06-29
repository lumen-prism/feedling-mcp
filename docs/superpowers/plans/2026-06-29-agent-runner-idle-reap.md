# Agent-Runner Idle-Reap + Lazy-Spawn Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reclaim idle hosted-agent consumers ("reap") and lazily re-spawn them on the next unit of work, so the same CVM hosts more registered users without losing any messages.

**Architecture:** Add a `dormant` lease status. The supervisor reaps a live consumer whose `last_active_at` is older than a threshold and which is not mid-CLI-call, marking the lease `dormant`. Dormant users are skipped by the tick's spawn pass; they are re-woken through a single `_wake()` path driven by four sources — chat messages and perception frames (push, via the existing Postgres `feedling_wake` LISTEN/NOTIFY bus), and due scheduled-wakes and night-window dreams (pull, polled each tick). Reap/wake **decisions** live in a pure `reaper.py` module; all IO and orchestration live in the supervisor; the backend bumps `last_active_at` at its two write chokepoints. The feature is gated behind a default-off flag so the shipped default behavior is byte-for-byte today's (all consumers permanently resident).

**Tech Stack:** Python (backend Flask app + `agent_runtime` supervisor + `tools/chat_resident_consumer.py`), Postgres (`agent_runtime_instances` lease table, `user_logs` scheduled-wake stream, `user_blobs`), `psycopg` + `psycopg_pool`, the existing `core/wake_bus.py` LISTEN/NOTIFY bus, `pytest`.

## Global Constraints

- **消息零丢失 (hard invariant):** A message is durably in Postgres (`chat_messages`) before and independent of any consumer. Reap/wake change *when* a message is processed (cold-start adds seconds), never *whether* it is delivered. Push wake + per-tick backstop pull + reply-claim dedup are the three guarantees. No task may make message delivery depend on a consumer being resident at send time.
- **失败偏向保命 (fail-toward-safe):** Any uncertainty or exception in a reap decision → do NOT reap (keep resident). Any wake failure → retried next tick. A reaper that cannot read `last_active_at` treats it as "unknown" and does not reap.
- **默认关、暗发 (default-off):** `AGENT_IDLE_REAP_ENABLED` defaults to `false`. With it off, no lease is ever marked `dormant`, the spawn pass and wedge guard behave exactly as today, and the supervisor never starts the wake listener. Shipping this plan with the flag off must be a no-op.
- **proactive 不被延后:** Capture/dream with no underlying activity is a no-op; when there IS activity (frame/message/scheduled timer/night window) the matching trigger wakes the user promptly. Scheduled-wake and dream wakes must not be silently dropped for dormant users.
- **E2E 不变:** The server never sees plaintext; provider keys are decrypted JIT in the enclave. No task reads or logs message bodies. A woken consumer resumes session/memory from its persistent home.
- **多 worker 安全:** Dormant state is authoritative in the DB lease row. Every wake goes through `leases.acquire`, which is atomic, so at most one supervisor (across `-w N` and multiple supervisors) spawns a given user's consumer.
- **Idle threshold:** `AGENT_IDLE_THRESHOLD_SEC` default `1080` (18 min) — chosen so it is much larger than a single CLI turn (the consumer's `subprocess.run(..., timeout=120)`); a turn can never span the threshold, which is what protects an in-flight chat turn from being reaped.
- **Channels:** Use the EXISTING wake-bus channels `"chat"` and `"frames"`. Do not invent a `"perception"` channel — it does not exist in `core/wake_bus.py::_STORE_CHANNELS` (`{"chat","proactive","frames","blob"}`).

---

## File Structure

**New files:**
- `backend/agent_runtime/reaper.py` — pure reap/wake decision functions, zero IO. Unit-tested in isolation.
- `tests/test_agent_runtime_reaper.py` — pure unit tests for `reaper.py`.
- `tests/test_agent_runtime_idle_reap.py` — DB-backed supervisor integration tests for reap + wake (matches the `test_agent_runtime_supervisor.py` fake-process style).

**Modified files:**
- `backend/core/util.py` — two config-flag helpers.
- `backend/agent_runtime/leases.py` — `mark_dormant()` + `list_dormant()`.
- `backend/db.py` — `bump_agent_last_active()`.
- `backend/proactive/scheduled_wake_v2.py` — `due_user_ids()` cross-user query.
- `backend/core/store.py` — bump `last_active_at` in `append_chat`.
- `backend/screen/frames.py` — bump `last_active_at` in `_save_frame_envelope`.
- `backend/agent_runtime/spawners.py` — busy-sentinel path helper + `AGENT_HOME` in `consumer_env`.
- `tools/chat_resident_consumer.py` — write/remove the busy sentinel around `call_agent_cli`.
- `backend/agent_runtime/supervisor.py` — reap pass, `_wake`, dormant-skip, wake listener, `wake_pass`, dream pull, main-loop wiring.
- Test files: `tests/test_agent_runtime_leases.py`, `tests/test_scheduled_wake_v2.py` (or nearest existing), `tests/test_core_store_*` / `tests/test_screen_frames_*` (nearest existing), `tests/test_agent_runtime_spawners.py`.

**No change needed (verified):** the `/v1/model_api/chat/send` wedge guard (`backend/hosted/chat_routes.py` + `agent_runtime_cutover.check_supervisor_live`) only reads the **global** supervisor heartbeat (`db.read_supervisor_heartbeat` → host_all/gateway/freshness). It never inspects a user's per-row `status`, so a dormant user's send already passes the guard, lands in `chat_messages`, and triggers a wake. No wedge change is in scope.

---

## Task 1: Config flags

**Files:**
- Modify: `backend/core/util.py` (after `runtime_v2_default_on`, ~line 28)
- Test: `tests/test_core_util_idle_reap.py` (create)

**Interfaces:**
- Produces: `util.agent_idle_reap_enabled() -> bool` (default `False`); `util.agent_idle_threshold_sec() -> int` (default `1080`, floored at `1`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_core_util_idle_reap.py`:

```python
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from core import util


def test_idle_reap_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_IDLE_REAP_ENABLED", raising=False)
    assert util.agent_idle_reap_enabled() is False


def test_idle_reap_enabled_truthy(monkeypatch):
    monkeypatch.setenv("AGENT_IDLE_REAP_ENABLED", "true")
    assert util.agent_idle_reap_enabled() is True
    monkeypatch.setenv("AGENT_IDLE_REAP_ENABLED", "1")
    assert util.agent_idle_reap_enabled() is True


def test_idle_threshold_default(monkeypatch):
    monkeypatch.delenv("AGENT_IDLE_THRESHOLD_SEC", raising=False)
    assert util.agent_idle_threshold_sec() == 1080


def test_idle_threshold_override(monkeypatch):
    monkeypatch.setenv("AGENT_IDLE_THRESHOLD_SEC", "300")
    assert util.agent_idle_threshold_sec() == 300


def test_idle_threshold_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("AGENT_IDLE_THRESHOLD_SEC", "not-a-number")
    assert util.agent_idle_threshold_sec() == 1080
    monkeypatch.setenv("AGENT_IDLE_THRESHOLD_SEC", "0")
    assert util.agent_idle_threshold_sec() == 1  # floored at 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_core_util_idle_reap.py -v`
Expected: FAIL with `AttributeError: module 'core.util' has no attribute 'agent_idle_reap_enabled'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/core/util.py`, after `runtime_v2_default_on()` (line 28):

```python
def agent_idle_reap_enabled() -> bool:
    """Feature flag for agent-runner idle-reap + lazy-spawn. OFF by default so
    prod keeps every enabled user's consumer permanently resident."""
    return _env_flag_enabled("AGENT_IDLE_REAP_ENABLED", "false")


def agent_idle_threshold_sec() -> int:
    """Seconds a consumer may be idle (no chat/frame/wake activity) before it is
    eligible for reaping. Default 18 min — far larger than one CLI turn
    (120s timeout) so an in-flight chat turn can never cross the threshold."""
    try:
        return max(1, int(os.environ.get("AGENT_IDLE_THRESHOLD_SEC", "1080")))
    except (TypeError, ValueError):
        return 1080
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_core_util_idle_reap.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/core/util.py tests/test_core_util_idle_reap.py
git commit -m "feat(agent-runtime): idle-reap config flags"
```

---

## Task 2: `reaper.py` — pure reap/wake decisions

**Files:**
- Create: `backend/agent_runtime/reaper.py`
- Test: `tests/test_agent_runtime_reaper.py` (create)

**Interfaces:**
- Produces:
  - `reaper.should_reap(*, last_active_at: float, now: float, idle_sec: float, in_flight: bool) -> bool`
  - `reaper.wakes_due(*, dormant_uids: set[str], scheduled_due_uids: set[str], backstop_uids: set[str], push_uids: set[str]) -> set[str]`
  - `reaper.dream_wake_due(*, in_window: bool, last_active_at: float, last_dream_wake_at: float) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_runtime_reaper.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from agent_runtime import reaper


# ---- should_reap ----

def test_should_reap_when_idle_past_threshold_and_not_in_flight():
    assert reaper.should_reap(last_active_at=1000.0, now=1000.0 + 1081, idle_sec=1080, in_flight=False) is True


def test_should_not_reap_within_threshold():
    assert reaper.should_reap(last_active_at=1000.0, now=1000.0 + 100, idle_sec=1080, in_flight=False) is False


def test_should_not_reap_at_exact_threshold():
    # strictly greater-than: at exactly idle_sec we are not yet past it
    assert reaper.should_reap(last_active_at=1000.0, now=1000.0 + 1080, idle_sec=1080, in_flight=False) is False


def test_should_not_reap_when_in_flight():
    assert reaper.should_reap(last_active_at=1000.0, now=1000.0 + 99999, idle_sec=1080, in_flight=True) is False


def test_should_not_reap_when_last_active_unknown():
    # last_active_at <= 0 means "unknown" -> fail toward safe, never reap
    assert reaper.should_reap(last_active_at=0.0, now=1e9, idle_sec=1080, in_flight=False) is False
    assert reaper.should_reap(last_active_at=-5.0, now=1e9, idle_sec=1080, in_flight=False) is False


# ---- wakes_due ----

def test_wakes_due_unions_sources_then_intersects_dormant():
    out = reaper.wakes_due(
        dormant_uids={"a", "b", "c"},
        scheduled_due_uids={"a", "z"},   # z not dormant -> dropped
        backstop_uids={"b"},
        push_uids={"c", "d"},            # d not dormant -> dropped
    )
    assert out == {"a", "b", "c"}


def test_wakes_due_empty_when_no_dormant():
    out = reaper.wakes_due(
        dormant_uids=set(), scheduled_due_uids={"a"}, backstop_uids={"b"}, push_uids={"c"})
    assert out == set()


# ---- dream_wake_due ----

def test_dream_wake_due_when_in_window_and_activity_since_last_dream():
    assert reaper.dream_wake_due(in_window=True, last_active_at=2000.0, last_dream_wake_at=1000.0) is True


def test_dream_no_wake_outside_window():
    assert reaper.dream_wake_due(in_window=False, last_active_at=2000.0, last_dream_wake_at=0.0) is False


def test_dream_no_wake_when_no_new_activity_since_last_dream():
    assert reaper.dream_wake_due(in_window=True, last_active_at=1000.0, last_dream_wake_at=1000.0) is False
    assert reaper.dream_wake_due(in_window=True, last_active_at=900.0, last_dream_wake_at=1000.0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_reaper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_runtime.reaper'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/agent_runtime/reaper.py`:

```python
"""Pure reap/wake decisions for the agent-runner idle-reap feature.

No IO. The supervisor reads the lease table + filesystem, passes the facts here,
and acts on the verdict. Keeping the logic pure makes the threshold/guard rules
exhaustively unit-testable without a database or processes.
"""

from __future__ import annotations


def should_reap(*, last_active_at: float, now: float, idle_sec: float, in_flight: bool) -> bool:
    """Whether a live consumer should be reaped to dormant.

    Reap iff it is NOT mid-CLI-call and has been idle strictly longer than the
    threshold. ``last_active_at <= 0`` means "unknown" — fail toward safe and
    never reap (a freshly spawned consumer has its clock stamped at spawn, so a
    real running consumer never reports 0 here)."""
    if in_flight:
        return False
    if last_active_at <= 0:
        return False
    return (now - last_active_at) > idle_sec


def wakes_due(*, dormant_uids: set[str], scheduled_due_uids: set[str],
              backstop_uids: set[str], push_uids: set[str]) -> set[str]:
    """The dormant users to wake this tick: the union of the non-dream wake
    sources (due scheduled timers, missed-notify backstop, pushed chat/frame
    notifies), intersected with the set that is actually dormant. Over-inclusion
    is harmless (a woken consumer with nothing to do idles again); the
    intersection drops sources that name a user who is already running."""
    return (scheduled_due_uids | backstop_uids | push_uids) & dormant_uids


def dream_wake_due(*, in_window: bool, last_active_at: float, last_dream_wake_at: float) -> bool:
    """Whether to wake a dormant user for a night-window dream. Wake iff we are in
    the user's night window AND there has been activity since we last woke them to
    dream (so a user with no new moments is not re-woken every tick). The woken
    consumer does the authoritative undigested-moments dedup via its dream_key."""
    return in_window and (last_active_at > last_dream_wake_at)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_reaper.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/agent_runtime/reaper.py tests/test_agent_runtime_reaper.py
git commit -m "feat(agent-runtime): pure reaper decision module"
```

---

## Task 3: `leases.mark_dormant` + `leases.list_dormant`

**Files:**
- Modify: `backend/agent_runtime/leases.py` (add after `release`, ~line 143)
- Test: `tests/test_agent_runtime_leases.py` (append)

**Interfaces:**
- Consumes: `agent_runtime_instances` columns `status, pid, lease_owner, lease_expires_at, last_active_at, last_heartbeat_at, updated_at` (existing).
- Produces:
  - `leases.mark_dormant(user_id: str, lease_owner: str, *, now: float | None = None) -> None` — sets `status='dormant'`, clears `pid/lease_owner/lease_expires_at`, stamps `last_heartbeat_at=now` (the dormancy moment), **leaves `last_active_at` untouched** (it is the real last-activity clock).
  - `leases.list_dormant() -> list[dict[str, Any]]` — all `status='dormant'` rows as dicts.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_runtime_leases.py` (it already has the `_clean_table` autouse fixture and `T0`):

```python
def test_mark_dormant_parks_the_lease():
    leases.acquire("u_d", driver="claude", runtime_home="/d/u_d",
                   lease_owner="sup_A", ttl=300.0, now=T0)
    leases.renew("u_d", "sup_A", ttl=300.0, pid=4242, status="running", now=T0)
    leases.mark_dormant("u_d", "sup_A", now=T0 + 50)

    row = leases.get("u_d")
    assert row["status"] == "dormant"
    assert row["pid"] is None
    assert row["lease_owner"] is None
    assert row["lease_expires_at"] is None
    # dormancy moment recorded on last_heartbeat_at; last_active_at left alone
    assert int(row["last_heartbeat_at"].timestamp()) == int(T0 + 50)


def test_list_dormant_returns_only_dormant_rows():
    leases.acquire("u_run", driver="claude", runtime_home="/d/u_run",
                   lease_owner="sup_A", ttl=300.0, now=T0)
    leases.renew("u_run", "sup_A", ttl=300.0, pid=1, status="running", now=T0)
    leases.acquire("u_dorm", driver="claude", runtime_home="/d/u_dorm",
                   lease_owner="sup_A", ttl=300.0, now=T0)
    leases.mark_dormant("u_dorm", "sup_A", now=T0)

    uids = {r["user_id"] for r in leases.list_dormant()}
    assert uids == {"u_dorm"}


def test_dormant_lease_is_reacquirable():
    # A parked lease (lease_expires_at NULL) must be acquirable by _wake.
    leases.acquire("u_w", driver="claude", runtime_home="/d/u_w",
                   lease_owner="sup_A", ttl=300.0, now=T0)
    leases.mark_dormant("u_w", "sup_A", now=T0)
    assert leases.acquire("u_w", driver="claude", runtime_home="/d/u_w",
                          lease_owner="sup_A", ttl=300.0, now=T0 + 1) is True
    assert leases.get("u_w")["status"] == "starting"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_leases.py -k "dormant" -v`
Expected: FAIL with `AttributeError: module 'leases' has no attribute 'mark_dormant'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/agent_runtime/leases.py`, after `release` (line 143):

```python
def mark_dormant(user_id: str, lease_owner: str, *, now: float | None = None) -> None:
    """Park a live lease as ``dormant`` (idle-reaped). Like ``release`` it clears
    the lease so another supervisor can re-acquire on wake, but the status label
    ``dormant`` (vs ``idle``) tells the tick's spawn pass NOT to auto-respawn —
    this user is parked until a trigger wakes it. ``last_heartbeat_at`` records the
    dormancy moment; ``last_active_at`` is deliberately left as the real
    last-activity time so a later activity bump (chat/frame) makes
    ``last_active_at > last_heartbeat_at``, which the missed-notify backstop keys
    off. Only the current owner may park it."""
    sql = """
        UPDATE agent_runtime_instances SET
            status = 'dormant', lease_owner = NULL, lease_expires_at = NULL,
            pid = NULL, last_heartbeat_at = to_timestamp(%s), updated_at = now()
        WHERE user_id = %s AND lease_owner = %s
    """
    with db.get_pool().connection() as conn:
        conn.execute(sql, (_now(now), user_id, lease_owner))


def list_dormant() -> list[dict[str, Any]]:
    """All parked (``status='dormant'``) rows. Used by the supervisor to skip them
    in the spawn pass and to evaluate wake triggers."""
    sql = "SELECT * FROM agent_runtime_instances WHERE status = 'dormant'"
    with db.get_pool().connection() as conn:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_leases.py -k "dormant" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/agent_runtime/leases.py tests/test_agent_runtime_leases.py
git commit -m "feat(agent-runtime): lease mark_dormant + list_dormant"
```

---

## Task 4: `db.bump_agent_last_active`

**Files:**
- Modify: `backend/db.py` (add near the other `agent_runtime_instances` helpers, after `list_agent_runtime_enabled_users`, ~line 740)
- Test: `tests/test_agent_runtime_leases.py` (append — same DB-backed file)

**Interfaces:**
- Produces: `db.bump_agent_last_active(user_id: str, *, now: float | None = None) -> None` — sets `last_active_at` for the user's row regardless of lease owner. `now` defaults to `time.time()` (the backend write path); the supervisor passes its injected `self._now()` so reap math stays on one clock (critical for deterministic tests, where `self._now()` is a fake `T0`). Best-effort: a missing row or DB error is swallowed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_runtime_leases.py`:

```python
def test_bump_agent_last_active_advances_clock():
    leases.acquire("u_b", driver="claude", runtime_home="/d/u_b",
                   lease_owner="sup_A", ttl=300.0, now=T0)
    leases.mark_dormant("u_b", "sup_A", now=T0)        # last_heartbeat_at = T0
    before = leases.get("u_b").get("last_active_at")

    db.bump_agent_last_active("u_b")

    row = leases.get("u_b")
    assert row["last_active_at"] is not None
    if before is not None:
        assert row["last_active_at"] >= before
    # bump must make last_active_at strictly newer than the dormancy heartbeat,
    # which is exactly the missed-notify backstop signal.
    assert row["last_active_at"] > row["last_heartbeat_at"]


def test_bump_agent_last_active_missing_row_is_noop():
    # No row for this user -> must not raise.
    db.bump_agent_last_active("u_does_not_exist")
```

(Confirm `tests/test_agent_runtime_leases.py` imports `db` at top — it does, via the `sys.path` + `import db` preamble shared with the other agent-runtime tests. If not, add `import db`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_leases.py -k "bump_agent_last_active" -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'bump_agent_last_active'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/db.py`, after `list_agent_runtime_enabled_users` (line 739):

```python
def bump_agent_last_active(user_id: str, *, now: float | None = None) -> None:
    """Stamp ``agent_runtime_instances.last_active_at`` for a user. Called from the
    backend write chokepoints (chat append, frame ingest) so the supervisor's
    idle-reap clock reflects real underlying activity. ``now`` defaults to
    ``time.time()``; the supervisor passes its injected clock so reap arithmetic
    stays on a single clock (deterministic under a fake test clock). Best-effort
    and owner-agnostic: the backend is not the lease owner, and a user with no
    hosted row (not enabled) simply has nothing to bump. Never raises into the
    request path — a missed bump only risks an over-eager reap, which the wake
    triggers immediately recover from."""
    ts = time.time() if now is None else now
    try:
        with get_pool().connection() as conn:
            conn.execute(
                "UPDATE agent_runtime_instances SET last_active_at = to_timestamp(%s) WHERE user_id = %s",
                (ts, user_id),
            )
    except Exception as e:  # noqa: BLE001
        log.error("[db] bump_agent_last_active(%s) failed: %s", user_id, e)
```

(`backend/db.py` imports `time` at module top — confirm before relying on it; it is used throughout for timestamps.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_leases.py -k "bump_agent_last_active" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/db.py tests/test_agent_runtime_leases.py
git commit -m "feat(agent-runtime): db.bump_agent_last_active activity stamp"
```

---

## Task 5: `scheduled_wake_v2.due_user_ids` cross-user query

**Files:**
- Modify: `backend/proactive/scheduled_wake_v2.py` (add a module-level function near the DB store, after `DBScheduledWakeStoreV2`)
- Test: `tests/test_scheduled_wake_v2.py` (append; if no such file exists, create `tests/test_scheduled_wake_v2_due_users.py` with the preamble shown)

**Interfaces:**
- Consumes: `user_logs` rows with `stream = SCHEDULED_WAKE_STREAM_V2`, `doc` JSON with `due_at`/`status`/`claim_expires_at`, latest `seq` per `(user_id, item_key)`.
- Produces: `scheduled_wake_v2.due_user_ids(*, now: float) -> set[str]` — user_ids whose latest timer row is due (pending, or claimed-but-expired). Errs toward inclusion; returns `set()` on DB error.

- [ ] **Step 1: Write the failing test**

Append to the scheduled-wake test file (preamble for a new file):

```python
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
```

(Confirm `db.log_append(user_id, stream, doc, ts=..., item_key=...)` and `DBScheduledWakeStoreV2._patch_guarded(user_id, timer_id, patch, statuses=...)` signatures against `backend/proactive/scheduled_wake_v2.py` — both are used verbatim by the existing store; the test mirrors them.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_scheduled_wake_v2_due_users.py -v`
Expected: FAIL with `AttributeError: module 'proactive.scheduled_wake_v2' has no attribute 'due_user_ids'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/proactive/scheduled_wake_v2.py`, add a module logger if absent (`import logging` + `log = logging.getLogger("feedling.scheduled_wake_v2")`), then add after `DBScheduledWakeStoreV2`:

```python
def due_user_ids(*, now: float) -> set[str]:
    """Cross-user: every user whose LATEST scheduled-wake-v2 timer is due — i.e.
    pending with ``due_at <= now``, or claimed with an expired claim. The
    supervisor uses this to wake dormant users for an imminent scheduled wake.

    Correctness leans toward over-inclusion: a woken consumer that finds nothing
    actually due simply idles again, whereas under-inclusion would drop a wake.
    DISTINCT ON (user_id, item_key) ... ORDER BY seq DESC takes only the current
    row per timer, so a fired/canceled timer's superseded 'pending' row can't
    resurrect a wake. Returns an empty set on any DB error (fail toward not
    over-waking; the per-tick backstop and push paths remain)."""
    sql = (
        "SELECT DISTINCT user_id FROM ("
        "  SELECT DISTINCT ON (user_id, item_key) user_id, doc"
        "  FROM user_logs WHERE stream = %s"
        "  ORDER BY user_id, item_key, seq DESC"
        ") latest "
        "WHERE COALESCE(NULLIF(doc->>'due_at','')::float8, 0) > 0 "
        "  AND COALESCE(NULLIF(doc->>'due_at','')::float8, 0) <= %s "
        "  AND (doc->>'status' = 'pending' OR (doc->>'status' = 'claimed' "
        "       AND COALESCE(NULLIF(doc->>'claim_expires_at','')::float8, 0) <= %s))"
    )
    try:
        with db.get_pool().connection() as conn:
            rows = conn.execute(sql, (SCHEDULED_WAKE_STREAM_V2, now, now)).fetchall()
        return {r[0] for r in rows}
    except Exception as e:  # noqa: BLE001
        log.error("[scheduled_wake_v2] due_user_ids failed: %s", e)
        return set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_scheduled_wake_v2_due_users.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/proactive/scheduled_wake_v2.py tests/test_scheduled_wake_v2_due_users.py
git commit -m "feat(agent-runtime): cross-user due_user_ids for scheduled wakes"
```

---

## Task 6: Activity-bump hooks (chat append + frame ingest)

**Files:**
- Modify: `backend/core/store.py` (`append_chat`, after the `wake_bus.notify("chat", …)` at line 423)
- Modify: `backend/screen/frames.py` (`_save_frame_envelope`, after `db.frame_upsert(...)` at line 56)
- Test: `tests/test_store_activity_bump.py` (create)

**Interfaces:**
- Consumes: `db.bump_agent_last_active(user_id)` (Task 4).
- Produces: side-effect only — every genuine chat append and every frame ingest stamps the user's `last_active_at`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_store_activity_bump.py`:

```python
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import db
from core.store import UserStore


def test_append_chat_bumps_last_active(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "bump_agent_last_active", lambda uid: calls.append(uid))
    store = UserStore("u_chat")
    env = {"id": "m1", "v": 1, "body_ct": "x", "nonce": "n", "K_user": "k"}
    store.append_chat("user", "app", env)
    assert "u_chat" in calls


def test_save_frame_bumps_last_active(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "bump_agent_last_active", lambda uid: calls.append(uid))
    monkeypatch.setattr(db, "frame_upsert", lambda *a, **k: True)
    from screen import frames
    store = UserStore("u_frame")
    env = {"v": 1, "id": "f1", "body_ct": "x"}
    frames._save_frame_envelope(store, {"ts": 1.0, "envelope": env}, env)
    assert "u_frame" in calls
```

(`UserStore("u_chat")` constructs against the test DB provisioned by `conftest.py`. If `UserStore.__init__` requires extra setup in this codebase, mirror an existing `tests/` usage of `UserStore` instead.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_store_activity_bump.py -v`
Expected: FAIL — `bump_agent_last_active` is never called (asserts fail).

- [ ] **Step 3: Write minimal implementation**

In `backend/core/store.py`, immediately after line 423 (`wake_bus.notify("chat", self.user_id)`):

```python
        # Idle-reap activity clock: a genuine chat write keeps the user's hosted
        # consumer from being reaped mid-conversation, and re-arms a dormant user
        # for the missed-notify backstop. Best-effort (never raises here).
        db.bump_agent_last_active(self.user_id)
```

In `backend/screen/frames.py`, immediately after line 56 (`db.frame_upsert(store.user_id, item_id, ts, env)`):

```python
    # Idle-reap activity clock: perception frames are underlying activity too, so
    # a perceiving-but-not-chatting user is not reaped and a dormant user re-arms
    # the backstop. The "frames" wake broadcast (other workers / the supervisor's
    # listener) is emitted by store._persist_frames_meta below.
    db.bump_agent_last_active(store.user_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_store_activity_bump.py -v`
Expected: PASS (2 passed). Also run the existing store/frames suites to confirm no regression:
`cd backend && python -m pytest ../tests/ -k "store or frame" -q`

- [ ] **Step 5: Commit**

```bash
git add backend/core/store.py backend/screen/frames.py tests/test_store_activity_bump.py
git commit -m "feat(agent-runtime): bump last_active_at on chat + frame writes"
```

---

## Task 7: Busy sentinel (in-flight signal)

**Files:**
- Modify: `backend/agent_runtime/spawners.py` (add sentinel helper near `runtime_token_path` ~line 68; add `AGENT_HOME` in `consumer_env` ~line 342)
- Modify: `tools/chat_resident_consumer.py` (wrap `call_agent_cli` body ~line 2412; startup cleanup)
- Test: `tests/test_agent_runtime_spawners.py` (append)

**Interfaces:**
- Produces:
  - `spawners.BUSY_SENTINEL_NAME = ".agent-busy"`
  - `spawners.busy_sentinel_path(home: str) -> str` → `f"{home}/.agent-busy"`
  - `consumer_env(...)` now also sets `env["AGENT_HOME"] = home`.
- Consumed by: Task 8's reap pass (`in_flight = os.path.exists(spawners.busy_sentinel_path(home))`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_runtime_spawners.py`:

```python
def test_busy_sentinel_path():
    from agent_runtime import spawners
    assert spawners.busy_sentinel_path("/agent-data/users/u_1") == "/agent-data/users/u_1/.agent-busy"


def test_consumer_env_exposes_agent_home():
    from agent_runtime import spawners
    env = spawners.consumer_env({}, {"api_key": "k", "driver": "claude"},
                                user_id="u_1", home="/agent-data/users/u_1")
    assert env["AGENT_HOME"] == "/agent-data/users/u_1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_spawners.py -k "busy_sentinel or agent_home" -v`
Expected: FAIL — `busy_sentinel_path` missing / `AGENT_HOME` not in env.

- [ ] **Step 3: Write minimal implementation**

In `backend/agent_runtime/spawners.py`, near `runtime_token_path` (line 68):

```python
BUSY_SENTINEL_NAME = ".agent-busy"


def busy_sentinel_path(home: str) -> str:
    """Path of the in-flight marker the consumer creates while a CLI turn is
    running. The supervisor treats its presence as ``in_flight`` and will not reap
    that consumer (see agent_runtime.reaper.should_reap)."""
    return f"{home}/{BUSY_SENTINEL_NAME}"
```

In `consumer_env` (after `env["CONSUMER_ID"] = ...`, ~line 357):

```python
    env["AGENT_HOME"] = home  # consumer writes the busy sentinel here
```

In `tools/chat_resident_consumer.py`: near the other module-level env reads (~line 197), add:

```python
AGENT_HOME = os.environ.get("AGENT_HOME", "")
_BUSY_SENTINEL = os.path.join(AGENT_HOME, ".agent-busy") if AGENT_HOME else ""


def _set_busy(active: bool) -> None:
    """Create/remove the in-flight marker the supervisor reads to avoid reaping a
    consumer mid-CLI-turn. Best-effort; no-op when AGENT_HOME is unset (non-hosted
    deployments)."""
    if not _BUSY_SENTINEL:
        return
    try:
        if active:
            with open(_BUSY_SENTINEL, "w") as f:
                f.write(str(os.getpid()))
        else:
            try:
                os.unlink(_BUSY_SENTINEL)
            except FileNotFoundError:
                pass
    except OSError:
        pass
```

Wrap the body of `call_agent_cli` (line 2412) so the marker brackets the subprocess. Do NOT rewrite the body — indent the **entire existing function body** one level under a `try:`, add `_set_busy(True)` before it and a `finally: _set_busy(False)` after. The existing `return`/`raise` statements work unchanged because `finally` always runs:

```python
def call_agent_cli(message: str, image_paths: list[str] | None = None) -> Any:
    _set_busy(True)
    try:
        # <<< the existing call_agent_cli body, verbatim and unchanged, indented
        #     one level — including `result = subprocess.run(cmd, ..., timeout=120)`
        #     and every existing `return` >>>
    finally:
        _set_busy(False)
```

At consumer startup (in `main()`/entrypoint, before the poll loop begins), clear any stale marker left by a crashed predecessor that reused this home:

```python
    _set_busy(False)  # clear stale in-flight marker from a prior crash
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_spawners.py -k "busy_sentinel or agent_home" -v`
Expected: PASS (2 passed). Then syntax-check the consumer:
`python -c "import ast; ast.parse(open('tools/chat_resident_consumer.py').read())"` → no output (valid).

- [ ] **Step 5: Commit**

```bash
git add backend/agent_runtime/spawners.py tools/chat_resident_consumer.py tests/test_agent_runtime_spawners.py
git commit -m "feat(agent-runtime): busy sentinel for in-flight reap guard"
```

---

## Task 8: Supervisor core — reap pass, `_wake`, dormant-skip

**Files:**
- Modify: `backend/agent_runtime/supervisor.py` (`Supervisor.__init__` ~line 69; `tick` ~line 102; add `_wake`)
- Test: `tests/test_agent_runtime_idle_reap.py` (create — DB-backed, fake-process style)

**Interfaces:**
- Consumes: `reaper.should_reap`, `leases.mark_dormant`, `leases.list_dormant`, `leases.get`, `db.bump_agent_last_active`, `spawners.busy_sentinel_path`.
- Produces (new on `Supervisor`):
  - `__init__(..., idle_reap_enabled: bool = False, idle_threshold_sec: float = 1080.0)` (added kwargs, defaulted so existing callers/tests are unchanged).
  - `_wake(entry: dict) -> None` — the single spawn path for a dormant user (acquire→spawn→token→renew→bump→record). No-op if `acquire` loses the race.
  - `tick(roster, *, dormant_uids: set[str] | None = None)` — adds an idle-reap pass (only when enabled) and skips dormant users in the spawn pass.
  - helper `_epoch(dt) -> float` (module-level) converting a timestamptz/None to epoch seconds.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_runtime_idle_reap.py`:

```python
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import db
from agent_runtime import leases
from agent_runtime import supervisor as supervisor_mod

T0 = 2_000_000.0


@pytest.fixture(autouse=True)
def _clean_table():
    with db.get_pool().connection() as conn:
        conn.execute("TRUNCATE agent_runtime_instances")
    yield


class FakeProcTable:
    def __init__(self):
        self.spawned = []
        self.alive = {}
        self.killed = []
        self._next = 1000

    def spawn(self, entry, user_id, home):
        self._next += 1
        pid = self._next
        self.alive[pid] = True
        self.spawned.append((entry, user_id, home))
        return pid

    def is_alive(self, pid):
        return self.alive.get(pid, False)

    def kill(self, pid):
        self.killed.append(pid)
        self.alive[pid] = False


def _roster(*uids):
    return [{"user_id": u, "driver": "claude", "api_key": "k"} for u in uids]


def _sup(procs, *, clock, reap=True, threshold=1080.0, owner="sup_A"):
    # lease_ttl huge so a fake-clock time jump never expires the lease — these
    # tests exercise reap logic, not lease TTL (production renews every ~15s tick).
    return supervisor_mod.Supervisor(
        owner=owner, lease_ttl=1e9, data_root="/agent-data",
        spawn_fn=procs.spawn, alive_fn=procs.is_alive, kill_fn=procs.kill,
        now=clock, idle_reap_enabled=reap, idle_threshold_sec=threshold,
    )


def test_idle_consumer_is_reaped_to_dormant():
    procs = FakeProcTable()
    t = {"v": T0}
    sup = _sup(procs, clock=lambda: t["v"])
    sup.tick(_roster("u_1"))                      # spawn; last_active stamped at T0
    assert leases.get("u_1")["status"] == "running"

    t["v"] = T0 + 1081                            # idle past threshold
    sup.tick(_roster("u_1"))                      # reap pass fires

    assert procs.killed == [1001]
    assert leases.get("u_1")["status"] == "dormant"
    assert "u_1" not in sup.children


def test_dormant_user_is_not_respawned_by_tick():
    procs = FakeProcTable()
    t = {"v": T0}
    sup = _sup(procs, clock=lambda: t["v"])
    sup.tick(_roster("u_1"))
    t["v"] = T0 + 1081
    sup.tick(_roster("u_1"))                      # -> dormant
    spawned_before = len(procs.spawned)

    sup.tick(_roster("u_1"))                      # spawn pass must skip dormant
    assert len(procs.spawned) == spawned_before
    assert leases.get("u_1")["status"] == "dormant"


def test_in_flight_consumer_is_not_reaped(tmp_path, monkeypatch):
    procs = FakeProcTable()
    t = {"v": T0}
    sup = _sup(procs, clock=lambda: t["v"])
    # Point the home at tmp and drop a busy sentinel so in_flight=True.
    monkeypatch.setattr(sup, "_home", lambda uid: str(tmp_path))
    sup.tick(_roster("u_1"))
    from agent_runtime import spawners
    Path(spawners.busy_sentinel_path(str(tmp_path))).write_text("1234")

    t["v"] = T0 + 99999
    sup.tick(_roster("u_1"))
    assert procs.killed == []
    assert leases.get("u_1")["status"] == "running"


def test_wake_respawns_a_dormant_user():
    procs = FakeProcTable()
    t = {"v": T0}
    sup = _sup(procs, clock=lambda: t["v"])
    sup.tick(_roster("u_1"))
    t["v"] = T0 + 1081
    sup.tick(_roster("u_1"))                      # dormant
    assert leases.get("u_1")["status"] == "dormant"

    sup._wake({"user_id": "u_1", "driver": "claude", "api_key": "k"})
    assert leases.get("u_1")["status"] == "running"
    assert "u_1" in sup.children


def test_reap_disabled_keeps_consumer_resident_forever():
    procs = FakeProcTable()
    t = {"v": T0}
    sup = _sup(procs, clock=lambda: t["v"], reap=False)
    sup.tick(_roster("u_1"))
    t["v"] = T0 + 999999
    sup.tick(_roster("u_1"))
    assert procs.killed == []
    assert leases.get("u_1")["status"] == "running"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_idle_reap.py -v`
Expected: FAIL — `Supervisor.__init__` rejects `idle_reap_enabled` (unexpected kwarg) / no reap behavior.

- [ ] **Step 3: Write minimal implementation**

In `backend/agent_runtime/supervisor.py`:

(a) Add a module-level epoch helper (near the other module helpers):

```python
def _epoch(dt) -> float:
    """A timestamptz row value (datetime) or None -> epoch seconds (0.0 if unset)."""
    if dt is None:
        return 0.0
    try:
        return dt.timestamp()
    except AttributeError:
        try:
            return float(dt)
        except (TypeError, ValueError):
            return 0.0
```

(b) Extend `__init__` (line 69) with two kwargs + store them:

```python
    def __init__(
        self,
        *,
        owner: str,
        lease_ttl: float,
        data_root: str,
        spawn_fn,
        alive_fn,
        kill_fn=spawners._signal_kill,
        now=time.time,
        token_writer=None,
        idle_reap_enabled: bool = False,
        idle_threshold_sec: float = 1080.0,
    ) -> None:
        # ... existing attribute assignments ...
        self.idle_reap_enabled = idle_reap_enabled
        self.idle_threshold_sec = idle_threshold_sec
```

(c) Add `_wake` (next to `_write_token`):

```python
    def _wake(self, entry: dict) -> None:
        """Spawn a consumer for a dormant/parked user — the single lazy-spawn path
        for chat/frame/scheduled/dream triggers. Atomic via leases.acquire: if
        another supervisor wins the race we simply return. Stamps last_active_at at
        spawn so the very next tick's reap pass doesn't immediately re-reap it."""
        user_id = entry.get("user_id")
        if not user_id or user_id in self.children:
            return
        home = self._home(user_id)
        if not leases.acquire(user_id, driver=entry.get("driver", "claude"),
                              runtime_home=home, lease_owner=self.owner,
                              ttl=self.lease_ttl, now=self._now()):
            return  # another supervisor holds it
        pid = self.spawn_fn(entry, user_id, home)
        self._write_token(user_id, home)
        leases.renew(user_id, self.owner, ttl=self.lease_ttl, pid=pid,
                     status="running", driver=entry.get("driver"), now=self._now())
        db.bump_agent_last_active(user_id, now=self._now())
        self.children[user_id] = {"pid": pid, "entry": entry, "home": home}
        log.info("woke dormant consumer for %s (pid=%s)", user_id, pid)
```

(d) In `tick`, change the signature and add the reap pass + dormant-skip:

```python
    def tick(self, roster: list[dict], *, dormant_uids: set[str] | None = None) -> None:
        # ... existing "reap children whose user left roster" block (lines 109-115) ...

        # Idle-reap pass: park live consumers that have been idle past the
        # threshold and are not mid-CLI-turn. Runs before renew/spawn so a reaped
        # user is neither renewed nor respawned this tick. (Feature-gated.) Reads
        # last_active_at via leases.get(uid) per child — NOT list_active — so a
        # momentarily-expired lease can't hide the row (mark_dormant still requires
        # we own it, which fails safe).
        if self.idle_reap_enabled and self.children:
            now = self._now()
            for uid in list(self.children):
                child = self.children[uid]
                if not self.alive_fn(child["pid"]):
                    continue  # dead child handled by the normal pass below
                row = leases.get(uid)
                last_active = _epoch(row.get("last_active_at")) if row else 0.0
                in_flight = os.path.exists(spawners.busy_sentinel_path(child["home"]))
                if reaper.should_reap(last_active_at=last_active, now=now,
                                      idle_sec=self.idle_threshold_sec, in_flight=in_flight):
                    self.kill_fn(child["pid"])
                    leases.mark_dormant(uid, self.owner, now=now)
                    self.children.pop(uid, None)
                    log.info("idle-reap: parked %s as dormant (idle>%.0fs)",
                             uid, self.idle_threshold_sec)

        # ... existing per-roster-entry live/dead/config-change block (lines 117-152) ...

        # Spawn pass: resolve the dormant set once and skip parked users.
        if self.idle_reap_enabled and dormant_uids is None:
            dormant_uids = {r["user_id"] for r in leases.list_dormant()}
        dormant_uids = dormant_uids or set()
        for entry in roster:
            user_id = entry.get("user_id")
            if not user_id or user_id in self.children:
                continue
            if user_id in dormant_uids:
                continue  # parked — only a trigger (via _wake) brings it back
            # ... existing genesis-gate + acquire + spawn + record block ...
            # After self.children[user_id] = {...} (line 169), add (gated so the
            # default-off path issues zero new DB writes):
            if self.idle_reap_enabled:
                db.bump_agent_last_active(user_id, now=self._now())  # start idle clock at spawn
```

(Open `supervisor.py` 154-170 and fold the existing spawn block into the loop above; the only added lines are the two `continue` guards and the post-spawn `bump_agent_last_active`. The existing reap-off-roster (109-115) and renew/respawn (117-152) blocks are unchanged.)

(e) Ensure imports: add `reaper` to the backend-imports block (alongside `leases`, `spawners`). `db`, `os` are already imported.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_idle_reap.py -v`
Expected: PASS (5 passed). Regression-check the existing supervisor suite (it constructs `Supervisor` without the new kwargs → defaults to reap off → unchanged):
`cd backend && python -m pytest ../tests/test_agent_runtime_supervisor.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/agent_runtime/supervisor.py tests/test_agent_runtime_idle_reap.py
git commit -m "feat(agent-runtime): supervisor idle-reap pass + _wake + dormant-skip"
```

---

## Task 9: Supervisor wake listener + wake queue + tick wait

**Files:**
- Modify: `backend/agent_runtime/supervisor.py` (`Supervisor.__init__`; add `_on_notify`, `_enqueue_wake`, `_drain_wakes`, `wait_for_tick`, `start_wake_listener`)
- Test: `tests/test_agent_runtime_idle_reap.py` (append — the pure parts; the blocking listen loop is covered by the end-to-end script, not a unit test)

**Interfaces:**
- Consumes: `core.wake_bus.PG_CHANNEL`, `core.wake_bus.WORKER_ID`, `db.listen_connection`.
- Produces (new on `Supervisor`):
  - `_enqueue_wake(user_id: str) -> None` — thread-safe add to a pending set + set the wake event.
  - `_drain_wakes() -> set[str]` — atomically take and clear the pending set.
  - `_on_notify(payload: str) -> None` — parse a wake-bus JSON payload; enqueue when channel ∈ {chat, frames}, has a user_id, and is not this supervisor's own notify.
  - `wait_for_tick(timeout: float) -> None` — wait on the wake event up to `timeout`, then clear it (replaces `time.sleep(interval)`).
  - `start_wake_listener() -> None` — daemon thread: `LISTEN feedling_wake`, dispatch each notify to `_on_notify`, reconnect on drop.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_runtime_idle_reap.py`:

```python
import json
from core import wake_bus


def test_on_notify_enqueues_chat_and_frames_for_other_workers():
    procs = FakeProcTable()
    sup = _sup(procs, clock=lambda: T0)
    sup._on_notify(json.dumps({"c": "chat", "u": "u_1", "o": "other-worker"}))
    sup._on_notify(json.dumps({"c": "frames", "u": "u_2", "o": "other-worker"}))
    assert sup._drain_wakes() == {"u_1", "u_2"}
    assert sup._drain_wakes() == set()  # drained


def test_on_notify_ignores_own_writes_and_other_channels():
    procs = FakeProcTable()
    sup = _sup(procs, clock=lambda: T0)
    sup._on_notify(json.dumps({"c": "chat", "u": "u_1", "o": wake_bus.WORKER_ID}))  # own
    sup._on_notify(json.dumps({"c": "blob", "u": "u_2", "o": "other"}))             # irrelevant channel
    sup._on_notify("not json")                                                       # malformed
    assert sup._drain_wakes() == set()


def test_wait_for_tick_returns_immediately_when_woken():
    procs = FakeProcTable()
    sup = _sup(procs, clock=lambda: T0)
    sup._enqueue_wake("u_1")          # sets the event
    sup.wait_for_tick(timeout=30)     # must return at once, not block 30s
    assert sup._drain_wakes() == {"u_1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_idle_reap.py -k "notify or wait_for_tick" -v`
Expected: FAIL — `_on_notify` / `_enqueue_wake` / `wait_for_tick` missing.

- [ ] **Step 3: Write minimal implementation**

In `__init__`, add:

```python
        self._wake_requests: set[str] = set()
        self._wake_lock = threading.Lock()
        self._wake_event = threading.Event()
```

Add methods to `Supervisor`:

```python
    def _enqueue_wake(self, user_id: str) -> None:
        if not user_id:
            return
        with self._wake_lock:
            self._wake_requests.add(user_id)
        self._wake_event.set()

    def _drain_wakes(self) -> set[str]:
        with self._wake_lock:
            pending = self._wake_requests
            self._wake_requests = set()
        return pending

    def _on_notify(self, payload: str) -> None:
        """Handle one wake-bus NOTIFY. Enqueue a wake for chat/frame writes from
        OTHER workers (our own writes are tagged with our WORKER_ID and skipped —
        though as a separate process the supervisor never emits these anyway)."""
        try:
            data = json.loads(payload)
        except Exception:
            return
        if data.get("o") == wake_bus.WORKER_ID:
            return
        if (data.get("c") or "") in ("chat", "frames"):
            self._enqueue_wake(data.get("u") or "")

    def wait_for_tick(self, timeout: float) -> None:
        """Sleep until the next tick, returning early when a push wake arrives.
        When reaping is disabled the event is never set, so this is a plain sleep."""
        self._wake_event.wait(timeout)
        self._wake_event.clear()

    def start_wake_listener(self) -> None:
        """Daemon thread holding a dedicated LISTEN connection on the wake bus,
        dispatching chat/frame notifies to _on_notify. Reconnects on drop."""
        def _loop():
            while True:
                conn = None
                try:
                    conn = db.listen_connection()
                    conn.execute(f"LISTEN {wake_bus.PG_CHANNEL}")
                    log.info("supervisor wake-listener up on %s", wake_bus.PG_CHANNEL)
                    for note in conn.notifies():
                        self._on_notify(note.payload)
                except Exception as e:  # noqa: BLE001
                    log.warning("supervisor wake-listener error: %s; reconnecting in 5s", e)
                    time.sleep(5.0)
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
        threading.Thread(target=_loop, daemon=True, name="sup-wake-listener").start()
```

Add `from core import wake_bus` to the backend-imports block (and `json` is already imported at module top).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_idle_reap.py -k "notify or wait_for_tick" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/agent_runtime/supervisor.py tests/test_agent_runtime_idle_reap.py
git commit -m "feat(agent-runtime): supervisor wake listener + push wake queue"
```

---

## Task 10: Supervisor `wake_pass` (push drain + scheduled + backstop) + main-loop wiring

**Files:**
- Modify: `backend/agent_runtime/supervisor.py` (add `wake_pass`; wire into `main()`)
- Test: `tests/test_agent_runtime_idle_reap.py` (append — DB-backed)

**Interfaces:**
- Consumes: `reaper.wakes_due`, `leases.list_dormant`, `scheduled_wake_v2.due_user_ids`, `_drain_wakes`, `_wake`, `_epoch`.
- Produces: `Supervisor.wake_pass(roster: list[dict]) -> set[str]` — computes the dormant users to wake from push + scheduled + missed-notify backstop, wakes each enabled one, and returns the dormant-uid set (so the caller can hand it to `tick` without a second query). Dream pull is added in Task 11. Returns `set()` immediately when reaping is disabled.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_runtime_idle_reap.py`:

```python
def test_wake_pass_wakes_pushed_dormant_user():
    procs = FakeProcTable()
    t = {"v": T0}
    sup = _sup(procs, clock=lambda: t["v"])
    sup.tick(_roster("u_1"))
    t["v"] = T0 + 1081
    sup.tick(_roster("u_1"))                       # dormant
    sup._enqueue_wake("u_1")                        # a chat notify arrived

    sup.wake_pass(_roster("u_1"))
    assert leases.get("u_1")["status"] == "running"


def test_wake_pass_backstop_wakes_dormant_user_with_late_activity():
    procs = FakeProcTable()
    t = {"v": T0}
    sup = _sup(procs, clock=lambda: t["v"])
    sup.tick(_roster("u_1"))
    t["v"] = T0 + 1081
    sup.tick(_roster("u_1"))                       # dormant; last_heartbeat_at=now
    db.bump_agent_last_active("u_1")               # missed-notify: activity after dormancy

    sup.wake_pass(_roster("u_1"))                  # backstop catches it
    assert leases.get("u_1")["status"] == "running"


def test_wake_pass_noop_when_reap_disabled():
    procs = FakeProcTable()
    sup = _sup(procs, clock=lambda: T0, reap=False)
    assert sup.wake_pass(_roster("u_1")) == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_idle_reap.py -k "wake_pass" -v`
Expected: FAIL — `Supervisor` has no `wake_pass`.

- [ ] **Step 3: Write minimal implementation**

Add to `Supervisor`:

```python
    def wake_pass(self, roster: list[dict]) -> set[str]:
        """Wake dormant users whose work has arrived: pushed chat/frame notifies,
        due scheduled timers, and the missed-notify backstop (a dormant row whose
        last_active_at advanced past the dormancy heartbeat). Returns the dormant
        uid set so the caller can pass it to tick() without re-querying."""
        if not self.idle_reap_enabled:
            return set()
        dormant_rows = leases.list_dormant()
        dormant_uids = {r["user_id"] for r in dormant_rows}
        by_uid = {e["user_id"]: e for e in roster if e.get("user_id")}
        push = self._drain_wakes()
        scheduled = scheduled_wake_v2.due_user_ids(now=self._now())
        backstop = {r["user_id"] for r in dormant_rows
                    if _epoch(r["last_active_at"]) > _epoch(r["last_heartbeat_at"])}
        to_wake = reaper.wakes_due(dormant_uids=dormant_uids, scheduled_due_uids=scheduled,
                                   backstop_uids=backstop, push_uids=push)
        for uid in to_wake:
            entry = by_uid.get(uid)
            if entry is not None:
                self._wake(entry)
        # re-derive dormant after waking, for tick's spawn-skip
        return dormant_uids - set(self.children)
```

Wire into `main()` — replace the existing `sup.tick(roster)` (line 788) and `time.sleep(interval)` (line 816):

```python
                if gateway_mgr is not None:
                    gateway_mgr.reconcile(gateways)
                dormant_uids = sup.wake_pass(roster)        # wake first
                sup.tick(roster, dormant_uids=dormant_uids)  # then reap/renew/spawn
                # ... existing persona-backfill + autoverify blocks ...
            except Exception as e:  # noqa: BLE001
                log.exception("supervisor tick failed: %s", e)
            sup.wait_for_tick(interval)                      # was: time.sleep(interval)
```

And start the listener once, after the `Supervisor(...)` is constructed (after line 723) — only when reaping is enabled:

```python
    if util.agent_idle_reap_enabled():
        sup.start_wake_listener()
```

Construct the supervisor with the flags (modify the `Supervisor(...)` call at line 719):

```python
    sup = Supervisor(
        owner=owner, lease_ttl=lease_ttl, data_root=data_root,
        spawn_fn=spawn_fn, alive_fn=alive_fn, kill_fn=kill_fn,
        token_writer=token_writer,
        idle_reap_enabled=util.agent_idle_reap_enabled(),
        idle_threshold_sec=float(util.agent_idle_threshold_sec()),
    )
```

Add `from core import util` and `from proactive import scheduled_wake_v2` to the backend-imports block.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_idle_reap.py -k "wake_pass" -v`
Expected: PASS (3 passed). Full agent-runtime regression:
`cd backend && python -m pytest ../tests/ -k "agent_runtime or supervisor or leases" -q`

- [ ] **Step 5: Commit**

```bash
git add backend/agent_runtime/supervisor.py tests/test_agent_runtime_idle_reap.py
git commit -m "feat(agent-runtime): wake_pass (push+scheduled+backstop) + main-loop wiring"
```

---

## Task 11: Dream pull (night-window wake for dormant users)

**Files:**
- Modify: `backend/agent_runtime/supervisor.py` (`Supervisor.__init__`; add `_user_tz`, `_in_night_window`; extend `wake_pass`)
- Test: `tests/test_agent_runtime_idle_reap.py` (append — DB-backed)

**Interfaces:**
- Consumes: `reaper.dream_wake_due`, `dream_scheduler.night_only`, `dream_scheduler.night_start_hour`, `dream_scheduler.night_end_hour`, `db.get_blob(user_id, "proactive_settings")`, `_epoch`.
- Produces (new on `Supervisor`):
  - `_user_tz(user_id: str) -> ZoneInfo` — per-user timezone from the `proactive_settings` blob, cached in `self._tz_cache`, defaulting to UTC.
  - `_in_night_window(user_id: str, now: float) -> bool` — mirrors `dream_scheduler._within_night_window`: `True` when `not night_only()`, else whether the user's local hour is inside `[night_start_hour, night_end_hour)`.
  - `wake_pass` additionally wakes a dormant user when `reaper.dream_wake_due(...)` holds, recording the wake time in `self._dream_wake_at` (in-process dedup).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_runtime_idle_reap.py`:

```python
from datetime import datetime, timezone
from proactive import dream_scheduler


def _is_night_now_utc():
    h = datetime.now(timezone.utc).hour
    s, e = dream_scheduler.night_start_hour(), dream_scheduler.night_end_hour()
    return (s <= h < e) if s <= e else (h >= s or h < e)


def test_dream_pull_wakes_in_window_dormant_user_with_activity(monkeypatch):
    procs = FakeProcTable()
    t = {"v": T0}
    sup = _sup(procs, clock=lambda: t["v"])
    # Force "in night window" deterministically (tz logic is unit-tested via reaper).
    monkeypatch.setattr(sup, "_in_night_window", lambda uid, now: True)
    sup.tick(_roster("u_1"))
    t["v"] = T0 + 1081
    sup.tick(_roster("u_1"))                       # dormant; last_active stamped at spawn (T0)

    sup.wake_pass(_roster("u_1"))                  # last_active(T0) > last_dream_wake(0) -> dream wake
    assert leases.get("u_1")["status"] == "running"


def test_dream_pull_does_not_reflap_without_new_activity(monkeypatch):
    procs = FakeProcTable()
    t = {"v": T0}
    sup = _sup(procs, clock=lambda: t["v"])
    monkeypatch.setattr(sup, "_in_night_window", lambda uid, now: True)
    sup.tick(_roster("u_1"))
    t["v"] = T0 + 1081
    sup.tick(_roster("u_1"))
    sup.wake_pass(_roster("u_1"))                  # first dream wake; records dream_wake_at
    # reap again without any new activity
    t["v"] = T0 + 1081 * 2
    sup.tick(_roster("u_1"), dormant_uids=set())   # force reap path -> dormant again
    spawned_before = len(procs.spawned)
    sup.wake_pass(_roster("u_1"))                  # no new activity since last dream -> no wake
    assert len(procs.spawned) == spawned_before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_idle_reap.py -k "dream_pull" -v`
Expected: FAIL — `_in_night_window` missing / no dream wake.

- [ ] **Step 3: Write minimal implementation**

In `__init__`, add:

```python
        self._tz_cache: dict = {}
        self._dream_wake_at: dict[str, float] = {}
```

Add methods:

```python
    def _user_tz(self, user_id: str):
        tz = self._tz_cache.get(user_id)
        if tz is not None:
            return tz
        name = "UTC"
        try:
            settings = db.get_blob(user_id, "proactive_settings")
            if isinstance(settings, dict) and settings.get("timezone"):
                name = str(settings["timezone"])
        except Exception:  # noqa: BLE001
            name = "UTC"
        try:
            tz = ZoneInfo(name)
        except Exception:  # noqa: BLE001
            tz = ZoneInfo("UTC")
        self._tz_cache[user_id] = tz
        return tz

    def _in_night_window(self, user_id: str, now: float) -> bool:
        """Whether the user is currently in their dream window. Mirrors
        dream_scheduler._within_night_window: when night-only is off, always true
        (dreams may run anytime; the woken consumer dedups via dream_key)."""
        if not dream_scheduler.night_only():
            return True
        local = datetime.fromtimestamp(now, timezone.utc).astimezone(self._user_tz(user_id))
        start = dream_scheduler.night_start_hour()
        end = dream_scheduler.night_end_hour()
        hour = local.hour
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end
```

Extend `wake_pass` — after the `for uid in to_wake:` loop, before `return`:

```python
        # Dream pull: wake an in-window dormant user that has had activity since we
        # last woke it to dream. The woken consumer's proactive tick runs the
        # authoritative undigested-moments check (dream_key dedup); a user with
        # nothing new simply idles back to dormant.
        now = self._now()
        for r in dormant_rows:
            uid = r["user_id"]
            if uid in self.children or uid not in by_uid:
                continue
            if reaper.dream_wake_due(in_window=self._in_night_window(uid, now),
                                     last_active_at=_epoch(r["last_active_at"]),
                                     last_dream_wake_at=self._dream_wake_at.get(uid, 0.0)):
                self._wake(by_uid[uid])
                self._dream_wake_at[uid] = now
```

Add `from datetime import datetime, timezone`, `from zoneinfo import ZoneInfo`, and `from proactive import dream_scheduler` to the imports (group the backend ones with the other `from proactive import …`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_agent_runtime_idle_reap.py -k "dream_pull" -v`
Expected: PASS (2 passed). Full file:
`cd backend && python -m pytest ../tests/test_agent_runtime_idle_reap.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/agent_runtime/supervisor.py tests/test_agent_runtime_idle_reap.py
git commit -m "feat(agent-runtime): dream-window pull wake for dormant users"
```

---

## Task 12: Full-suite regression + docs note

**Files:**
- Modify: `docs/CHANGELOG.md` (prepend a dated entry per repo convention)
- Test: whole suite

**Interfaces:** none (verification + documentation task).

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && python -m pytest ../tests/ -q`
Expected: all green (no regressions). Pay attention to `test_agent_runtime_supervisor.py`, `test_model_api_chat_send_routing.py`, `test_wake_bus.py`.

- [ ] **Step 2: Confirm default-off is a true no-op**

Run with the flag explicitly off and assert no dormant transition path is exercised:
`cd backend && AGENT_IDLE_REAP_ENABLED=false python -m pytest ../tests/test_agent_runtime_supervisor.py -q`
Expected: PASS, identical to pre-change behavior (the supervisor suite never sets the flag).

- [ ] **Step 3: Write the CHANGELOG entry**

Prepend to `docs/CHANGELOG.md`:

```markdown
## 2026-06-29 — Agent-runner idle-reap + lazy-spawn (Phase A, default-off)

Hosted consumers idle past `AGENT_IDLE_THRESHOLD_SEC` (default 18 min) and not
mid-CLI-turn are reaped to a new `dormant` lease status and lazily re-spawned on
the next unit of work: chat/frame writes (push, via the `feedling_wake` LISTEN
bus + a supervisor-side listener), due scheduled wakes, and night-window dreams
(pull, polled each tick). Gated behind `AGENT_IDLE_REAP_ENABLED` (default false)
— shipped off, byte-for-byte today's all-resident behavior. Decision logic is the
pure `backend/agent_runtime/reaper.py`; `last_active_at` is bumped at the chat and
frame write chokepoints; in-flight is signalled by a `.agent-busy` sentinel the
consumer writes around each CLI turn. Message-zero-loss invariant: messages are
durable in Postgres before/independent of any consumer; reap changes only when a
message is processed, never whether it is delivered. B-phase (concurrent-active
CLI cap + queueing) is a separate spec.
```

- [ ] **Step 4: Commit**

```bash
git add docs/CHANGELOG.md
git commit -m "docs: changelog for agent-runner idle-reap (Phase A)"
```

---

## Rollout (post-merge, operator runbook — not code)

1. Merge with `AGENT_IDLE_REAP_ENABLED` unset (off). Verify behavior unchanged on test.
2. On the **test** CVM only, set `AGENT_IDLE_REAP_ENABLED=true` (and optionally a lower `AGENT_IDLE_THRESHOLD_SEC` to exercise reaping faster). Observe over a day:
   - agent-runner RSS drops; backend long-poll connection count drops.
   - reminders/dreams still fire (scheduled + dream pull working).
   - **flap count** per user (reap→wake churn) — if high, raise the threshold.
   - reply-latency distribution unchanged for active users (no missed wakes).
   - **backstop-hit count > 0** means notifies are being missed — investigate the listener before prod.
3. Once stable on test, enable on prod.
4. Observability to add while enabling (log-grep is enough for Phase A): `idle-reap: parked <uid>`, `woke dormant consumer for <uid>`, `supervisor wake-listener up`.

## Out of scope (Phase B — separate spec)

Concurrent active-CLI cap + queue/backpressure (OOM-spike protection). Idle-reap
trims the baseline (total registered × resident cost); B trims the peak
(concurrent active × CLI memory). Complementary, independent.
```