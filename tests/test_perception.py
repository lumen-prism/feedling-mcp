"""Unit tests for the Extended Perception service logic.

These exercise the generic machinery — sparse report with v1-envelope-encrypted
sensitive signals (plaintext rejected), cleartext operational signals, the
client-driven `changed` wake flag with debounce, the ciphertext snapshot shape
({fields, encrypted, recent_apps}), snapshot TTL off the cleartext ts, the
user_state override stack, server-side sealing for app_open, and the photo
sensitivity gate with the encrypted meta_envelope — WITHOUT a real Postgres:
the store layer is replaced with an in-memory fake, and the wake trigger is
captured instead of enqueuing a real proactive job.

Run:  python -m pytest tests/test_perception.py -q
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import perception.service as service
import perception.wake as wake
from content_encryption import build_envelope


UID = "u1"


def _raw_pub(sk) -> bytes:
    return sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


# Real keypairs so envelopes are structurally valid end-to-end (the enclave
# round-trip itself is covered in test_perception_enclave.py).
USER_SK = X25519PrivateKey.generate()
USER_PK = _raw_pub(USER_SK)
ENCLAVE_SK = X25519PrivateKey.generate()
ENCLAVE_PK = _raw_pub(ENCLAVE_SK)


class FakeStore:
    """In-memory stand-in for perception.store."""
    def __init__(self):
        self.state = {}
        self.config = {}
        self.user_state = {}
        self.items = {}   # (uid, kind) -> {item_id: {"ts","expires_at","doc"}}
        self.events = {}  # uid -> [event...]
        self.frames = {}  # (uid, frame_id) -> envelope
        self.app_opens = {}  # uid -> [event...]

    # singletons
    def get_state(self, uid): return {k: dict(v) for k, v in self.state.get(uid, {}).items()}
    def merge_state(self, uid, patch):
        self.state.setdefault(uid, {}).update(patch); return self.get_state(uid)
    def merge_state_guarded(self, uid, patch):
        cur = self.state.setdefault(uid, {})
        written = set()
        for f, cell in patch.items():
            old = cur.get(f)
            old_ts = old.get("ts") if isinstance(old, dict) else None
            new_ts = cell.get("ts")
            if old_ts is None or new_ts is None or float(new_ts) >= float(old_ts):
                cur[f] = dict(cell); written.add(f)
        return written
    def clear_state_fields(self, uid, fields):
        for f in fields:
            self.state.get(uid, {}).pop(f, None)

    def get_config(self, uid): return dict(self.config.get(uid, {}))

    def get_user_state_doc(self, uid): return dict(self.user_state.get(uid, {}))
    def set_user_state_doc(self, uid, doc): self.user_state[uid] = dict(doc)
    def set_manual_user_state_guarded(self, uid, value, ts):
        doc = dict(self.user_state.get(uid, {}))
        prev_ts = doc.get("manual_ts")
        if prev_ts is None or float(ts) >= float(prev_ts):
            doc["manual"] = str(value or "default")
            doc["manual_ts"] = float(ts)
            self.user_state[uid] = doc
        return dict(self.user_state.get(uid, {}))

    # collections
    def item_upsert(self, uid, kind, item_id, ts, doc, expires_at=None):
        self.items.setdefault((uid, kind), {})[item_id] = {
            "ts": ts, "expires_at": expires_at, "doc": dict(doc)}
    def item_get(self, uid, kind, item_id, now=None):
        row = self.items.get((uid, kind), {}).get(item_id)
        if not row:
            return None
        if now is not None and row["expires_at"] is not None and row["expires_at"] <= now:
            return None
        return dict(row["doc"])
    def item_list(self, uid, kind, limit=20, now=None):
        return [r["doc"] for r in self._rows(uid, kind, now)[:limit]]
    def item_list_rows(self, uid, kind, limit=20, now=None):
        return [{"item_id": iid, "ts": r["ts"], "expires_at": r["expires_at"],
                 "doc": dict(r["doc"])}
                for iid, r in self._rows_with_ids(uid, kind, now)[:limit]]
    def _rows(self, uid, kind, now):
        return [r for _, r in self._rows_with_ids(uid, kind, now)]
    def _rows_with_ids(self, uid, kind, now):
        rows = list(self.items.get((uid, kind), {}).items())
        if now is not None:
            rows = [(i, r) for i, r in rows
                    if r["expires_at"] is None or r["expires_at"] > now]
        rows.sort(key=lambda p: p[1]["ts"], reverse=True)
        return rows
    def item_patch(self, uid, kind, item_id, patch, expires_at="__keep__"):
        row = self.items.get((uid, kind), {}).get(item_id)
        if not row:
            return None
        row["doc"].update(patch)
        if expires_at != "__keep__":
            row["expires_at"] = expires_at
        return dict(row["doc"])

    # events
    def append_event(self, uid, event, ts):
        self.events.setdefault(uid, []).append(dict(event))
    def read_events(self, uid, limit=50):
        return [dict(e) for e in self.events.get(uid, [])[-limit:]]

    # app-usage time series
    def append_app_open(self, uid, doc, ts):
        self.app_opens.setdefault(uid, []).append(dict(doc))
    def read_app_opens(self, uid, limit=100, since_epoch=0.0):
        rows = self.app_opens.get(uid, [])
        if since_epoch:
            rows = [r for r in rows if (r.get("ts") or 0) > since_epoch]
        return [dict(r) for r in rows[-limit:]]

    # photo ciphertext channel (reuses frame_envelopes in prod)
    def put_photo_envelope(self, uid, frame_id, ts, env):
        self.frames[(uid, frame_id)] = dict(env)
    def get_photo_envelope(self, uid, frame_id):
        return self.frames.get((uid, frame_id))
    def delete_photo_envelope(self, uid, frame_id):
        self.frames.pop((uid, frame_id), None)


@pytest.fixture
def env(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(service, "store", fake)
    wakes = []
    monkeypatch.setattr(service, "_fire_wake",
                        lambda uid, cap, hint, now: wakes.append((cap, hint)))
    return fake, wakes


@pytest.fixture
def sealer(monkeypatch):
    """Replace the server-side app_open sealer with a real local envelope build."""
    def seal(uid, plaintext):
        return build_envelope(plaintext=plaintext, owner_user_id=uid,
                              user_pk_bytes=USER_PK, enclave_pk_bytes=ENCLAVE_PK,
                              visibility="shared"), ""
    monkeypatch.setattr(service, "_seal_for_user", seal)


# ---------------------------------------------------------------------------

def _item(key, obj, message=""):
    """Build one PLAIN context_snapshot item; obj=None -> data:"null"."""
    return {"key": key, "data": ("null" if obj is None else json.dumps(obj)), "message": message}


def _mk_env(body_obj, owner=UID, visibility="shared", item_id=None):
    return build_envelope(
        plaintext=json.dumps(body_obj).encode("utf-8"),
        owner_user_id=owner, user_pk_bytes=USER_PK,
        enclave_pk_bytes=ENCLAVE_PK if visibility == "shared" else None,
        visibility=visibility, item_id=item_id)


def _enc_item(key, values, message=None, changed=False, owner=UID):
    """Build one ENCRYPTED context_snapshot item: v1 envelope + changed flag."""
    body = {"values": values}
    if message is not None:
        body["message"] = message
    return {"key": key, "envelope": _mk_env(body, owner=owner), "changed": changed}


def _ingest_location(uid, label, changed=True):
    return service.ingest_snapshot(uid, [_enc_item(
        "location_signal", {"place_label": label}, changed=changed, owner=uid)])


# ---------------------------------------------------------------------------
# Plain (operational) signals — unchanged behavior
# ---------------------------------------------------------------------------

def test_always_on_signals_stored(env):
    fake, _ = env  # time/battery/broadcast always available, cleartext
    service.ingest_snapshot(UID, [
        _item("time", {"local_time": "2026-06-08T15:30:45Z",
                       "timezone": "America/Los_Angeles", "locale": "en"}),
        _item("battery", {"level": "0.85", "charging": "true"}),
        _item("broadcast", {"state": "inactive", "active": "false"}),
    ])
    snap = service.snapshot(UID)
    assert snap["fields"]["local_time"] == "2026-06-08T15:30:45Z"
    assert snap["fields"]["battery_level"] == "0.85"
    assert snap["fields"]["broadcast_state"] == "inactive"


def test_string_scalar_values_stored(env):
    """默认字符串: scalar values may arrive as strings; stored verbatim."""
    fake, _ = env  # battery is always-on
    service.ingest_snapshot(UID, [_item("battery", {"level": "0.55", "charging": "false"})])
    st = fake.get_state(UID)
    assert st["battery_level"]["v"] == "0.55"
    assert st["charging"]["v"] == "false"


def test_plain_field_ttl_nulls_stale(env):
    fake, _ = env
    service.ingest_snapshot(UID, [_item("battery", {"level": "0.85", "charging": "true"})])
    fake.state[UID]["battery_level"]["ts"] = time.time() - 10_000
    assert service.snapshot(UID)["fields"]["battery_level"] is None


def test_manual_user_state(env):
    fake, _ = env
    assert service.snapshot(UID)["fields"]["user_state"] == "default"   # always present
    service.set_manual_user_state(UID, "focused")
    assert service.snapshot(UID)["fields"]["user_state"] == "focused"


def test_unsupported_ignored(env):
    fake, _ = env
    res = service.ingest_snapshot(UID, [_item("unsupported", {
        "frontmost_app": None, "silent_mode": None, "focus": None, "precise_unlock": None})])
    assert res["unsupported"] == "ignored"


# ---------------------------------------------------------------------------
# Encrypted signals — envelope mandatory, ciphertext at rest
# ---------------------------------------------------------------------------

def test_encrypted_signal_stored_as_ciphertext_cell(env):
    fake, _ = env
    res = service.ingest_snapshot(UID, [_enc_item(
        "location_signal", {"place_label": "home", "wifi_label": "home_wifi", "country": "US"})])
    assert res["location_signal"] == "accepted"
    cell = fake.get_state(UID)["location_signal"]
    assert isinstance(cell["env"], dict) and cell["env"]["body_ct"]
    # Resolvers must NOT run on encrypted signals: only the ciphertext cell is
    # stored — no resolved plaintext fields.
    assert set(cell.keys()) == {"env", "ts"}
    # No PLAINTEXT sensitive value leaks into the stored state. Scan everything
    # EXCEPT the opaque sealed fields: those are random base64 ciphertext, where a
    # short substring like "US" appears by chance and would flake the assertion.
    _SEALED = {"body_ct", "nonce", "K_user", "K_enclave"}
    visible = {k: v for k, v in cell["env"].items() if k not in _SEALED}
    blob = str(visible) + str({k: v for k, v in cell.items() if k != "env"})
    for sensitive in ("home", "home_wifi", "US", "place_label"):
        assert sensitive not in blob


def test_plaintext_for_encrypted_signal_rejected(env):
    fake, _ = env
    res = service.ingest_snapshot(UID, [_item("motion_state", {"state": "walking"})])
    assert res["motion_state"] == "rejected:plaintext_requires_envelope"
    assert "motion_state" not in fake.state.get(UID, {})


def test_envelope_owner_mismatch_rejected(env):
    fake, _ = env
    res = service.ingest_snapshot(UID, [_enc_item(
        "motion_state", {"motion_state": {"state": "walking"}}, owner="someone_else")])
    assert res["motion_state"] == "rejected:envelope_owner_mismatch"
    assert "motion_state" not in fake.state.get(UID, {})


def test_envelope_shared_requires_k_enclave(env):
    fake, _ = env
    it = _enc_item("motion_state", {"motion_state": {"state": "walking"}})
    it["envelope"].pop("K_enclave")
    res = service.ingest_snapshot(UID, [it])
    assert res["motion_state"] == "rejected:envelope_missing_fields:K_enclave"


def test_malformed_envelope_rejected_not_cleared(env):
    """A non-dict envelope (string/list) with no `data` must be rejected — not
    silently treated as a null report that clears existing state (Codex P2)."""
    fake, _ = env
    # seed a valid value first
    service.ingest_snapshot(UID, [_enc_item("motion_state", {"motion_state": "walking"})])
    seeded = fake.get_state(UID)["motion_state"]["env"]
    # now a buggy client sends a malformed envelope (a string), no data
    res = service.ingest_snapshot(UID, [{"key": "motion_state", "envelope": "garbage"}])
    assert res["motion_state"] == "rejected:malformed_envelope"
    # existing value untouched (NOT cleared)
    cell = fake.get_state(UID)["motion_state"]
    assert cell.get("env", {}).get("body_ct") == seeded["body_ct"]
    assert cell.get("v", "MISSING") == "MISSING"   # never became a null cell


def test_envelope_missing_fields_rejected(env):
    fake, _ = env
    it = _enc_item("motion_state", {"motion_state": {"state": "walking"}})
    it["envelope"].pop("nonce")
    res = service.ingest_snapshot(UID, [it])
    assert res["motion_state"].startswith("rejected:envelope_missing_fields:")


def test_encrypted_alias_resolves(env):
    """Aliases still resolve (location -> location_signal) for encrypted items."""
    fake, _ = env
    res = service.ingest_snapshot(UID, [
        {"key": "location", "envelope": _mk_env({"values": {"place_label": "home"}}),
         "changed": True}])
    assert res["location"] == "accepted"
    assert "env" in fake.get_state(UID)["location_signal"]


def test_encrypted_null_records_unavailable(env):
    """data:"null" for an encrypted signal: null cell with operational message;
    the snapshot's encrypted section omits it."""
    fake, _ = env
    service.ingest_snapshot(UID, [_enc_item("location_signal", {"place_label": "home"})])
    assert "env" in fake.get_state(UID)["location_signal"]
    service.ingest_snapshot(UID, [{"key": "location_signal", "data": "null", "message": "未授权定位"}])
    cell = fake.get_state(UID)["location_signal"]
    assert cell["v"] is None and cell["msg"] == "未授权定位" and "env" not in cell
    assert "location_signal" not in service.snapshot(UID)["encrypted"]


def test_encrypted_omitted_data_does_not_clear_cell(env):
    """A malformed item with NEITHER an envelope NOR a `data` field (e.g.
    {"key":"location_signal"}) must be rejected — NOT treated as an explicit
    data:"null" that clears a valid encrypted cell (Codex P2). Only an explicit
    null clears."""
    fake, _ = env
    service.ingest_snapshot(UID, [_enc_item("location_signal", {"place_label": "home"})])
    seeded = fake.get_state(UID)["location_signal"]["env"]
    res = service.ingest_snapshot(UID, [{"key": "location_signal"}])
    assert res["location_signal"] == "rejected:envelope_required"
    cell = fake.get_state(UID)["location_signal"]
    assert cell.get("env", {}).get("body_ct") == seeded["body_ct"]   # untouched
    assert cell.get("v", "MISSING") == "MISSING"                     # never null-cleared


def test_envelope_non_integer_version_rejected(env):
    """An envelope with a non-integer v passes the field checks but would crash
    the enclave at int(env["v"]) with an uncaught ValueError (500). Reject it at
    the write seam (Codex P1)."""
    fake, _ = env
    it = _enc_item("motion_state", {"motion_state": "walking"})
    it["envelope"]["v"] = "x"
    res = service.ingest_snapshot(UID, [it])
    assert res["motion_state"] == "rejected:envelope_unsupported_version"
    assert "motion_state" not in fake.get_state(UID)            # nothing stored


def test_encrypted_cell_ts_guard(env):
    """A late-arriving OLDER encrypted record must not clobber a newer one."""
    fake, _ = env
    service.ingest_snapshot(UID, [_enc_item("motion_state", {"motion_state": "running"})],
                            client_ts=200.0)
    newer_env = fake.get_state(UID)["motion_state"]["env"]
    res = service.ingest_snapshot(UID, [_enc_item("motion_state", {"motion_state": "still"})],
                                  client_ts=100.0)
    assert res["motion_state"] == "stale_ignored"
    cell = fake.get_state(UID)["motion_state"]
    assert cell["ts"] == 200.0 and cell["env"]["body_ct"] == newer_env["body_ct"]


def test_client_ts_is_logical_time(env):
    fake, _ = env
    service.ingest_snapshot(UID, [_enc_item("motion_state", {"motion_state": "walking"})],
                            client_ts=1000.0)
    assert fake.get_state(UID)["motion_state"]["ts"] == 1000.0


def test_future_client_ts_clamped(env):
    """A far-future client_ts (clock skew / ms) is clamped to now, so it can't
    freeze state by making the ordering guard reject later correct reports."""
    fake, _ = env
    future = time.time() + 10 * 365 * 86400        # ~10 years ahead (or ms-as-s)
    service.ingest_snapshot(UID, [_enc_item("motion_state", {"motion_state": "walking"})],
                            client_ts=future)
    stored_ts = fake.get_state(UID)["motion_state"]["ts"]
    assert stored_ts <= time.time() + 1             # clamped to ~now, not the future
    res = service.ingest_snapshot(UID, [_enc_item("motion_state", {"motion_state": "still"})])
    assert res["motion_state"] == "accepted"


# ---------------------------------------------------------------------------
# Snapshot — ciphertext shape {fields, encrypted, recent_apps}
# ---------------------------------------------------------------------------

def test_snapshot_three_section_shape(env, sealer):
    fake, _ = env
    service.ingest_snapshot(UID, [
        _item("battery", {"level": "0.85", "charging": "true"}),
        _enc_item("location_signal", {"place_label": "home"}),
        _enc_item("playback", {"now_playing": {"title": "晴天", "artist": "周杰伦"}}),
    ])
    service.app_open(UID, "Instagram", category="social")
    snap = service.snapshot(UID)
    assert set(snap.keys()) == {"fields", "encrypted", "recent_apps"}
    assert snap["fields"]["battery_level"] == "0.85"
    assert snap["fields"]["user_state"] == "default"
    for key in ("location_signal", "playback", "app"):
        assert snap["encrypted"][key]["envelope"]["body_ct"], key
        assert snap["encrypted"][key]["ts"]
    assert snap["recent_apps"][-1]["envelope"]["body_ct"]
    # no plaintext value leaks into the snapshot
    blob = json.dumps(snap, ensure_ascii=False)
    for sensitive in ("home", "晴天", "Instagram"):
        assert sensitive not in blob


def test_snapshot_ttl_drops_stale_encrypted(env):
    fake, _ = env
    service.ingest_snapshot(UID, [_enc_item("motion_state", {"motion_state": "running"})])
    assert "motion_state" in service.snapshot(UID)["encrypted"]
    fake.state[UID]["motion_state"]["ts"] = time.time() - 10_000   # > 300s ttl
    assert "motion_state" not in service.snapshot(UID)["encrypted"]


def test_wake_fallback_flat_shape(env):
    """Without an api_key/enclave the wake snapshot degrades to cleartext
    fields + null sensitive outputs — same flat shape, same null contract."""
    fake, _ = env
    service.ingest_snapshot(UID, [
        _item("battery", {"level": "0.85", "charging": "true"}),
        _enc_item("location_signal", {"place_label": "home"}),
    ])
    flat, err = wake.snapshot_for_wake(UID)
    assert err == "api_key_unavailable"
    assert flat["battery_level"] == "0.85"
    for f in ("place_label", "wifi_label", "country", "motion_state",
              "calendar_next_event", "now_playing", "app_name", "app_category"):
        assert flat[f] is None, f
    assert flat["recent_apps"] == []
    assert "home" not in json.dumps(flat)


# ---------------------------------------------------------------------------
# Wake triggering — client `changed` flag + debounce; no values in hints/events
# ---------------------------------------------------------------------------

def test_wake_debounce(env, monkeypatch):
    fake, wakes = env
    monkeypatch.setattr(service, "_app_proactive_settings", lambda uid: {})
    _ingest_location(UID, "home")    # wake
    _ingest_location(UID, "work")    # debounced (60s window)
    assert len([w for w in wakes if w[0] == "location"]) == 1


def test_changed_false_does_not_wake(env, monkeypatch):
    fake, wakes = env
    monkeypatch.setattr(service, "_app_proactive_settings", lambda uid: {})
    _ingest_location(UID, "home", changed=False)
    assert wakes == []
    assert "env" in fake.get_state(UID)["location_signal"]   # still stored


def test_wake_hint_and_events_carry_no_values(env, monkeypatch):
    fake, wakes = env
    monkeypatch.setattr(service, "_app_proactive_settings", lambda uid: {})
    _ingest_location(UID, "gym")
    assert len(wakes) == 1
    cap, hint = wakes[0]
    assert cap == "location" and "gym" not in hint
    for e in fake.read_events(UID):
        assert "old" not in e and "new" not in e
        assert "gym" not in json.dumps(e, ensure_ascii=False)


def test_local_only_encrypted_signal_rejected(env, monkeypatch):
    """local_only perception envelopes are rejected at validation: the agent
    can't read them (enclave has no key) AND persistent ones can't be rewrapped
    on key rotation. So they're never stored — and never wake (Codex P1, r5)."""
    fake, wakes = env
    monkeypatch.setattr(service, "_app_proactive_settings", lambda uid: {})
    item = {"key": "location_signal",
            "envelope": _mk_env({"values": {"place_label": "home"}}, visibility="local_only"),
            "changed": True}
    res = service.ingest_snapshot(UID, [item])
    assert res["location_signal"] == "rejected:envelope_must_be_shared"
    assert "location_signal" not in fake.state.get(UID, {})
    assert wakes == []


def test_items_local_only_envelope_rejected(env):
    """A persistent Tier 2 item with a local_only envelope is rejected (it could
    never be rewrapped, blocking key rotation forever) — Codex P1, r5."""
    fake, _ = env
    envd = _mk_env({"kind": "run"}, visibility="local_only")
    out, code = service.items_ingest(UID, "workout", [{"envelope": envd}])
    assert code == 400 and out["error"] == "envelope_must_be_shared"
    assert service.items_recent(UID, "workout")[0]["items"] == []


def test_wake_suppressed_when_perception_user_state_away(env, monkeypatch):
    fake, wakes = env
    monkeypatch.setattr(service, "_app_proactive_settings", lambda uid: {})
    uid = "u_away"
    service.ingest_snapshot(uid, [{"key": "user_state", "data": json.dumps("away")}])
    _ingest_location(uid, "gym")
    assert wakes == []
    events = fake.read_events(uid)
    assert any(e.get("type") == "suppressed" and e.get("reason") == "user_away"
               for e in events)
    assert not any(e.get("type") == "wake" for e in events)


def test_wake_suppressed_when_settings_disabled_or_dnd_or_away(env, monkeypatch):
    fake, wakes = env
    cases = [
        ({"enabled": False}, "proactive_disabled"),
        ({"enabled": True, "dnd": True}, "dnd_enabled"),
        ({"enabled": True, "dnd": False, "user_state": "away"}, "user_away"),
    ]
    for i, (settings, reason) in enumerate(cases):
        monkeypatch.setattr(service, "_app_proactive_settings", lambda uid, s=settings: s)
        uid = f"u_gate_{i}"
        _ingest_location(uid, "gym")
        assert wakes == [], f"case {reason}: wake should be suppressed"
        assert any(e.get("type") == "suppressed" and e.get("reason") == reason
                   for e in fake.read_events(uid)), f"case {reason}"


def test_wake_fires_normally_when_gate_open(env, monkeypatch):
    fake, wakes = env
    monkeypatch.setattr(service, "_app_proactive_settings", lambda uid: {"enabled": True})
    uid = "u_open"
    _ingest_location(uid, "gym")
    assert len(wakes) == 1
    assert any(e.get("type") == "wake" for e in fake.read_events(uid))


def test_wake_recovers_after_away_clears(env, monkeypatch):
    fake, wakes = env
    monkeypatch.setattr(service, "_app_proactive_settings", lambda uid: {})
    uid = "u_recover"
    service.ingest_snapshot(uid, [{"key": "user_state", "data": json.dumps("away")}])
    _ingest_location(uid, "gym")
    assert wakes == []
    service.ingest_snapshot(uid, [{"key": "user_state", "data": json.dumps("default")}])
    _ingest_location(uid, "home")
    assert len(wakes) == 1


def test_app_settings_failure_does_not_block_wake(env, monkeypatch):
    """app 不可达（如单测环境 import 失败）时不拦截——拦截是 best-effort。"""
    fake, wakes = env

    def boom(uid):
        raise RuntimeError("no app here")
    monkeypatch.setattr(service, "_app_proactive_settings", boom)
    uid = "u_no_app"
    _ingest_location(uid, "gym")
    assert len(wakes) == 1


# ---------------------------------------------------------------------------
# user_state via report
# ---------------------------------------------------------------------------

def test_report_user_state_key_sets_manual(env):
    fake, _ = env
    res = service.ingest_snapshot(UID, [
        {"key": "user_state", "data": '"focused"', "message": ""}])
    assert res["user_state"] == "accepted"
    assert service.snapshot(UID)["fields"]["user_state"] == "focused"


def test_report_user_state_stale_ignored(env):
    fake, _ = env
    service.ingest_snapshot(
        UID, [{"key": "user_state", "data": '"focused"', "message": ""}], client_ts=200.0)
    res = service.ingest_snapshot(
        UID, [{"key": "user_state", "data": '"away"', "message": ""}], client_ts=100.0)
    assert res["user_state"] == "stale_ignored"
    assert service.snapshot(UID)["fields"]["user_state"] == "focused"


def test_focus_mapping_ignores_server_config():
    """focus_map is no longer server-configurable (perception_config is gone).
    A leftover config override must NOT change the fixed default mapping
    (Codex P2: config rejected yet still read was the contradiction). Tested at
    the resolver — the single thing that changed."""
    from perception import resolve
    # default map: work -> focused; the legacy "away" override is ignored.
    assert resolve.resolve_focus({"focus": "work"}, {"focus_map": {"work": "away"}}) \
        == {"user_state": "focused"}
    # cleared focus -> default, override ignored.
    assert resolve.resolve_focus("none", {"focus_map": {"none": "away"}}) \
        == {"user_state": "default"}


# ---------------------------------------------------------------------------
# Photos — cleartext gate metadata + encrypted meta_envelope
# ---------------------------------------------------------------------------

def test_photo_hard_block_discards_ciphertext(env):
    """Hard-blocked scene (id_card): rejected; even an uploaded envelope is NOT
    stored — no frame envelope, not listed."""
    fake, _ = env
    out, code = service.photo_evaluate(
        UID, {"scene_hint": "id_card"}, {"id": "p_bad", "body_ct": "x"})
    assert code == 200 and out["usable"] is False and out["status"] == "rejected"
    assert fake.get_photo_envelope(UID, "p_bad") is None      # ciphertext discarded
    assert service.photos_recent(UID)[0]["photos"] == []


def test_photo_contextual_scene_stored_and_reaches_agent(env):
    fake, _ = env
    penv = _mk_env({"pixels": "c"}, item_id="p_priv")
    out, _ = service.photo_evaluate(
        UID, {"scene_hint": "private", "is_indoor": True}, penv)
    assert out["usable"] is True and out["status"] == "stored"
    assert out["metadata"]["scene_hint"] == "private"
    assert fake.get_photo_envelope(UID, "p_priv")["body_ct"] == penv["body_ct"]


def test_photo_one_step_store(env):
    fake, wakes = env
    penv = _mk_env({"pixels": "cipher"}, item_id="p_ok")
    out, code = service.photo_evaluate(
        UID, {"has_faces": "true", "scene_hint": "landscape"}, penv)
    assert code == 200 and out["status"] == "stored" and out["photo_id"] == "p_ok"
    listed = service.photos_recent(UID)[0]["photos"]
    assert len(listed) == 1 and listed[0]["photo_id"] == "p_ok"
    assert "envelope" not in listed[0]                       # no pixels in the list
    content, c2 = service.photo_content(UID, "p_ok")
    assert c2 == 200 and content["frame_id"] == "p_ok"
    assert fake.get_photo_envelope(UID, "p_ok")["body_ct"] == penv["body_ct"]  # in frame channel
    assert any(c == "photos" for c, _ in wakes)              # fired a wake


def test_photo_meta_envelope_passthrough(env):
    """place_label is resolved on-device and arrives ENCRYPTED in meta_envelope;
    the backend stores/serves it as ciphertext alongside cleartext gate metadata."""
    fake, _ = env
    menv = _mk_env({"place_label": "home"})
    penv = _mk_env({"pixels": "c"}, item_id="p_meta")
    out, code = service.photo_evaluate(
        UID, {"scene_hint": "food"}, penv, meta_envelope=menv)
    assert code == 200 and out["status"] == "stored"
    listed = service.photos_recent(UID)[0]["photos"][0]
    assert listed["meta_envelope"]["body_ct"] == menv["body_ct"]
    assert "home" not in json.dumps(listed.get("metadata") or {})
    content, _ = service.photo_content(UID, "p_meta")
    assert content["meta_envelope"]["body_ct"] == menv["body_ct"]


def test_photo_meta_envelope_owner_mismatch_400(env):
    fake, _ = env
    menv = _mk_env({"place_label": "home"}, owner="someone_else")
    out, code = service.photo_evaluate(
        UID, {"scene_hint": "food"}, {"id": "p_x", "body_ct": "c"}, meta_envelope=menv)
    assert code == 400 and out["error"] == "envelope_owner_mismatch"


def test_photo_usable_requires_envelope(env):
    fake, _ = env
    out, code = service.photo_evaluate(UID, {"scene_hint": "food"}, None)
    assert code == 400 and out["error"] == "content_envelope_required"


def test_photo_pixel_envelope_local_only_rejected(env):
    """A usable photo with a local_only pixel envelope must be REJECTED, not
    stored: it can't be rewrapped (the enclave can't decrypt it) and would land
    in the key-rotation inventory and block public-key rotation forever (Codex
    P1). The frame channel and item list stay empty."""
    fake, _ = env
    penv = _mk_env({"pixels": "x"}, visibility="local_only", item_id="p_local")
    out, code = service.photo_evaluate(UID, {"scene_hint": "food"}, penv)
    assert code == 400 and out["error"] == "envelope_must_be_shared"
    assert fake.get_photo_envelope(UID, "p_local") is None      # nothing stored
    assert service.photos_recent(UID)[0]["photos"] == []


def test_photo_pixel_envelope_owner_mismatch_rejected(env):
    """A pixel envelope owned by someone else must be rejected (same write-seam
    validation as meta_env / Tier 2 items)."""
    fake, _ = env
    penv = _mk_env({"pixels": "x"}, owner="someone_else", item_id="p_other")
    out, code = service.photo_evaluate(UID, {"scene_hint": "food"}, penv)
    assert code == 400 and out["error"] == "envelope_owner_mismatch"
    assert fake.get_photo_envelope(UID, "p_other") is None


def test_photo_metadata_string_bools(env):
    """默认字符串: is_screenshot as string 'false' must NOT be treated truthy."""
    fake, _ = env
    out, _ = service.photo_evaluate(
        UID, {"scene_hint": "food", "is_screenshot": "false"},
        _mk_env({"pixels": "c"}, item_id="p1"))
    assert out["usable"] is True                       # "false" not wrongly blocked
    out2, _ = service.photo_evaluate(
        UID, {"scene_hint": "food", "is_screenshot": "true"}, {"id": "p2", "body_ct": "c"})
    assert out2["usable"] is False                     # "true" blocks (rejected pre-storage)


def test_photo_suppressed_when_dnd_enabled(env, monkeypatch):
    fake, wakes = env
    monkeypatch.setattr(service, "_app_proactive_settings",
                        lambda uid: {"enabled": True, "dnd": True})
    uid = "u_photo_dnd"
    out, code = service.photo_evaluate(
        uid, {"scene_hint": "food"}, _mk_env({"pixels": "cipher"}, owner=uid, item_id="p_dnd"))
    assert code == 200 and out["status"] == "stored"   # gate affects wake, not storage
    assert wakes == []
    suppressed = [e for e in fake.read_events(uid)
                  if e.get("cap") == "photos" and e.get("type") == "suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0]["reason"] == "dnd_enabled"
    assert suppressed[0]["item"] == "p_dnd"


# ---------------------------------------------------------------------------
# Tier 2 collections — envelope mandatory
# ---------------------------------------------------------------------------

def test_items_require_envelope(env):
    fake, _ = env
    out, code = service.items_ingest(UID, "workout", [{"item_id": "w1"}])
    assert code == 400 and out["error"] == "envelope_required"
    out2, code2 = service.items_ingest(
        UID, "workout", [{"item_id": "w1", "doc": {"kind": "run", "minutes": 30}}])
    assert code2 == 400 and out2["error"] == "plaintext_doc_rejected_envelope_required"
    assert service.items_recent(UID, "workout")[0]["items"] == []


def test_items_envelope_ingest_and_ciphertext_list(env):
    fake, _ = env
    envd = _mk_env({"kind": "run", "minutes": 30})
    out, code = service.items_ingest(UID, "workout", [{"envelope": envd}])
    assert code == 200 and out["written"] == 1
    got, code = service.items_recent(UID, "workout")
    assert code == 200
    row = got["items"][0]
    assert row["item_id"] == envd["id"]                 # row id binds to envelope id
    assert row["envelope"]["body_ct"] == envd["body_ct"]
    assert "run" not in json.dumps(got)                 # no plaintext served


def test_items_envelope_owner_mismatch_400(env):
    fake, _ = env
    envd = _mk_env({"kind": "run"}, owner="someone_else")
    out, code = service.items_ingest(UID, "workout", [{"envelope": envd}])
    assert code == 400 and out["error"] == "envelope_owner_mismatch"


def test_items_rejects_photo_kind(env):
    """photo must NOT be ingestable via the generic /items endpoint (would bypass
    the _photo_usable gate). Only /photo/evaluate stores photos."""
    fake, _ = env
    out, code = service.items_ingest(UID, "photo", [
        {"item_id": "p_x", "envelope": _mk_env({"status": "confirmed"})}])
    assert code == 400 and out["error"] == "unknown_kind"
    assert service.photos_recent(UID)[0]["photos"] == []   # nothing leaked through


# ---------------------------------------------------------------------------
# app_open — server-side sealing (the iOS Shortcut can't encrypt)
# ---------------------------------------------------------------------------

def test_app_open_seals_current_and_history(env, sealer):
    fake, _ = env
    out, code = service.app_open(UID, "Instagram", category="social")
    assert code == 200 and out["app"] == "Instagram"
    st = fake.get_state(UID)
    assert "env" in st["app"] and "Instagram" not in str(st)
    opens = fake.read_app_opens(UID)
    assert "env" in opens[-1] and "Instagram" not in str(opens)
    snap = service.snapshot(UID)
    assert snap["encrypted"]["app"]["envelope"]["body_ct"]
    assert snap["recent_apps"][-1]["envelope"]["body_ct"]


def test_app_open_envelope_body_has_values_shape(env, monkeypatch):
    """The sealed body must carry values.{app_name,app_category} (so the enclave
    snapshot flattener populates the current-app fields) AND top-level
    app/category (so the recent_apps reader works) — one envelope, both
    consumers (Codex P2)."""
    fake, _ = env
    captured: dict = {}

    def seal(uid, plaintext):
        captured["body"] = json.loads(plaintext.decode("utf-8"))
        return build_envelope(plaintext=plaintext, owner_user_id=uid,
                              user_pk_bytes=USER_PK, enclave_pk_bytes=ENCLAVE_PK,
                              visibility="shared"), ""
    monkeypatch.setattr(service, "_seal_for_user", seal)

    service.app_open(UID, "Instagram", category="social")
    body = captured["body"]
    assert body["values"] == {"app_name": "Instagram", "app_category": "social"}
    assert body["app"] == "Instagram" and body["category"] == "social"


def test_app_open_encryption_unavailable_503(env, monkeypatch):
    """No user key / enclave info -> the event is DROPPED, never stored plaintext."""
    fake, _ = env
    monkeypatch.setattr(service, "_seal_for_user",
                        lambda uid, pt: (None, "user_content_public_key_missing"))
    out, code = service.app_open(UID, "Instagram", category="social")
    assert code == 503 and out["error"] == "encryption_unavailable"
    assert "app" not in fake.state.get(UID, {})
    assert fake.read_app_opens(UID) == []


def test_app_open_requires_app(env):
    fake, _ = env
    out, code = service.app_open(UID, "")
    assert code == 400 and out["error"] == "app_required"


def test_app_open_via_get_route(env, monkeypatch, sealer):
    """End-to-end: GET with everything (incl. key) in the URL query string."""
    import types
    from flask import Flask
    import perception.routes as routes

    fake, _ = env
    import accounts.auth as accounts_auth
    monkeypatch.setattr(accounts_auth, "require_user", lambda: types.SimpleNamespace(user_id=UID))

    app = Flask("t")
    app.register_blueprint(routes.bp)
    client = app.test_client()
    r = client.get("/v1/perception/app_open?key=APIKEY&app=Instagram&category=social&ts=1000")
    assert r.status_code == 200 and r.get_json()["app"] == "Instagram"
    assert "env" in fake.get_state(UID)["app"]


# ---------------------------------------------------------------------------
# /report route — multiplexing, error mapping, config 410
# ---------------------------------------------------------------------------

def _report_client(env, monkeypatch):
    import types
    from flask import Flask
    import perception.routes as routes
    fake, _ = env
    import accounts.auth as accounts_auth
    monkeypatch.setattr(accounts_auth, "require_user", lambda: types.SimpleNamespace(user_id=UID))
    app = Flask("t")
    app.register_blueprint(routes.bp)
    return fake, app.test_client()


def test_report_endpoint_encrypted_snapshot(env, monkeypatch):
    fake, client = _report_client(env, monkeypatch)
    r = client.post("/v1/perception/report", json={"context_snapshot": [
        _enc_item("motion_state", {"motion_state": {"state": "running"}}),
    ]})
    assert r.status_code == 200
    assert r.get_json()["results"]["motion_state"] == "accepted"
    assert "env" in fake.get_state(UID)["motion_state"]

    # missing context_snapshot -> 400
    bad = client.post("/v1/perception/report", json={"signals": {}})
    assert bad.status_code == 400


def test_report_plaintext_encrypted_signal_400_but_valid_items_applied(env, monkeypatch):
    fake, client = _report_client(env, monkeypatch)
    r = client.post("/v1/perception/report", json={"context_snapshot": [
        _item("battery", {"level": "0.5", "charging": "false"}),
        _item("motion_state", {"state": "running"}),       # plaintext -> rejected
    ]})
    assert r.status_code == 400
    body = r.get_json()["results"]
    assert body["motion_state"] == "rejected:plaintext_requires_envelope"
    assert body["battery"] == "accepted"                    # valid item still applied
    assert fake.get_state(UID)["battery_level"]["v"] == "0.5"
    assert "motion_state" not in fake.state.get(UID, {})


def test_report_config_gone_410(env, monkeypatch):
    fake, client = _report_client(env, monkeypatch)
    r = client.post("/v1/perception/report", json={"config": {
        "geofences": [{"label": "home", "lat": 37.0, "lon": -122.0, "radius_m": 150}]}})
    assert r.status_code == 410
    assert fake.config.get(UID) in (None, {})               # nothing stored


def test_report_multiplex_items(env, monkeypatch):
    fake, client = _report_client(env, monkeypatch)
    envd = _mk_env({"kind": "run", "minutes": 30})
    r = client.post("/v1/perception/report", json={"items": {
        "workout": [{"item_id": "w1", "envelope": envd}]}})
    assert r.status_code == 200
    assert r.get_json()["results"]["items"]["workout"]["written"] == 1
    got, code = service.items_recent(UID, "workout")
    assert code == 200 and got["items"][0]["envelope"]["body_ct"] == envd["body_ct"]


def test_report_empty_body_400(env, monkeypatch):
    fake, client = _report_client(env, monkeypatch)
    r = client.post("/v1/perception/report", json={"signals": {}})
    assert r.status_code == 400


def test_report_items_invalid_kind_400(env, monkeypatch):
    fake, client = _report_client(env, monkeypatch)
    r = client.post("/v1/perception/report", json={"items": {
        "photo": [{"item_id": "x", "envelope": _mk_env({"status": "confirmed"})}]}})
    assert r.status_code == 400
    assert r.get_json()["results"]["items"]["photo"]["error"] == "unknown_kind"


def test_report_empty_sections_400(env, monkeypatch):
    """空 section（[] / {}）不算 provided -> 400。"""
    fake, client = _report_client(env, monkeypatch)
    for body in ({"context_snapshot": []}, {"items": {}}, {"config": {}}):
        r = client.post("/v1/perception/report", json=body)
        assert r.status_code == 400, body


def test_report_items_malformed_400(env, monkeypatch):
    fake, client = _report_client(env, monkeypatch)
    for bad in ({"workout": {"a": 1}}, {"workout": [123]}, {"workout": "x"}):
        r = client.post("/v1/perception/report", json={"items": bad})
        assert r.status_code == 400, bad


def test_snapshot_route_serves_ciphertext_shape(env, monkeypatch):
    fake, client = _report_client(env, monkeypatch)
    service.ingest_snapshot(UID, [_enc_item("location_signal", {"place_label": "home"})])
    r = client.get("/v1/perception/snapshot")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body.keys()) == {"fields", "encrypted", "recent_apps"}
    assert body["encrypted"]["location_signal"]["envelope"]["body_ct"]
    assert "home" not in r.get_data(as_text=True)
