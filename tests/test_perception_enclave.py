"""Tests for the enclave's perception decrypt-and-serve routes.

The enclave pulls the CIPHERTEXT snapshot shape from the backend
({fields, encrypted, recent_apps}), opens each v1 envelope with its content
key inside the TEE, and serves the FLAT agent-facing snapshot. These tests
mock the backend hop (`_flask_get`) and whoami, but run the REAL decryption
(_box_seal_open_hkdf + AEAD) against envelopes built with
content_encryption.build_envelope — so wire-format drift fails here.

Run:  python -m pytest tests/test_perception_enclave.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import nacl.public
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import enclave_app  # noqa: E402
from content_encryption import build_envelope  # noqa: E402


UID = "usr_test"

USER_SK = X25519PrivateKey.generate()
ENCLAVE_SK = X25519PrivateKey.generate()


def _raw_pub(sk) -> bytes:
    return sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def _raw_priv(sk) -> bytes:
    return sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())


USER_PK = _raw_pub(USER_SK)
ENCLAVE_PK = _raw_pub(ENCLAVE_SK)
ENCLAVE_NACL_SK = nacl.public.PrivateKey(_raw_priv(ENCLAVE_SK))


def _mk_env(body_obj, owner=UID):
    return build_envelope(
        plaintext=json.dumps(body_obj).encode("utf-8"),
        owner_user_id=owner, user_pk_bytes=USER_PK,
        enclave_pk_bytes=ENCLAVE_PK, visibility="shared")


def _ciphertext_snapshot():
    return {
        "fields": {
            "battery_level": "0.85", "charging": "true",
            "local_time": "2026-06-12T15:30:45Z", "timezone": "UTC", "locale": "en",
            "broadcast_state": "inactive", "broadcast_active": "false",
            "user_state": "default",
        },
        "encrypted": {
            "location_signal": {"envelope": _mk_env({"values": {
                "place_label": "home", "wifi_label": "home_wifi", "country": "US"}}),
                "ts": 1000.0},
            "playback": {"envelope": _mk_env({"values": {
                "now_playing": {"title": "晴天", "artist": "周杰伦"}}}), "ts": 1000.0},
            # app_open seals the dual-shape body: values.* feeds the flattener,
            # top-level app/category feeds recent_apps (Codex P2, round 3).
            "app": {"envelope": _mk_env({
                "values": {"app_name": "Instagram", "app_category": "social"},
                "app": "Instagram", "category": "social", "ts": 999.0}), "ts": 999.0},
        },
        "recent_apps": [
            {"envelope": _mk_env({
                "values": {"app_name": "Instagram", "app_category": "social"},
                "app": "Instagram", "category": "social", "ts": 999.0}),
             "ts": 999.0},
        ],
    }


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setitem(enclave_app._state, "ready", True)
    monkeypatch.setitem(enclave_app._state, "error", None)
    monkeypatch.setattr(enclave_app, "_whoami_cached", lambda api_key: {"user_id": UID})
    monkeypatch.setattr(enclave_app, "_get_or_derive_content_sk", lambda: ENCLAVE_NACL_SK)
    enclave_app.app.config.update(TESTING=False)
    with enclave_app.app.test_client() as c:
        yield c


def test_snapshot_decrypts_to_flat_shape(client, monkeypatch):
    monkeypatch.setattr(enclave_app, "_flask_get",
                        lambda path, key, params=None: _ciphertext_snapshot())
    r = client.get("/v1/perception/snapshot", headers={"X-API-Key": "k"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    snap = body["snapshot"]
    # cleartext operational fields pass through
    assert snap["battery_level"] == "0.85" and snap["user_state"] == "default"
    # encrypted signals flattened into their output fields
    assert snap["place_label"] == "home"
    assert snap["wifi_label"] == "home_wifi"
    assert snap["country"] == "US"
    assert snap["now_playing"]["title"] == "晴天"
    # app cell flattens via values.{app_name,app_category} (not just recent_apps)
    assert snap["app_name"] == "Instagram"
    assert snap["app_category"] == "social"
    # encrypted-but-unreported signals are null, not absent
    assert snap["motion_state"] is None
    assert snap["calendar_next_event"] is None
    # recent_apps decrypted
    assert snap["recent_apps"] == [{"app": "Instagram", "category": "social", "ts": 999.0}]
    assert "decrypt_errors" not in body


def test_snapshot_tampered_envelope_yields_null_and_error(client, monkeypatch):
    snap_ct = _ciphertext_snapshot()
    env = snap_ct["encrypted"]["location_signal"]["envelope"]
    env["body_ct"] = env["body_ct"][:-8] + "AAAAAAA="     # corrupt the AEAD body
    monkeypatch.setattr(enclave_app, "_flask_get",
                        lambda path, key, params=None: snap_ct)
    r = client.get("/v1/perception/snapshot", headers={"X-API-Key": "k"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["snapshot"]["place_label"] is None         # failed item -> null
    assert body["snapshot"]["now_playing"]["title"] == "晴天"  # others unaffected
    assert any(e["signal"] == "location_signal" for e in body["decrypt_errors"])


def test_snapshot_cross_user_envelope_rejected(client, monkeypatch):
    """An envelope owned by another user must fail AEAD/owner binding, not leak."""
    snap_ct = _ciphertext_snapshot()
    snap_ct["encrypted"]["location_signal"]["envelope"] = _mk_env(
        {"values": {"place_label": "home"}}, owner="usr_other")
    monkeypatch.setattr(enclave_app, "_flask_get",
                        lambda path, key, params=None: snap_ct)
    r = client.get("/v1/perception/snapshot", headers={"X-API-Key": "k"})
    body = r.get_json()
    assert body["snapshot"]["place_label"] is None
    assert any(e["signal"] == "location_signal" for e in body["decrypt_errors"])


def test_snapshot_requires_api_key(client):
    r = client.get("/v1/perception/snapshot")
    assert r.status_code == 401


def test_items_decrypt(client, monkeypatch):
    rows = {"items": [
        {"item_id": "w1", "ts": 1000.0, "expires_at": None,
         "envelope": _mk_env({"kind": "run", "minutes": 30})},
    ]}
    monkeypatch.setattr(enclave_app, "_flask_get",
                        lambda path, key, params=None: rows)
    r = client.get("/v1/perception/items/workout", headers={"X-API-Key": "k"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["kind"] == "workout"
    assert body["items"][0]["doc"] == {"kind": "run", "minutes": 30}
    assert body["items"][0]["decrypt_status"] == "ok"


def test_items_tampered_envelope_reports_error(client, monkeypatch):
    env = _mk_env({"kind": "run"})
    env["body_ct"] = env["body_ct"][:-8] + "AAAAAAA="
    monkeypatch.setattr(
        enclave_app, "_flask_get",
        lambda path, key, params=None: {"items": [
            {"item_id": "w1", "ts": 1.0, "expires_at": None, "envelope": env}]})
    r = client.get("/v1/perception/items/workout", headers={"X-API-Key": "k"})
    body = r.get_json()
    assert "doc" not in body["items"][0]
    assert body["items"][0]["decrypt_status"].startswith("error:")
