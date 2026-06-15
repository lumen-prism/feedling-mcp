"""Resolvers for the PLAIN (operational) context_snapshot signals.

These run on ingest for cleartext signals only: they take the parsed `data`
object and pick out the state fields. Sensitive signals (location / motion /
calendar / playback) no longer have server-side resolvers — the device resolves
raw values to coarse labels locally (geofence/SSID matching lives in the iOS
app) and reports a v1 envelope; the backend never sees raw OR resolved values.

Each resolver returns a dict {output_field: value}.
"""
from __future__ import annotations


# iOS Focus -> user_state mapping. This is the SINGLE source of truth: there is
# no longer a server-side per-user override (perception_config is gone — see
# routes.report's 410). iOS only exposes a binary "focused" anyway, so a custom
# named-focus map carried no real signal; hardcoding the default removes the
# last server-side perception_config consumer and the "config rejected but still
# read" contradiction.
_DEFAULT_FOCUS_MAP = {
    "none": "default",
    "work": "focused",
    "sleep": "away",
    "driving": "away",
    "dnd": "away",
    "do_not_disturb": "away",
    "personal": "default",
    # any custom / unrecognized focus -> focused (doc default)
}


def resolve_focus(value, config: dict) -> dict:
    """value: ios_focus identifier (or "" / "none" when Focus cleared).
    Returns {"user_state": <default|focused|away>}. The override/restore stack
    (Focus overrides the manual user_state, clearing restores it) is applied in
    service, which treats the `focus` capability specially.

    Focus stays a CLEARTEXT signal: it is binary on iOS (focused / not) and is
    an input to the backend's wake gate, which must read it synchronously. The
    `config` arg is ignored (kept for the resolver signature) — the mapping is
    fixed; no server-side focus config exists anymore.
    """
    if isinstance(value, dict):
        focus = value.get("ios_focus") or value.get("focus") or value.get("state")
    else:
        focus = value
    focus = (str(focus) if focus is not None else "none").strip().lower()
    if focus in _DEFAULT_FOCUS_MAP:
        return {"user_state": _DEFAULT_FOCUS_MAP[focus]}
    return {"user_state": "focused"}  # custom focus default


def resolve_time(value, config: dict) -> dict:
    """`time` data: {local_time, timezone, locale}. Stored as-is."""
    if not isinstance(value, dict):
        return {}
    return {k: value.get(k) for k in ("local_time", "timezone", "locale")}


def resolve_battery(value, config: dict) -> dict:
    """`battery` data: {level, charging} → battery_level / charging."""
    if not isinstance(value, dict):
        return {}
    return {"battery_level": value.get("level"), "charging": value.get("charging")}


def resolve_broadcast(value, config: dict) -> dict:
    """`broadcast` data: {state, active} → broadcast_state / broadcast_active."""
    if not isinstance(value, dict):
        return {}
    return {"broadcast_state": value.get("state"),
            "broadcast_active": value.get("active")}


RESOLVERS = {
    "focus": resolve_focus,
    "time": resolve_time,
    "battery": resolve_battery,
    "broadcast": resolve_broadcast,
}
