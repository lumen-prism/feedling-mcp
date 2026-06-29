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
