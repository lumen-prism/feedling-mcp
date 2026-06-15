"""Extended Perception business logic.

All the generic machinery driven by catalog.py:
  - ingest(): sparse, permission-gated report. Sensitive signals arrive as v1
    envelopes (encrypted on-device; plaintext rejected) and are stored
    ciphertext; operational signals (time/battery/broadcast/focus/user_state)
    stay cleartext for the wake gate and TTL logic. Wakes are triggered by the
    client's value-free `changed` flag, debounced as before.
  - snapshot(): current fresh state — cleartext operational fields resolved,
    encrypted signals returned as envelopes (the enclave or the device
    decrypts; the backend filters freshness off the cleartext ts only).
  - user_state with the Focus override/restore stack.
  - photo one-step flow (sensitivity gate on cleartext coarse metadata;
    pixels + sensitive context ciphertext).
  - generic collection ingest/read for Tier 2 (calendar / health), envelope-only.

No business logic lives in app.py. The only app.py coupling is a lazy import in
_fire_wake() to enqueue a proactive job (the existing wake mechanism).
"""
from __future__ import annotations

import logging
import json
import time
from datetime import datetime

from content_encryption import random_item_id

from . import catalog, resolve, store

log = logging.getLogger("perception.service")


def _now() -> float:
    return time.time()


_FUTURE_TS_TOLERANCE_SEC = 60.0  # allow minor client clock skew


def _coerce_ts(client_ts) -> float:
    """A report's logical time: its client_ts when present, else now. Using the
    record's own time makes batch/offline replay correct — per-field freshness
    and wake debounce are evaluated against when the signal actually happened.

    A FUTURE timestamp (clock skew, or milliseconds sent instead of seconds) is
    clamped to now: otherwise it would keep the snapshot permanently "fresh" and
    make the ordering guard reject every correctly-timestamped report until
    wall-clock catches up, freezing perception state."""
    now = _now()
    try:
        ts = float(client_ts)
    except (TypeError, ValueError):
        return now
    return now if ts > now + _FUTURE_TS_TOLERANCE_SEC else ts


# ---------------------------------------------------------------------------
# Permission gating
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Ingest (generic sparse report)
# ---------------------------------------------------------------------------

def _parse_data(data):
    """A context_snapshot item's `data` is a STRING: JSON, or "null"/""/None for
    "no value". Returns the parsed Python value, None for the null cases, or the
    raw string if it isn't valid JSON (lenient)."""
    if data is None:
        return None
    if isinstance(data, str):
        s = data.strip()
        if s == "" or s.lower() == "null":
            return None
        try:
            return json.loads(s)
        except Exception:
            return s
    return data  # already structured (lenient)


def ingest_snapshot(user_id: str, items: list, client_ts=None) -> dict:
    """Ingest a {context_snapshot:[...]} report. A plain item is {key, data,
    message} (`data` is a JSON string or "null"); an ENCRYPTED item is
    {key, envelope, changed} — the v1 envelope replaces `data`, and `changed`
    is the client's value-free change flag that drives wake triggering (the
    backend can't compare ciphertexts). Composite keys (e.g. device) expand
    into their sub-signals; aliases (location -> location_signal) normalize."""
    pairs: list[tuple] = []  # (input_name, value, message, envelope, changed)
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        key = str(it.get("key") or "").strip()
        if not key:
            continue
        msg = it.get("message")
        # Pass the RAW envelope (don't collapse a malformed non-dict to None
        # here) so _apply can reject a malformed encrypted payload instead of
        # silently treating it as a null report that clears the signal.
        envelope = it.get("envelope")
        changed = _truthy(it.get("changed"))
        data_present = "data" in it
        value = _parse_data(it.get("data"))
        if key in catalog.COMPOSITE_KEYS:
            subs = catalog.COMPOSITE_KEYS[key]
            if isinstance(value, dict):
                for sub in subs:
                    if sub in value:
                        pairs.append((sub, value[sub], msg, None, False, True))
            elif value is None:
                for sub in subs:
                    pairs.append((sub, None, msg, None, False, data_present))
            continue
        pairs.append((key, value, msg, envelope, changed, data_present))
    return _apply(user_id, pairs, client_ts)


def ingest(user_id: str, signals: dict, client_ts: float | None = None) -> dict:
    """Back-compat / internal: ingest a flat {key: value} map (no messages)."""
    return _apply(user_id, [(k, v, None, None, False) for k, v in (signals or {}).items()],
                  client_ts)


def _cell(value, ts: float, msg) -> dict:
    cell = {"v": value, "ts": ts}
    if msg is not None:
        cell["msg"] = msg
    return cell


_ENVELOPE_REQUIRED_FIELDS = ("body_ct", "nonce", "K_user", "visibility", "owner_user_id")


def _envelope_error(env: dict, user_id: str) -> str:
    """Validate a v1 envelope on a perception write path: required fields,
    owner binding, and visibility. Returns "" when valid.

    Perception envelopes MUST be `shared` (K sealed to the enclave too). Unlike
    chat/memory, a local_only perception value is useless — the agent can never
    read it (the enclave can't decrypt it) — AND it's a trap: the persistent
    rows (Tier 2 items, photo metadata) can't be rewrapped on key rotation, so
    one local_only upload would block rotation forever. So we reject local_only
    here at the single write-validation seam rather than store an unrewrappable,
    unreadable row."""
    missing = [f for f in _ENVELOPE_REQUIRED_FIELDS if not env.get(f)]
    if missing:
        return "envelope_missing_fields:" + ",".join(missing)
    # v MUST be the supported integer version. A non-integer v (e.g. "x" or a
    # list) passes the field checks but later crashes the enclave at
    # int(env.get("v")) with an UNCAUGHT ValueError -> 500 on the snapshot read,
    # and a persistent collection item would stay unreadable indefinitely. Pin
    # it to v1 here at the write seam (bools are ints in Python -> exclude them).
    v = env.get("v", 1)
    if isinstance(v, bool) or not isinstance(v, int) or v != 1:
        return "envelope_unsupported_version"
    if env.get("visibility") != "shared":
        return "envelope_must_be_shared"
    if str(env.get("owner_user_id")) != str(user_id):
        return "envelope_owner_mismatch"
    if not env.get("K_enclave"):
        return "envelope_missing_fields:K_enclave"
    return ""


def _apply(user_id: str, pairs: list, client_ts=None) -> dict:
    now = _coerce_ts(client_ts)
    config = store.get_config(user_id)
    prev_state = store.get_state(user_id)
    results: dict[str, str] = {}
    patch: dict[str, dict] = {}          # field -> candidate cell (ts-guarded on write)
    input_fields: dict[str, list] = {}   # input_name -> output fields it proposed
    wake_pending: list[tuple] = []       # (cap_key, debounce, field, old, new)

    for input_name, value, msg, envelope, changed, *rest in pairs:
        # 6th element (optional, back-compat): whether `data` was explicitly
        # supplied in the item. None of the legacy 5-tuple callers carry it, so
        # default True (data present) to preserve their behavior.
        data_present = rest[0] if rest else True
        if input_name in catalog.IGNORED_KEYS:
            results[input_name] = "ignored"  # e.g. "unsupported" (all-null placeholder)
            continue
        # Manual user_state arrives as an explicit report key (folds POST /user_state).
        # The store applies it atomically under a row lock with a ts guard, so a
        # late/concurrent older report can't overwrite a newer manual value.
        if input_name == "user_state":
            doc = store.set_manual_user_state_guarded(
                user_id, value if value is not None else "default", now)
            results[input_name] = "accepted" if doc.get("manual_ts") == now else "stale_ignored"
            continue
        key = catalog.KEY_ALIASES.get(input_name, input_name)
        sig = catalog.SIGNALS.get(key)
        if sig is None:
            results[input_name] = "unknown_signal"
            continue

        # Focus drives the user_state override stack (value None -> clear).
        if sig.capability == "focus":
            _apply_focus(user_id, value, config)
            results[input_name] = "accepted"
            continue

        # Encrypted signals: the value travels as a v1 envelope; the backend
        # stores it verbatim under the SIGNAL key ({"env",ts} cell — the ts
        # guard and TTL work off the cleartext ts) and never sees the value.
        # Wake triggering uses the client's value-free `changed` flag; the
        # debounce window still caps the wake rate even if a client always
        # claims changed. Plaintext `data` for these signals is rejected.
        if sig.encrypted:
            if envelope is not None:
                if not isinstance(envelope, dict):
                    # Malformed payload (string/list/…). Reject — do NOT fall
                    # through to the null path, which would clear valid state.
                    results[input_name] = "rejected:malformed_envelope"
                    continue
                err = _envelope_error(envelope, user_id)
                if err:
                    results[input_name] = f"rejected:{err}"
                    continue
                patch[key] = {"env": envelope, "ts": now}
                input_fields[input_name] = [key]
                cap = catalog.CAPABILITIES[sig.capability]
                # Only wake on SHARED envelopes: the enclave can't decrypt
                # local_only, so the agent would wake to null fields — a
                # contextless (and potentially misleading) proactive ping.
                if (cap.wake_source and sig.significant and changed
                        and envelope.get("visibility") == "shared"):
                    wake_pending.append((sig.capability, cap.debounce_sec, key))
            elif data_present and value is None:
                # explicit data:"null" -> unavailable now (no permission / no
                # value); the message here is operational copy, not a sensitive
                # value. Only an EXPLICIT null clears the cell.
                patch[key] = _cell(None, now, msg)
                input_fields[input_name] = [key]
            elif not data_present:
                # Neither an envelope nor a `data` field: a malformed item (e.g.
                # {"key":"location_signal"}). Encrypted signals are envelope-
                # mandatory — do NOT silently clear a valid encrypted cell.
                results[input_name] = "rejected:envelope_required"
            else:
                results[input_name] = "rejected:plaintext_requires_envelope"
            continue

        fields: list[str] = []
        if value is None:
            # data:"null" -> field unavailable now; record null + message. No wake.
            for fname in sig.outputs:
                patch[fname] = _cell(None, now, msg)
                fields.append(fname)
        else:
            # Resolve raw -> state fields, or store as-is.
            if sig.resolver:
                fn = resolve.RESOLVERS.get(sig.resolver)
                resolved = fn(value, config) if fn else {}
            else:
                resolved = {sig.outputs[0]: value}
            cap = catalog.CAPABILITIES[sig.capability]
            for fname in sig.outputs:
                if fname not in resolved:
                    continue
                new_v = resolved[fname]
                patch[fname] = _cell(new_v, now, msg)
                fields.append(fname)
                old = (prev_state.get(fname) or {}).get("v")
                if cap.wake_source and sig.significant and new_v != old:
                    wake_pending.append((sig.capability, cap.debounce_sec, fname))
        input_fields[input_name] = fields

    # Atomic ts-guarded write under a row lock: a field is persisted only if its
    # new ts >= the currently-stored ts. Doing the compare-and-write atomically
    # (not against the pre-read prev_state) is what makes the "late older record
    # never clobbers a newer value" guarantee hold under concurrent reports.
    written = store.merge_state_guarded(user_id, patch) if patch else set()

    for input_name, fields in input_fields.items():
        results[input_name] = "accepted" if any(f in written for f in fields) else "stale_ignored"

    # device "back after a long lock" wake (only if the field was actually written).
    _maybe_unlock_wake(user_id, patch, written, prev_state, now, wake_pending)

    for (capk, debounce, f) in wake_pending:
        if f in written:
            _maybe_wake(user_id, capk, debounce, f, now)

    return results


def _apply_focus(user_id: str, value, config: dict) -> None:
    """Maintain the {manual, focus_override} stack. An active Focus overrides the
    manual user_state; clearing Focus (none/null) restores the manual value.
    `value` may be a string, a dict ({ios_focus|focus|state:..}), or None."""
    if isinstance(value, dict):
        raw = value.get("ios_focus") or value.get("focus") or value.get("state")
    else:
        raw = value
    raw = (str(raw) if raw is not None else "none").strip().lower()
    doc = store.get_user_state_doc(user_id)
    doc.setdefault("manual", doc.get("manual") or "default")
    if raw in ("", "none"):
        doc["focus_override"] = None
    else:
        doc["focus_override"] = resolve.resolve_focus(value, config).get("user_state")
    store.set_user_state_doc(user_id, doc)


def _maybe_unlock_wake(user_id, patch, written, prev_state, now, wake_candidates) -> None:
    if "last_unlock_ago_sec" not in written:
        return
    cell = patch.get("last_unlock_ago_sec")
    if not cell or cell.get("v") is None:
        return
    try:
        new = float(cell["v"])
    except (TypeError, ValueError):
        return
    prev = (prev_state.get("last_unlock_ago_sec") or {}).get("v")
    try:
        was_long = prev is None or float(prev) >= catalog.UNLOCK_BACK_THRESHOLD_SEC
    except (TypeError, ValueError):
        was_long = True
    if new <= 60 and was_long:
        wake_candidates.append(("device", 0.0, "last_unlock_ago_sec"))


# ---------------------------------------------------------------------------
# Wake triggering (debounced; reuses the existing proactive-job mechanism)
# ---------------------------------------------------------------------------

def _app_proactive_settings(user_id: str) -> dict:
    """Best-effort read of the app-level proactive settings (enabled/dnd/
    user_state). Lazy import like _fire_wake; failures mean "no block" so a
    broken app layer can't silently kill perception observability."""
    from core import store as core_store  # lazy; assembly loads core first
    return core_store.get_store(user_id).load_proactive_settings()


def _wake_block_reason(user_id: str) -> str:
    """Mechanical gate for AUTOMATIC perception wakes, mirroring the tick
    path's enabled/dnd/away suppression (app.py
    _build_proactive_v2_wake_decision). Perception wakes are all automatic —
    manual summons go through the tick path — so there is no manual bypass.
    Away is honored from EITHER source: the app proactive settings (iOS
    Proactive panel) or perception's own focus/manual stack."""
    try:
        settings = _app_proactive_settings(user_id) or {}
    except Exception as e:
        log.warning("wake gate settings read failed for %s: %s", user_id, e)
        settings = {}
    if settings and not settings.get("enabled", True):
        return "proactive_disabled"
    if settings.get("dnd", False):
        return "dnd_enabled"
    if str(settings.get("user_state") or "") == "away":
        return "user_away"
    if effective_user_state(user_id) == "away":
        return "user_away"
    return ""


def _last_wake_ts(user_id: str, cap_key: str) -> float:
    for ev in reversed(store.read_events(user_id, limit=50)):
        if ev.get("cap") == cap_key and ev.get("type") == "wake":
            return float(ev.get("ts") or 0)
    return 0.0


def _maybe_wake(user_id, cap_key, debounce, field, now) -> None:
    # Event docs and wake hints carry NO values (old/new) — perception values
    # are encrypted at rest, and the audit trail / proactive job hint must not
    # leak them. The agent reads actual values from the decrypted snapshot.
    block = _wake_block_reason(user_id)
    if block:
        store.append_event(user_id, {
            "cap": cap_key, "type": "suppressed", "reason": block,
            "field": field, "ts": now,
        }, now)
        return
    if debounce and (now - _last_wake_ts(user_id, cap_key)) < debounce:
        store.append_event(user_id, {
            "cap": cap_key, "type": "debounced", "field": field, "ts": now,
        }, now)
        return
    store.append_event(user_id, {
        "cap": cap_key, "type": "wake", "field": field, "ts": now,
    }, now)
    _fire_wake(user_id, cap_key, _wake_hint(cap_key, field), now)


def _wake_hint(cap_key: str, field: str) -> str:
    if cap_key == "location":
        return "她到了一个新地方（具体地点标签见 perception 快照）。"
    if cap_key == "wifi":
        return "她换了 Wi-Fi 环境（见 perception 快照）。"
    if cap_key == "app":
        return "她切换了应用（见 perception 快照）。"
    if cap_key == "motion":
        return "她的运动状态变了（见 perception 快照）。"
    if cap_key == "device" and field == "last_unlock_ago_sec":
        return "她长时间锁屏后刚刚解锁——拿起手机回来了。"
    if cap_key == "region":
        return "她到了一个新的国家或地区——一个明确的'今天联系一下'时刻。"
    return f"{cap_key} 发生了变化：{field}。"


def _fire_wake(user_id: str, cap_key: str, hint: str, now: float) -> None:
    """Enqueue a proactive job so the resident agent wakes. Lazy-imports app to
    avoid an import cycle (app registers this module at the bottom of startup)."""
    try:
        from core import store as core_store  # lazy
        from core import util as core_util  # lazy
        from proactive import service as proactive_service  # lazy
        s = core_store.get_store(user_id)
        job = {
            "job_id": core_util._new_public_id("pj"),
            "ts": now,
            "created_at": datetime.fromtimestamp(now).isoformat(),
            "source": proactive_service.PROACTIVE_JOB_SOURCE,
            "status": "pending",
            "intent_label": f"perception_{cap_key}"[:120],
            # trigger/wake_kind keep the V2 job schema consistent with the
            # tick path so consumers and the wake dashboard see one shape.
            "trigger": f"perception_{cap_key}"[:120],
            "wake_kind": "presence",
            "context_hint": hint[:2000],
            "connections": [],
            "connection": {},
            "frame_ids": [],
            "device_event_ids": [],
            "current_app": "",
        }
        s.append_proactive_job(job)
    except Exception as e:
        log.error("fire_wake(%s,%s) failed: %s", user_id, cap_key, e)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def snapshot(user_id: str, now: float | None = None) -> dict:
    """Ciphertext-shape snapshot — the backend filters freshness off the
    CLEARTEXT ts only and never decrypts:
      - fields    : cleartext operational fields, TTL-filtered (stale -> null).
      - encrypted : fresh encrypted signal cells {signal: {envelope, ts}};
        stale or null-reported signals simply don't appear (consumers null
        their output fields).
      - recent_apps : ciphertext app-open events [{envelope, ts}], newest last.
    The enclave (agent path) or the device (K_user) flattens this into the
    agent-facing flat snapshot."""
    now = now or _now()
    state = store.get_state(user_id)
    fields: dict = {}
    encrypted: dict = {}
    for sig in catalog.SIGNALS.values():
        cap = catalog.CAPABILITIES.get(sig.capability)
        if not cap or not cap.context_field:
            continue
        if sig.encrypted:
            cell = state.get(sig.input)
            if (isinstance(cell, dict) and isinstance(cell.get("env"), dict)
                    and (now - float(cell.get("ts") or 0)) <= sig.ttl_sec):
                encrypted[sig.input] = {"envelope": cell["env"], "ts": cell.get("ts")}
            continue
        for f in sig.outputs:
            if f == "user_state":
                continue  # owned by the focus/manual stack, set below
            cell = state.get(f)
            if not isinstance(cell, dict):
                fields[f] = None
                continue
            if (now - float(cell.get("ts") or 0)) > sig.ttl_sec:
                fields[f] = None  # stale -> agent treats as "don't infer"
            else:
                fields[f] = cell.get("v")  # null cell -> None (= no permission now)
    # user_state is always present (manual default if nothing set).
    fields["user_state"] = effective_user_state(user_id)
    # recent_apps folds the old /app_usage read; capped to keep snapshot small.
    recent_apps = [
        {"envelope": ev["env"], "ts": ev.get("ts")}
        for ev in store.read_app_opens(user_id, limit=catalog.RECENT_APPS_LIMIT)
        if isinstance(ev, dict) and isinstance(ev.get("env"), dict)
    ]
    return {"fields": fields, "encrypted": encrypted, "recent_apps": recent_apps}


# ---------------------------------------------------------------------------
# user_state
# ---------------------------------------------------------------------------

def _effective_from_doc(doc: dict) -> str:
    return doc.get("focus_override") or doc.get("manual") or "default"


def effective_user_state(user_id: str) -> str:
    return _effective_from_doc(store.get_user_state_doc(user_id))


def set_manual_user_state(user_id: str, value: str, ts: float | None = None) -> str:
    """Set the manual user_state, ts-guarded ATOMICALLY in the store (a write
    older than the stored manual_ts is dropped under a row lock, so a late/
    concurrent report can't clobber a newer manual value). ts=None means now."""
    ts = _now() if ts is None else float(ts)
    return _effective_from_doc(store.set_manual_user_state_guarded(user_id, value, ts))


# ---------------------------------------------------------------------------
# Photos (two-step, sensitivity-gated)
# ---------------------------------------------------------------------------

def _truthy(v) -> bool:
    """Coerce a bool that may have arrived as a string ("默认字符串"). "false"/
    "0"/"" -> False."""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def _photo_usable(meta: dict) -> tuple[bool, bool, str]:
    """Two-layer sensitivity gate — metadata only, no pixel decryption. The
    platform HARD-blocks only objectively-sensitive scenes; contextual ones
    (private/receipt) pass and the agent self-censors. Returns
    (usable, sensitive, reason)."""
    scene = str(meta.get("scene_hint") or "").lower()
    if scene in catalog.HARD_BLOCK_SCENES:
        return False, True, f"hard_block:{scene}"
    if _truthy(meta.get("is_screenshot")):
        return False, False, "screenshot"
    return True, False, ""


def photo_evaluate(user_id: str, metadata: dict,
                   content_envelope: dict | None = None,
                   meta_envelope: dict | None = None) -> tuple[dict, int]:
    """Single-step photo ingest: evaluate metadata AND (if usable) store the
    encrypted image in one call.

    - Hard-blocked photos (id_card/medical/document/screenshot) are REJECTED and
      their ciphertext — even if uploaded — is discarded: never stored, never
      reaches the agent.
    - Usable photos: the ciphertext goes into the screen-frame envelope channel
      (reuses the enclave's existing frame-decrypt path); the backend never sees
      plaintext. frame_id == photo_id == content_envelope.id.
    - `metadata` (scene_hint etc.) stays CLEARTEXT — coarse enums/bools that the
      hard-block gate must read synchronously. Sensitive context (place_label,
      resolved on-device from EXIF GPS — raw coordinates never leave the phone)
      arrives in the optional `meta_envelope` and is stored/passed ciphertext.
    """
    now = _now()
    metadata = metadata or {}
    if meta_envelope is not None:
        err = _envelope_error(meta_envelope, user_id)
        if err:
            return {"error": err}, 400
    usable, sensitive, reason = _photo_usable(metadata)
    photo_id = str((content_envelope or {}).get("id") or random_item_id())
    meta_out = {k: metadata.get(k) for k in catalog.PHOTO_METADATA_FIELDS}

    if not usable:
        # Hard-blocked: do NOT store anything; any uploaded ciphertext is dropped.
        return {"photo_id": photo_id, "metadata": meta_out, "usable": False,
                "sensitive": sensitive, "reason": reason, "status": "rejected"}, 200

    if not content_envelope:
        return {"error": "content_envelope_required"}, 400

    # Validate the pixel envelope the SAME way as meta_env / Tier 2 items: it's
    # a persistent row that lands in the key-rotation rewrap inventory, so a
    # local_only / enclave-keyless / owner-mismatched envelope is a trap — it
    # can't be rewrapped and would block public-key rotation forever. Reject it
    # at this single write seam rather than store an unrewrappable row.
    err = _envelope_error(content_envelope, user_id)
    if err:
        return {"error": err}, 400

    # Store ciphertext in the frame channel + metadata as a confirmed item.
    store.put_photo_envelope(user_id, photo_id, now, content_envelope)
    doc = {"photo_id": photo_id, "metadata": meta_out, "status": "confirmed",
           "usable": True, "sensitive": False, "frame_id": photo_id}
    if meta_envelope is not None:
        doc["meta_env"] = meta_envelope
    store.item_upsert(user_id, "photo", photo_id, now, doc, expires_at=None)

    # Burst de-dup backstop: only wake once per cluster window.
    block = _wake_block_reason(user_id)
    if block:
        store.append_event(user_id, {"cap": "photos", "type": "suppressed",
                                     "reason": block, "item": photo_id,
                                     "ts": now}, now)
    elif (now - _last_wake_ts(user_id, "photos")) >= catalog.PHOTO_CLUSTER_SEC:
        store.append_event(user_id, {"cap": "photos", "type": "wake",
                                     "item": photo_id, "ts": now}, now)
        _fire_wake(user_id, "photos",
                   "她拍了一张可能值得一提的照片（先看元数据，需要再 pull 内容）。", now)
    return {"photo_id": photo_id, "metadata": meta_out, "usable": True,
            "sensitive": False, "status": "stored"}, 200


def photos_recent(user_id: str, limit: int = 20) -> tuple[dict, int]:
    now = _now()
    items = [i for i in store.item_list(user_id, "photo", limit=limit, now=now)
             if i.get("status") == "confirmed"]
    out = []
    for i in items:
        row = {"photo_id": i.get("photo_id"), "metadata": i.get("metadata")}
        if i.get("meta_env"):
            row["meta_envelope"] = i["meta_env"]  # ciphertext passthrough
        out.append(row)
    return {"photos": out}, 200


def photo_content(user_id: str, photo_id: str) -> tuple[dict, int]:
    """Permission + status gate for one confirmed photo. Returns metadata and the
    frame_id; the caller decrypts pixels via the enclave's existing
    /v1/screen/frames/<frame_id>/decrypt path. The backend never holds plaintext
    pixels — only the enclave decrypts."""
    now = _now()
    doc = store.item_get(user_id, "photo", photo_id, now=now)
    if not doc or doc.get("status") != "confirmed":
        return {"error": "not_found"}, 404
    out = {
        "photo_id": photo_id,
        "frame_id": doc.get("frame_id") or photo_id,
        "metadata": doc.get("metadata"),
        "decrypt_path": f"/v1/screen/frames/{doc.get('frame_id') or photo_id}/decrypt",
    }
    if doc.get("meta_env"):
        out["meta_envelope"] = doc["meta_env"]
    return out, 200


# ---------------------------------------------------------------------------
# Tier 2 collections (calendar / health) — generic
# ---------------------------------------------------------------------------

def items_ingest(user_id: str, kind: str, items: list[dict]) -> tuple[dict, int]:
    """Each item is {item_id?, ts?, expires_at?, envelope} — the v1 envelope
    wraps the whole doc (v1 encryption is mandatory; a plaintext `doc` is
    rejected). Cleartext index columns (item_id/ts/expires_at) stay for
    listing and GC; the value is only readable by the enclave or the device."""
    cap = catalog.KIND_CAPABILITY.get(kind)
    if not cap:
        return {"error": "unknown_kind"}, 400
    if not isinstance(items, list) or not all(isinstance(it, dict) for it in items):
        return {"error": "invalid_items"}, 400
    for it in items:
        if it.get("doc"):
            return {"error": "plaintext_doc_rejected_envelope_required"}, 400
        env = it.get("envelope")
        if not isinstance(env, dict):
            return {"error": "envelope_required"}, 400
        err = _envelope_error(env, user_id)
        if err:
            return {"error": err}, 400
    now = _now()
    wrote = 0
    for it in items:
        env = it["envelope"]
        iid = str(it.get("item_id") or env.get("id") or random_item_id())
        ts = float(it.get("ts") or now)
        store.item_upsert(user_id, kind, iid, ts, {"env": env}, it.get("expires_at"))
        wrote += 1
    return {"written": wrote}, 200


def items_recent(user_id: str, kind: str, limit: int = 20) -> tuple[dict, int]:
    """Ciphertext list: [{item_id, ts, expires_at, envelope}]. The enclave's
    /v1/perception/items/<kind> (agent path) or the device decrypts."""
    cap = catalog.KIND_CAPABILITY.get(kind)
    if not cap:
        return {"error": "unknown_kind"}, 400
    now = _now()
    items = []
    for row in store.item_list_rows(user_id, kind, limit=limit, now=now):
        doc = row.get("doc") or {}
        if not isinstance(doc.get("env"), dict):
            continue  # pre-encryption legacy row (wiped by migration 0004)
        items.append({"item_id": row.get("item_id"), "ts": row.get("ts"),
                      "expires_at": row.get("expires_at"), "envelope": doc["env"]})
    return {"items": items}, 200


# ---------------------------------------------------------------------------
# App usage (iOS Shortcut GET endpoint) — "what app at what time"
# ---------------------------------------------------------------------------

def _seal_for_user(user_id: str, plaintext: bytes) -> tuple[dict | None, str]:
    """Server-side shared-envelope build (lazy app-layer imports, same pattern
    as _app_proactive_settings). Used by paths whose client can't encrypt — the
    iOS Shortcut GET. Plaintext exists transiently in backend memory; only the
    envelope is persisted."""
    from core import store as core_store        # lazy; assembly loads core first
    from core import envelope as core_envelope  # lazy
    return core_envelope._build_shared_envelope_for_store(
        core_store.get_store(user_id), plaintext)


def app_open(user_id: str, app: str, category: str | None = None,
             client_ts=None) -> tuple[dict, int]:
    """Record one app-open event (fired by an iOS Shortcut when the user opens an
    app). The Shortcut can't do ChaCha20, so the backend seals {app, category}
    server-side and persists ONLY ciphertext (snapshot cell + usage time series).
    If encryption material is unavailable (no user public key / enclave info),
    the event is dropped with a 503 — never stored plaintext."""
    app = (app or "").strip()
    if not app:
        return {"error": "app_required"}, 400
    now = _coerce_ts(client_ts)
    category = (category or "").strip() or None
    # Body carries BOTH shapes from one envelope:
    #   - values.{app_name,app_category}: the enclave snapshot flattener reads
    #     body["values"][<output field>] for the "app" signal (outputs are
    #     app_name/app_category), so the current-app fields populate.
    #   - top-level app/category/ts: the enclave recent_apps reader reads these.
    body = {
        "values": {"app_name": app, "app_category": category},
        "app": app, "category": category, "ts": now,
    }
    try:
        env, err = _seal_for_user(user_id, json.dumps(body).encode("utf-8"))
    except Exception as e:
        env, err = None, f"seal_failed:{type(e).__name__}"
    if env is None:
        log.warning("app_open(%s) encryption unavailable: %s", user_id, err)
        return {"error": "encryption_unavailable", "detail": err}, 503
    # current app -> snapshot (ts-guarded, one envelope cell under the signal key)
    store.merge_state_guarded(user_id, {"app": {"env": env, "ts": now}})
    # append to the usage time series (same envelope: same value, same owner)
    store.append_app_open(user_id, {"env": env, "ts": now}, now)
    return {"status": "ok", "app": app, "category": category, "ts": now}, 200


