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
