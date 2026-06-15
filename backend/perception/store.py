"""Self-contained DB access for Extended Perception.

Keeps all perception SQL inside the feature module (db.py is untouched). Reuses
the shared connection pool from db.get_pool(). Singletons live in user_blobs
(atomic shallow-merge upsert); collections live in the perception_items table
added by migration 0002; the wake/change audit trail uses user_logs.
"""
from __future__ import annotations

import logging

from psycopg.types.json import Jsonb

from db import (
    get_pool, log_append, log_read, log_read_all, log_patch_item,
    frame_upsert, frame_get, frame_delete,
)

log = logging.getLogger("perception.store")

# user_blobs kinds (singletons, one row per user)
STATE = "perception_state"            # plain cell {field:{"v",ts}} or encrypted {signal:{"env",ts}}
CONFIG = "perception_config"          # legacy (focus_map only); geofences/ssid_labels live on-device now
USER_STATE = "perception_user_state"  # {"manual": "default", "focus_override": None}

EVENT_STREAM = "perception_events"


# ---------------------------------------------------------------------------
# Singleton blobs (user_blobs)
# ---------------------------------------------------------------------------

def _get_blob(user_id: str, kind: str) -> dict:
    try:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT doc FROM user_blobs WHERE user_id = %s AND kind = %s",
                (user_id, kind),
            ).fetchone()
        return row[0] if row and isinstance(row[0], dict) else {}
    except Exception as e:
        log.error("get_blob(%s,%s) failed: %s", user_id, kind, e)
        return {}


def _merge_blob(user_id: str, kind: str, patch: dict) -> dict:
    """Atomic shallow-merge: existing doc || patch (patch wins per top-level key).
    Inserts the row if absent. Returns the merged doc."""
    if not patch:
        return _get_blob(user_id, kind)
    try:
        with get_pool().connection() as conn:
            row = conn.execute(
                "INSERT INTO user_blobs (user_id, kind, doc) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, kind) DO UPDATE "
                "SET doc = user_blobs.doc || EXCLUDED.doc RETURNING doc",
                (user_id, kind, Jsonb(patch)),
            ).fetchone()
        return row[0] if row else patch
    except Exception as e:
        log.error("merge_blob(%s,%s) failed: %s", user_id, kind, e)
        return _get_blob(user_id, kind)


def _set_blob(user_id: str, kind: str, doc: dict) -> None:
    try:
        with get_pool().connection() as conn:
            conn.execute(
                "INSERT INTO user_blobs (user_id, kind, doc) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, kind) DO UPDATE SET doc = EXCLUDED.doc",
                (user_id, kind, Jsonb(doc)),
            )
    except Exception as e:
        log.error("set_blob(%s,%s) failed: %s", user_id, kind, e)


def _delete_state_keys(user_id: str, keys: list[str]) -> None:
    """Remove specific top-level keys from perception_state (used when a
    capability is turned off — its fields must vanish from the snapshot)."""
    if not keys:
        return
    try:
        with get_pool().connection() as conn:
            # doc - ARRAY[...]  removes each key from the JSONB object
            conn.execute(
                "UPDATE user_blobs SET doc = doc - %s::text[] "
                "WHERE user_id = %s AND kind = %s",
                (list(keys), user_id, STATE),
            )
    except Exception as e:
        log.error("delete_state_keys(%s) failed: %s", user_id, e)


# typed accessors
def get_state(user_id: str) -> dict:
    return _get_blob(user_id, STATE)


def merge_state(user_id: str, patch: dict) -> dict:
    return _merge_blob(user_id, STATE, patch)


def merge_state_guarded(user_id: str, patch: dict) -> set:
    """Atomically merge per-field cells into perception_state under a ROW LOCK,
    applying a per-field timestamp guard: a field is written only if its new ts
    >= the currently-stored ts. Returns the set of field names actually written.

    The compare-and-write happens inside one locked transaction, so concurrent
    reports for the same user can't both read the old value and have the older
    one win — a late older record can never clobber a newer value."""
    if not patch:
        return set()
    written: set = set()
    try:
        with get_pool().connection() as conn:
            with conn.transaction():
                # Ensure the row exists, then lock it (FOR UPDATE serializes
                # concurrent writers on this user's state row).
                conn.execute(
                    "INSERT INTO user_blobs (user_id, kind, doc) VALUES (%s, %s, '{}'::jsonb) "
                    "ON CONFLICT (user_id, kind) DO NOTHING",
                    (user_id, STATE),
                )
                row = conn.execute(
                    "SELECT doc FROM user_blobs WHERE user_id = %s AND kind = %s FOR UPDATE",
                    (user_id, STATE),
                ).fetchone()
                merged = dict(row[0]) if row and isinstance(row[0], dict) else {}
                for field, cell in patch.items():
                    existing = merged.get(field)
                    old_ts = existing.get("ts") if isinstance(existing, dict) else None
                    new_ts = cell.get("ts")
                    if old_ts is None or new_ts is None or float(new_ts) >= float(old_ts):
                        merged[field] = cell
                        written.add(field)
                conn.execute(
                    "UPDATE user_blobs SET doc = %s WHERE user_id = %s AND kind = %s",
                    (Jsonb(merged), user_id, STATE),
                )
        return written
    except Exception as e:
        log.error("merge_state_guarded(%s) failed: %s", user_id, e)
        return set()


def clear_state_fields(user_id: str, fields: list[str]) -> None:
    _delete_state_keys(user_id, fields)


def get_config(user_id: str) -> dict:
    return _get_blob(user_id, CONFIG)


def get_user_state_doc(user_id: str) -> dict:
    return _get_blob(user_id, USER_STATE)


def set_user_state_doc(user_id: str, doc: dict) -> None:
    _set_blob(user_id, USER_STATE, doc)


def set_manual_user_state_guarded(user_id: str, value: str, ts: float) -> dict:
    """Atomically set the manual user_state under a ROW LOCK with a ts guard: the
    write applies only if `ts` >= the stored manual_ts. Returns the resulting
    user_state doc (whether or not this write won). Mirrors merge_state_guarded's
    locked compare-and-write so a late/concurrent older report can't clobber a
    newer manual value — the read-check-write is one serialized transaction."""
    try:
        with get_pool().connection() as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO user_blobs (user_id, kind, doc) VALUES (%s, %s, '{}'::jsonb) "
                    "ON CONFLICT (user_id, kind) DO NOTHING",
                    (user_id, USER_STATE),
                )
                row = conn.execute(
                    "SELECT doc FROM user_blobs WHERE user_id = %s AND kind = %s FOR UPDATE",
                    (user_id, USER_STATE),
                ).fetchone()
                doc = dict(row[0]) if row and isinstance(row[0], dict) else {}
                prev_ts = doc.get("manual_ts")
                if prev_ts is None or float(ts) >= float(prev_ts):
                    doc["manual"] = str(value or "default")
                    doc["manual_ts"] = float(ts)
                    conn.execute(
                        "UPDATE user_blobs SET doc = %s WHERE user_id = %s AND kind = %s",
                        (Jsonb(doc), user_id, USER_STATE),
                    )
                return doc
    except Exception as e:
        log.error("set_manual_user_state_guarded(%s) failed: %s", user_id, e)
        return _get_blob(user_id, USER_STATE)


# ---------------------------------------------------------------------------
# Collections (perception_items)
# ---------------------------------------------------------------------------

def item_upsert(user_id: str, kind: str, item_id: str, ts: float,
                doc: dict, expires_at: float | None = None) -> bool:
    """Insert/update one item. Returns True on success, False on DB failure.
    The key-rotation rewrap relies on the bool to abort before swapping the
    user key when a rewrapped item can't be persisted (otherwise that item
    would be stranded on the retired key)."""
    try:
        with get_pool().connection() as conn:
            conn.execute(
                "INSERT INTO perception_items (user_id, kind, item_id, ts, expires_at, doc) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (user_id, kind, item_id) DO UPDATE "
                "SET ts = EXCLUDED.ts, expires_at = EXCLUDED.expires_at, doc = EXCLUDED.doc",
                (user_id, kind, item_id, ts, expires_at, Jsonb(doc)),
            )
        return True
    except Exception as e:
        log.error("item_upsert(%s,%s,%s) failed: %s", user_id, kind, item_id, e)
        return False


def item_get(user_id: str, kind: str, item_id: str, now: float | None = None) -> dict | None:
    """Fetch one item. If `now` is given, expired items (expires_at <= now) are
    treated as absent."""
    try:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT doc, expires_at FROM perception_items "
                "WHERE user_id = %s AND kind = %s AND item_id = %s",
                (user_id, kind, item_id),
            ).fetchone()
        if not row:
            return None
        doc, expires_at = row[0], row[1]
        if now is not None and expires_at is not None and expires_at <= now:
            return None
        return doc
    except Exception as e:
        log.error("item_get(%s,%s,%s) failed: %s", user_id, kind, item_id, e)
        return None


def item_list(user_id: str, kind: str, limit: int = 20,
              now: float | None = None) -> list[dict]:
    """Newest-first list of a kind, skipping expired rows."""
    try:
        sql = ("SELECT doc FROM perception_items "
               "WHERE user_id = %s AND kind = %s")
        params: list = [user_id, kind]
        if now is not None:
            sql += " AND (expires_at IS NULL OR expires_at > %s)"
            params.append(now)
        sql += " ORDER BY ts DESC LIMIT %s"
        params.append(max(1, min(limit, 200)))
        with get_pool().connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        log.error("item_list(%s,%s) failed: %s", user_id, kind, e)
        return []


def item_list_rows(user_id: str, kind: str, limit: int | None = 20,
                   now: float | None = None, strict: bool = False) -> list[dict]:
    """Like item_list but includes the cleartext index columns — needed by the
    ciphertext read path, where the doc is just {"env": envelope} and consumers
    (enclave / iOS) need item_id/ts to label decrypted items.

    limit=None means NO limit: the key-rotation rewrap must see every live row
    — a silent cap would leave older rows sealed to the retired key.

    strict=True RE-RAISES on a DB error instead of returning []. The rewrap
    inventory must use strict: a swallowed listing failure would look like
    "no encrypted content" and let the user key rotate while real envelopes
    stay sealed to the old key."""
    try:
        sql = ("SELECT item_id, ts, expires_at, doc FROM perception_items "
               "WHERE user_id = %s AND kind = %s")
        params: list = [user_id, kind]
        if now is not None:
            sql += " AND (expires_at IS NULL OR expires_at > %s)"
            params.append(now)
        sql += " ORDER BY ts DESC"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(max(1, min(limit, 200)))
        with get_pool().connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [{"item_id": r[0], "ts": r[1], "expires_at": r[2], "doc": r[3]}
                for r in rows]
    except Exception as e:
        log.error("item_list_rows(%s,%s) failed: %s", user_id, kind, e)
        if strict:
            raise
        return []


def item_patch(user_id: str, kind: str, item_id: str, patch: dict,
               expires_at: float | None = "__keep__") -> dict | None:
    """Shallow-merge `patch` into an item's doc. Optionally also set expires_at
    (pass None to clear the TTL / promote a staged item; omit to keep current)."""
    try:
        with get_pool().connection() as conn:
            if expires_at == "__keep__":
                row = conn.execute(
                    "UPDATE perception_items SET doc = doc || %s "
                    "WHERE user_id = %s AND kind = %s AND item_id = %s RETURNING doc",
                    (Jsonb(patch), user_id, kind, item_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "UPDATE perception_items SET doc = doc || %s, expires_at = %s "
                    "WHERE user_id = %s AND kind = %s AND item_id = %s RETURNING doc",
                    (Jsonb(patch), expires_at, user_id, kind, item_id),
                ).fetchone()
        return row[0] if row else None
    except Exception as e:
        log.error("item_patch(%s,%s,%s) failed: %s", user_id, kind, item_id, e)
        return None


def item_delete(user_id: str, kind: str, item_id: str) -> bool:
    try:
        with get_pool().connection() as conn:
            cur = conn.execute(
                "DELETE FROM perception_items WHERE user_id = %s AND kind = %s AND item_id = %s",
                (user_id, kind, item_id),
            )
        return cur.rowcount > 0
    except Exception as e:
        log.error("item_delete(%s,%s,%s) failed: %s", user_id, kind, item_id, e)
        return False


def prune_expired(now: float) -> int:
    """Delete all expired items across users (lazy GC; safe to call anytime)."""
    try:
        with get_pool().connection() as conn:
            cur = conn.execute(
                "DELETE FROM perception_items WHERE expires_at IS NOT NULL AND expires_at <= %s",
                (now,),
            )
        return cur.rowcount or 0
    except Exception as e:
        log.error("prune_expired failed: %s", e)
        return 0


def latest_ts(user_id: str, kind: str) -> float | None:
    """Most-recent ts for a kind (used for photo burst clustering)."""
    try:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT MAX(ts) FROM perception_items WHERE user_id = %s AND kind = %s",
                (user_id, kind),
            ).fetchone()
        return row[0] if row else None
    except Exception as e:
        log.error("latest_ts(%s,%s) failed: %s", user_id, kind, e)
        return None


# ---------------------------------------------------------------------------
# Event / wake audit trail (user_logs)
# ---------------------------------------------------------------------------

def append_event(user_id: str, event: dict, ts: float) -> None:
    log_append(user_id, EVENT_STREAM, event, ts=ts)


def read_events(user_id: str, limit: int = 50) -> list[dict]:
    return log_read(user_id, EVENT_STREAM, limit=limit)


# App-usage time series (one append per app-open from the iOS Shortcut endpoint).
APP_USAGE_STREAM = "app_usage"


def append_app_open(user_id: str, doc: dict, ts: float) -> None:
    # item_key = envelope id so the key-rotation rewrap can patch this row's
    # envelope in place (these events never expire, unlike perception_state).
    env = doc.get("env")
    item_key = env.get("id") if isinstance(env, dict) else None
    log_append(user_id, APP_USAGE_STREAM, doc, ts=ts, item_key=item_key)


def read_app_opens(user_id: str, limit: int = 100, since_epoch: float = 0.0) -> list[dict]:
    return log_read(user_id, APP_USAGE_STREAM, limit=limit, since_epoch=since_epoch)


def read_all_app_opens(user_id: str, strict: bool = False) -> list[dict]:
    """EVERY app-open row (no limit) — for the key-rotation rewrap.

    strict=True RE-RAISES on a DB error (the generic log_read swallows it and
    returns []). The rewrap inventory must use strict: a swallowed failure
    here would omit every app_usage envelope and let the key rotate while
    those rows stay sealed to the old key."""
    if not strict:
        return log_read_all(user_id, APP_USAGE_STREAM)
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT doc FROM user_logs WHERE user_id = %s AND stream = %s ORDER BY seq ASC",
            (user_id, APP_USAGE_STREAM),
        ).fetchall()
    return [r[0] for r in rows]


def update_app_open_env(user_id: str, item_key: str, env: dict) -> bool:
    """Swap one app-open row's envelope in place (key rotation). Returns False
    when no row matches item_key (e.g. a pre-item_key legacy row)."""
    return log_patch_item(user_id, APP_USAGE_STREAM, item_key, {"env": env}) is not None


def apply_rewrap_batch(user_id: str,
                       item_upserts: list[tuple],
                       app_usage_patches: list[tuple],
                       frame_upserts: list[tuple] | None = None) -> tuple[bool, list[str]]:
    """ATOMICALLY apply a batch of perception rewrites in ONE transaction.

    - item_upserts: (kind, item_id, ts, doc, expires_at) for perception_items.
    - app_usage_patches: (item_key, env) — patches user_logs app_usage rows in
      place (matched by item_key); a 0-row match is a miss.
    - frame_upserts: (frame_id, ts, env) — photo PIXEL envelopes in
      frame_envelopes. They live outside perception_items but must rotate in the
      SAME transaction, or a photo's pixels stay sealed to the retired key while
      its meta_env / items moved on (then /v1/screen/frames/<id>/decrypt fails).

    All-or-nothing: any DB error or app_usage miss rolls the whole batch back so
    a partial rewrap can never persist some rows on the NEW key while the
    endpoint then refuses to rotate (which would strand them). Returns
    (True, []) on commit, or (False, misses) with nothing written."""
    if not item_upserts and not app_usage_patches and not frame_upserts:
        return True, []
    misses: list[str] = []
    try:
        with get_pool().connection() as conn:
            with conn.transaction():
                for kind, item_id, ts, doc, expires_at in item_upserts:
                    conn.execute(
                        "INSERT INTO perception_items (user_id, kind, item_id, ts, expires_at, doc) "
                        "VALUES (%s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (user_id, kind, item_id) DO UPDATE "
                        "SET ts = EXCLUDED.ts, expires_at = EXCLUDED.expires_at, doc = EXCLUDED.doc",
                        (user_id, kind, item_id, ts, expires_at, Jsonb(doc)),
                    )
                for frame_id, ts, env in (frame_upserts or []):
                    conn.execute(
                        "INSERT INTO frame_envelopes (user_id, frame_id, ts, doc) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (user_id, frame_id) DO UPDATE "
                        "SET ts = EXCLUDED.ts, doc = EXCLUDED.doc",
                        (user_id, frame_id, float(ts), Jsonb(env)),
                    )
                for item_key, env in app_usage_patches:
                    if not item_key:
                        misses.append("app_usage:(no id)")
                        continue
                    cur = conn.execute(
                        "UPDATE user_logs SET doc = doc || %s "
                        "WHERE user_id = %s AND stream = %s AND item_key = %s",
                        (Jsonb({"env": env}), user_id, APP_USAGE_STREAM, item_key),
                    )
                    if (cur.rowcount or 0) == 0:
                        misses.append(f"app_usage:{item_key}")
                if misses:
                    # Roll the whole transaction back — no partial new-key state.
                    raise _RewrapBatchMiss()
        return True, []
    except _RewrapBatchMiss:
        return False, misses
    except Exception as e:
        log.error("apply_rewrap_batch(%s) failed: %s", user_id, e)
        return False, misses or [f"db_error:{type(e).__name__}"]


class _RewrapBatchMiss(Exception):
    """Internal sentinel to roll back apply_rewrap_batch on an app_usage miss."""


# ---------------------------------------------------------------------------
# Photo ciphertext — reuses the screen-frame envelope channel.
# ---------------------------------------------------------------------------
# Confirmed photo envelopes live in the existing frame_envelopes table (keyed by
# the envelope id == photo_id). This keeps the big base64 ciphertext out of the
# perception_items JSONB AND makes photos decryptable through the enclave's
# existing /v1/screen/frames/<id>/decrypt path with zero new crypto code. The
# screen-frame *list* (frames_meta, a separate index blob) is left untouched, so
# photos never appear among screen frames.

def put_photo_envelope(user_id: str, frame_id: str, ts: float, env: dict) -> None:
    frame_upsert(user_id, frame_id, ts, env)


def get_photo_envelope(user_id: str, frame_id: str) -> dict | None:
    return frame_get(user_id, frame_id)


def get_photo_envelope_strict(user_id: str, frame_id: str) -> dict | None:
    """Strict frame-envelope read for the key-rotation inventory: RE-RAISES on a
    DB error instead of swallowing it. frame_get returns None on error, which
    would hide a photo's pixel envelope and let the user key rotate while the
    ciphertext stays sealed to the retired key (then /v1/screen/frames/<id>/
    decrypt can never be decrypted with the new key)."""
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT doc FROM frame_envelopes WHERE user_id = %s AND frame_id = %s",
            (user_id, frame_id),
        ).fetchone()
    return row[0] if row is not None else None


def delete_photo_envelope(user_id: str, frame_id: str) -> None:
    frame_delete(user_id, frame_id)
