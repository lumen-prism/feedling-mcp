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


def test_wake_pass_noop_when_reap_disabled(monkeypatch):
    procs = FakeProcTable()
    sup = _sup(procs, clock=lambda: T0, reap=False)
    # Default-off path must issue ZERO new DB queries (Global Constraint): the
    # disabled wake_pass returns before touching the lease table.
    calls = []
    monkeypatch.setattr(leases, "list_dormant", lambda: calls.append(1) or [])
    assert sup.wake_pass(_roster("u_1")) == set()
    assert calls == []


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


def test_production_wiring_no_respawn_when_reaped_same_tick():
    # Regression for the cross-task integration bug: production calls
    # `dormant = wake_pass(roster); tick(roster, dormant_uids=dormant)`. A consumer
    # that crosses the idle threshold DURING this tick is reaped by the reap pass,
    # but `dormant` was computed by wake_pass BEFORE the reap (so it omits this
    # uid). The spawn pass must still skip it (via reaped_now) — otherwise it
    # re-acquires the just-cleared lease and respawns the user every tick.
    procs = FakeProcTable()
    t = {"v": T0}
    sup = _sup(procs, clock=lambda: t["v"])
    sup.tick(_roster("u_1"))                       # spawn at T0; last_active=T0
    assert leases.get("u_1")["status"] == "running"

    t["v"] = T0 + 1081                             # idle past threshold
    dormant = sup.wake_pass(_roster("u_1"))        # computed while u_1 still running
    assert dormant == set()                        # u_1 not yet dormant here
    sup.tick(_roster("u_1"), dormant_uids=dormant)  # reaped THIS tick — must NOT respawn

    assert procs.spawned == [({"user_id": "u_1", "driver": "claude", "api_key": "k"}, "u_1", "/agent-data/users/u_1")]
    assert leases.get("u_1")["status"] == "dormant"
    assert "u_1" not in sup.children


def test_dream_wake_marker_matches_stored_last_active(monkeypatch):
    # Regression (Codex P2): the dream-wake dedup marker must equal the
    # last_active_at the wake actually STORED (Postgres rounds epoch->microsecond),
    # NOT the raw float clock. Otherwise _wake's own activity bump leaves
    # last_active_at a sub-microsecond AHEAD of the marker, so the strict-`>` dedup
    # re-fires the dream every idle window with no new activity. A clock carrying a
    # sub-microsecond fraction exposes the gap.
    procs = FakeProcTable()
    t = {"v": 1000.0}
    sup = _sup(procs, clock=lambda: t["v"])
    monkeypatch.setattr(sup, "_in_night_window", lambda uid, now: True)
    sup.tick(_roster("u_1"))                       # spawn
    t["v"] = 1000.0 + 1081 + 0.0001234            # idle past threshold; sub-us fraction
    sup.tick(_roster("u_1"))                       # dormant
    sup.wake_pass(_roster("u_1"))                  # dream-wakes u_1 at the fractional clock

    stored = supervisor_mod._epoch(leases.get("u_1")["last_active_at"])
    # marker is the value last_active_at actually stored, not the raw float clock.
    assert sup._dream_wake_at["u_1"] == stored
    # so the dedup correctly refuses a repeat dream when nothing new happened.
    from agent_runtime import reaper
    assert reaper.dream_wake_due(in_window=True, last_active_at=stored,
                                 last_dream_wake_at=sup._dream_wake_at["u_1"]) is False


def test_spawn_pass_skips_user_parked_after_wake_pass_snapshot():
    # Multi-supervisor race (Codex P2): another supervisor parks u_1 as dormant
    # AFTER our wake_pass computed its (now-stale) dormant_uids snapshot. The spawn
    # pass must re-confirm dormancy before acquire — otherwise it re-acquires the
    # cleared lease (mark_dormant set lease_expires_at NULL) and respawns a parked
    # user with NO wake trigger, defeating idle reaping.
    procs = FakeProcTable()
    sup = _sup(procs, clock=lambda: T0)
    sup.tick(_roster("u_1"))                       # u_1 running, owned by sup_A
    leases.mark_dormant("u_1", "sup_A", now=T0)    # parked in the DB...
    sup.children.pop("u_1", None)                  # ...and our child handle is gone
    spawned_before = len(procs.spawned)

    # Passed snapshot is stale (omits u_1); reap pass is a no-op (no children).
    sup.tick(_roster("u_1"), dormant_uids=set())
    assert len(procs.spawned) == spawned_before    # must NOT respawn the parked user
    assert leases.get("u_1")["status"] == "dormant"
