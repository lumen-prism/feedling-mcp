from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import app as appmod  # noqa: E402
from core import config as core_config  # noqa: E402
from core import enclave as core_enclave  # noqa: E402


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core_config, "FEEDLING_DIR", tmp_path)
    appmod._users[:] = []
    appmod._key_to_user.clear()
    appmod._stores.clear()
    appmod._save_users()
    monkeypatch.setattr(
        core_enclave,
        "_get_enclave_info",
        lambda: {"content_pk_hex": ("22" * 32), "compose_hash": "test"},
    )
    appmod.app.config.update(TESTING=True)
    with appmod.app.test_client() as c:
        yield c


def _register(client) -> tuple[str, str]:
    res = client.post(
        "/v1/users/register",
        json={"public_key": _b64(b"\x11" * 32), "archive_language": "en"},
    )
    assert res.status_code == 201, res.get_data(as_text=True)
    body = res.get_json()
    return body["user_id"], body["api_key"]


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


def _old_env(user_id: str, item_id: str) -> dict:
    return {
        "v": 1,
        "id": item_id,
        "body_ct": _b64(f"old-body:{item_id}".encode()),
        "nonce": _b64(b"\x00" * 12),
        "K_user": _b64(b"\x01" * 48),
        "K_enclave": _b64(b"\x02" * 48),
        "visibility": "shared",
        "owner_user_id": user_id,
        "enclave_pk_fpr": "old",
    }


def _seed_encrypted_content(user_id: str) -> dict[str, str]:
    store = appmod.get_store(user_id)
    now = appmod.datetime.now().isoformat()

    identity = {
        **_old_env(user_id, "identity1"),
        "created_at": now,
        "updated_at": now,
        "relationship_started_at": "2026-06-01",
    }
    appmod._save_identity(store, identity)

    memory = {
        **_old_env(user_id, "memory1"),
        "type": "fact",
        "occurred_at": "2026-06-01",
        "created_at": now,
        "source": "test",
    }
    appmod._save_moments(store, [memory])

    chat = {
        **_old_env(user_id, "chat1"),
        "role": "openclaw",
        "source": "test",
        "ts": time.time(),
        "content_type": "text",
    }
    with store.chat_lock:
        store.chat_messages = [chat]
        appmod.db.chat_append(user_id, chat["id"], chat["ts"], chat, appmod.MAX_CHAT_MESSAGES)
    return {
        "identity_K_user": identity["K_user"],
        "memory_K_user": memory["K_user"],
        "chat_K_user": chat["K_user"],
    }


def test_public_key_rotation_requires_rewrap_when_content_exists(client):
    user_id, api_key = _register(client)
    _seed_encrypted_content(user_id)

    res = client.post(
        "/v1/users/public-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )

    assert res.status_code == 409, res.get_data(as_text=True)
    body = res.get_json()
    assert body["error"] == "public_key_rotation_requires_rewrap"
    assert body["encrypted_content"] == {
        "identity": 1, "memory": 1, "chat": 1, "perception": 0, "total": 3}
    assert body["recovery_endpoint"] == "/v1/content/rewrap-to-current-key"
    assert appmod._get_user_public_key(user_id) == _b64(b"\x11" * 32)


def test_content_rewrap_to_current_key_rewraps_all_shared_content(client, monkeypatch):
    user_id, api_key = _register(client)
    old_keys = _seed_encrypted_content(user_id)
    new_public_key = _b64(b"\x33" * 32)

    def fake_decrypt(envelope, key, purpose):
        assert key == api_key
        return f"plaintext:{purpose}:{envelope.get('id')}".encode()

    monkeypatch.setattr(core_enclave, "_decrypt_envelope_via_enclave", fake_decrypt)

    dry = client.post(
        "/v1/content/rewrap-to-current-key",
        json={"public_key": new_public_key, "dry_run": True},
        headers=_headers(api_key),
    )
    assert dry.status_code == 200, dry.get_data(as_text=True)
    assert dry.get_json()["summary"]["total_rewrapped"] == 3
    assert appmod._get_user_public_key(user_id) == _b64(b"\x11" * 32)

    res = client.post(
        "/v1/content/rewrap-to-current-key",
        json={"public_key": new_public_key},
        headers=_headers(api_key),
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["summary"]["total_rewrapped"] == 3
    assert body["summary"]["total_errors"] == 0
    assert appmod._get_user_public_key(user_id) == new_public_key

    store = appmod.get_store(user_id)
    identity = appmod._load_identity(store)
    moments = appmod._load_moments(store)
    with store.chat_lock:
        chat = list(store.chat_messages)

    assert identity["id"] == "identity1"
    assert moments[0]["id"] == "memory1"
    assert chat[0]["id"] == "chat1"
    assert identity["K_user"] != old_keys["identity_K_user"]
    assert moments[0]["K_user"] != old_keys["memory_K_user"]
    assert chat[0]["K_user"] != old_keys["chat_K_user"]
    assert "K_enclave" in identity and "K_enclave" in moments[0] and "K_enclave" in chat[0]


def test_content_rewrap_covers_perception_items(client, monkeypatch):
    """Tier 2 perception items ({"env": envelope} docs), photo meta envelopes
    AND persistent app_usage events are rewrapped alongside chat/memory/
    identity; cleartext index/gate fields are preserved."""
    from perception import store as perception_store  # noqa: E402

    user_id, api_key = _register(client)
    now = time.time()
    old_k_user = _b64(b"\x01" * 48)
    perception_store.item_upsert(
        user_id, "workout", "w1", now, {"env": _old_env(user_id, "w1")}, None)
    perception_store.item_upsert(
        user_id, "photo", "p1", now,
        {"photo_id": "p1", "metadata": {"scene_hint": "food"}, "status": "confirmed",
         "usable": True, "sensitive": False, "frame_id": "p1",
         "meta_env": _old_env(user_id, "pm1")}, None)
    perception_store.append_app_open(
        user_id, {"env": _old_env(user_id, "ao1"), "ts": now}, now)

    def fake_decrypt(envelope, key, purpose):
        assert key == api_key
        return f"plaintext:{purpose}:{envelope.get('id')}".encode()

    monkeypatch.setattr(core_enclave, "_decrypt_envelope_via_enclave", fake_decrypt)

    res = client.post(
        "/v1/content/rewrap-to-current-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["summary"]["perception"]["rewrapped"] == 3
    assert body["summary"]["total_errors"] == 0

    rows = perception_store.item_list_rows(user_id, "workout", limit=10)
    assert rows[0]["item_id"] == "w1"
    assert rows[0]["doc"]["env"]["K_user"] != old_k_user

    photo = perception_store.item_get(user_id, "photo", "p1")
    assert photo["meta_env"]["K_user"] != old_k_user
    assert photo["metadata"]["scene_hint"] == "food"   # cleartext gate metadata intact
    assert photo["status"] == "confirmed"

    opens = perception_store.read_all_app_opens(user_id)
    assert len(opens) == 1
    assert opens[0]["env"]["K_user"] != old_k_user      # patched in place
    assert opens[0]["env"]["id"] == "ao1"


def test_content_rewrap_covers_photo_pixel_envelope(client, monkeypatch):
    """A confirmed photo's PIXEL envelope lives in frame_envelopes (keyed by
    frame_id == photo_id), not in the perception_items doc. It must be rewrapped
    alongside the meta_env, or /v1/screen/frames/<id>/decrypt stays sealed to the
    retired key after rotation, leaving the device unable to decrypt the photo
    with its new key (Codex P1)."""
    from perception import store as perception_store  # noqa: E402

    user_id, api_key = _register(client)
    now = time.time()
    old_k_user = _b64(b"\x01" * 48)
    perception_store.item_upsert(
        user_id, "photo", "p1", now,
        {"photo_id": "p1", "metadata": {"scene_hint": "food"}, "status": "confirmed",
         "usable": True, "sensitive": False, "frame_id": "p1",
         "meta_env": _old_env(user_id, "pm1")}, None)
    perception_store.put_photo_envelope(user_id, "p1", now, _old_env(user_id, "p1"))

    monkeypatch.setattr(
        core_enclave, "_decrypt_envelope_via_enclave",
        lambda envelope, key, purpose: f"plaintext:{envelope.get('id')}".encode())

    # The rotation gate must COUNT the pixel envelope (meta_env + pixels = 2).
    gate = client.post(
        "/v1/users/public-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )
    assert gate.status_code == 409, gate.get_data(as_text=True)
    assert gate.get_json()["encrypted_content"]["perception"] == 2

    res = client.post(
        "/v1/content/rewrap-to-current-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["summary"]["perception"]["rewrapped"] == 2   # meta_env + pixels
    assert body["summary"]["total_errors"] == 0
    assert appmod._get_user_public_key(user_id) == _b64(b"\x33" * 32)

    pixel = perception_store.get_photo_envelope(user_id, "p1")
    assert pixel["id"] == "p1"
    assert pixel["K_user"] != old_k_user      # pixel envelope followed the key
    meta = perception_store.item_get(user_id, "photo", "p1")["meta_env"]
    assert meta["K_user"] != old_k_user


def test_content_rewrap_perception_items_not_capped_at_200(client, monkeypatch):
    """The rewrap listing must be uncapped: with >200 live rows of one kind,
    every row is rewrapped before the public key is rotated (a silent cap
    would strand the older rows on the retired key)."""
    from perception import store as perception_store  # noqa: E402

    user_id, api_key = _register(client)
    now = time.time()
    old_k_user = _b64(b"\x01" * 48)
    total = 230
    for i in range(total):
        perception_store.item_upsert(
            user_id, "workout", f"w{i}", now - i, {"env": _old_env(user_id, f"w{i}")}, None)

    monkeypatch.setattr(
        core_enclave, "_decrypt_envelope_via_enclave",
        lambda envelope, key, purpose: f"plaintext:{envelope.get('id')}".encode())

    res = client.post(
        "/v1/content/rewrap-to-current-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["summary"]["perception"]["rewrapped"] == total

    rows = perception_store.item_list_rows(user_id, "workout", limit=None)
    assert len(rows) == total
    assert all(r["doc"]["env"]["K_user"] != old_k_user for r in rows)


def test_content_rewrap_aborts_when_item_write_fails(client, monkeypatch):
    """If persisting a rewrapped perception item fails, the endpoint 409s and
    does NOT rotate the user key — otherwise that item is stranded on the old
    key (Codex P1)."""
    from content import routes as content_routes  # noqa: E402
    from perception import store as perception_store  # noqa: E402

    user_id, api_key = _register(client)
    now = time.time()
    _seed_encrypted_content(user_id)   # identity/memory/chat on the old key
    old_identity_k_user = appmod.db.get_blob(user_id, "identity")["K_user"]
    perception_store.item_upsert(
        user_id, "workout", "w1", now, {"env": _old_env(user_id, "w1")}, None)
    monkeypatch.setattr(
        core_enclave, "_decrypt_envelope_via_enclave",
        lambda envelope, key, purpose: f"plaintext:{envelope.get('id')}".encode())
    # Simulate the atomic apply rolling back (a write failed mid-batch).
    monkeypatch.setattr(content_routes.perception_store, "apply_rewrap_batch",
                        lambda *a, **k: (False, ["workout:w1"]))

    res = client.post(
        "/v1/content/rewrap-to-current-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )
    assert res.status_code == 409, res.get_data(as_text=True)
    body = res.get_json()
    assert body["error"] == "perception_rewrap_incomplete"
    assert any(m.startswith("workout:") for m in body["perception_misses"])
    # The durable invariant: the SOURCE OF TRUTH (registry key) is NOT rotated
    # on a perception failure, so the device re-runs and the rewrap converges.
    # (Perception is applied last, just before the swap, so its rollback leaves
    # the registry on the old key — content rewrapped earlier this call is
    # re-sealed to the same target key on retry.)
    assert appmod._get_user_public_key(user_id) == _b64(b"\x11" * 32)
    _ = old_identity_k_user  # (identity may be pre-persisted; the key swap is the commit point)


def test_content_rewrap_aborts_when_inventory_listing_fails(client, monkeypatch):
    """A DB error while enumerating perception envelopes must 503 and refuse to
    rotate — never silently treat the inventory as empty (Codex P1)."""
    from content import routes as content_routes  # noqa: E402

    user_id, api_key = _register(client)
    monkeypatch.setattr(
        core_enclave, "_decrypt_envelope_via_enclave",
        lambda envelope, key, purpose: b"sk")

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(content_routes.perception_store, "item_list_rows", boom)

    res = client.post(
        "/v1/content/rewrap-to-current-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )
    assert res.status_code == 503, res.get_data(as_text=True)
    assert res.get_json()["error"] == "perception_inventory_unavailable"
    assert appmod._get_user_public_key(user_id) == _b64(b"\x11" * 32)


def test_set_public_key_refuses_when_inventory_unavailable(client, monkeypatch):
    """The 409-gate path must not rotate the key on an undercount when the
    perception inventory can't be enumerated (Codex P1)."""
    from content import routes as content_routes  # noqa: E402

    user_id, api_key = _register(client)

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(content_routes.perception_store, "item_list_rows", boom)

    res = client.post(
        "/v1/users/public-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )
    assert res.status_code == 503, res.get_data(as_text=True)
    assert res.get_json()["error"] == "encrypted_content_inventory_unavailable"
    assert appmod._get_user_public_key(user_id) == _b64(b"\x11" * 32)


def test_content_rewrap_apply_is_atomic_no_partial_writes(client, monkeypatch):
    """An app_usage patch miss mid-batch must roll back the perception_items
    writes too — no row left persisted on the new key while we refuse to
    rotate (Codex P1, round 4: atomic apply)."""
    from perception import store as perception_store  # noqa: E402

    user_id, api_key = _register(client)
    now = time.time()
    old_k_user = _b64(b"\x01" * 48)
    # A workout item that WILL rewrap, plus an app_usage row whose item_key
    # won't match on patch (no item_key) -> miss -> whole batch rolls back.
    perception_store.item_upsert(
        user_id, "workout", "w1", now, {"env": _old_env(user_id, "w1")}, None)
    # app_usage row appended WITHOUT an item_key (legacy shape) -> patch misses.
    appmod.db.log_append(user_id, perception_store.APP_USAGE_STREAM,
                         {"env": _old_env(user_id, "ao_legacy"), "ts": now}, ts=now)
    monkeypatch.setattr(
        core_enclave, "_decrypt_envelope_via_enclave",
        lambda envelope, key, purpose: f"plaintext:{envelope.get('id')}".encode())

    res = client.post(
        "/v1/content/rewrap-to-current-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )
    assert res.status_code == 409, res.get_data(as_text=True)
    assert res.get_json()["error"] == "perception_rewrap_incomplete"
    # key NOT rotated and the workout item was rolled back (still old key).
    assert appmod._get_user_public_key(user_id) == _b64(b"\x11" * 32)
    rows = perception_store.item_list_rows(user_id, "workout", limit=None)
    assert rows[0]["doc"]["env"]["K_user"] == old_k_user


def test_content_rewrap_aborts_on_concurrent_perception_write(client, monkeypatch):
    """A perception write sealed to the OLD key that lands AFTER the inventory is
    enumerated but BEFORE the key swap must abort the rotation (409, no swap) —
    otherwise it stays sealed to the retired key while rotation succeeds, and
    these rows don't expire (Codex P1-2). The device retries and converges."""
    from content import routes as content_routes  # noqa: E402
    from perception import store as perception_store  # noqa: E402

    user_id, api_key = _register(client)
    now = time.time()
    perception_store.item_upsert(
        user_id, "workout", "w1", now, {"env": _old_env(user_id, "w1")}, None)
    monkeypatch.setattr(
        core_enclave, "_decrypt_envelope_via_enclave",
        lambda envelope, key, purpose: f"plaintext:{envelope.get('id')}".encode())

    real_apply = perception_store.apply_rewrap_batch

    def racing_apply(uid, item_upserts, app_usage_patches, frame_upserts=None):
        ok, misses = real_apply(uid, item_upserts, app_usage_patches, frame_upserts)
        # Simulate a concurrent reporter writing a NEW item sealed to the OLD key
        # AFTER the inventory snapshot but before the (still pending) key swap.
        perception_store.item_upsert(
            uid, "workout", "w_race", now, {"env": _old_env(uid, "w_race")}, None)
        return ok, misses

    monkeypatch.setattr(content_routes.perception_store, "apply_rewrap_batch", racing_apply)

    res = client.post(
        "/v1/content/rewrap-to-current-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )
    assert res.status_code == 409, res.get_data(as_text=True)
    body = res.get_json()
    assert body["error"] == "perception_rewrap_raced"
    assert any(m.startswith("workout:w_race") for m in body["perception_stragglers"])
    # The SOURCE OF TRUTH (registry key) is NOT rotated, so the stranded straggler
    # is healed on the next (now race-free) retry.
    assert appmod._get_user_public_key(user_id) == _b64(b"\x11" * 32)


def test_content_rewrap_refuses_when_perception_envelope_local_only(client, monkeypatch):
    """A local_only perception envelope can't be rewrapped (no enclave key), so
    rotating would strand it on the retired key. Rewrap must 409, not skip-and-
    rotate (Codex P1, round 4)."""
    from perception import store as perception_store  # noqa: E402

    user_id, api_key = _register(client)
    now = time.time()
    local_env = _old_env(user_id, "w_local")
    local_env["visibility"] = "local_only"
    local_env.pop("K_enclave", None)
    perception_store.item_upsert(
        user_id, "workout", "w_local", now, {"env": local_env}, None)
    monkeypatch.setattr(
        core_enclave, "_decrypt_envelope_via_enclave",
        lambda envelope, key, purpose: b"sk")

    res = client.post(
        "/v1/content/rewrap-to-current-key",
        json={"public_key": _b64(b"\x33" * 32)},
        headers=_headers(api_key),
    )
    assert res.status_code == 409, res.get_data(as_text=True)
    body = res.get_json()
    assert body["error"] == "rewrap_failed"
    assert body["summary"]["perception"]["errors"] >= 1
    assert appmod._get_user_public_key(user_id) == _b64(b"\x11" * 32)
