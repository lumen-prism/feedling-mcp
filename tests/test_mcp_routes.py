from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import app as appmod  # noqa: E402
from core import config as core_config  # noqa: E402
from mcp import routes as mcp_routes  # noqa: E402


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core_config, "FEEDLING_DIR", tmp_path)
    appmod._users[:] = []
    appmod._key_to_user.clear()
    appmod._stores.clear()
    appmod._save_users()
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


def _env(user_id: str, entry_id: str = "mcp1", *, body_ct: str = "ct") -> dict:
    return {
        "v": 1,
        "id": entry_id,
        "body_ct": body_ct,
        "nonce": "nonce",
        "K_user": "k-user",
        "K_enclave": "k-enclave",
        "visibility": "shared",
        "owner_user_id": user_id,
        "enclave_pk_fpr": "fpr",
    }


def test_mcp_routes_require_auth(client):
    assert client.get("/v1/mcp/list").status_code == 401
    assert client.post("/v1/mcp/upsert", json={}).status_code == 401
    assert client.delete("/v1/mcp/delete?id=mcp1").status_code == 401


def test_mcp_upsert_list_delete_round_trips_ciphertext(client):
    user_id, api_key = _register(client)
    env = _env(user_id, body_ct="body-1")

    upsert = client.post("/v1/mcp/upsert", json=env, headers=_headers(api_key))
    assert upsert.status_code == 200, upsert.get_data(as_text=True)
    assert upsert.get_json() == {"id": "mcp1"}

    listed = client.get("/v1/mcp/list", headers=_headers(api_key))
    assert listed.status_code == 200, listed.get_data(as_text=True)
    envelopes = listed.get_json()["envelopes"]
    assert len(envelopes) == 1
    assert {key: envelopes[0][key] for key in env} == env

    deleted = client.delete("/v1/mcp/delete?id=mcp1", headers=_headers(api_key))
    assert deleted.status_code == 200, deleted.get_data(as_text=True)
    assert deleted.get_json() == {"ok": True}
    assert client.get("/v1/mcp/list", headers=_headers(api_key)).get_json() == {"envelopes": []}


def test_mcp_upsert_rejects_outer_id_mismatch(client):
    user_id, api_key = _register(client)
    res = client.post(
        "/v1/mcp/upsert",
        json={"id": "outer", "envelope": _env(user_id, "inner")},
        headers=_headers(api_key),
    )
    assert res.status_code == 400
    assert "id" in res.get_json()["error"]


def test_mcp_upsert_rejects_wrong_owner(client):
    _user_id, api_key = _register(client)
    res = client.post(
        "/v1/mcp/upsert",
        json=_env("other-user", "mcp1"),
        headers=_headers(api_key),
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "owner_user_id does not match caller"


def test_mcp_upsert_rejects_invalid_plain_config_reported_by_enclave(client, monkeypatch):
    user_id, api_key = _register(client)

    def fake_validate(api_key_arg, mcp_servers, *, runtime_token=None):
        assert api_key_arg == api_key
        assert [item["id"] for item in mcp_servers] == ["bad-url"]
        return {"valid_ids": [], "invalid": [{"id": "bad-url", "error": "url must start with https://"}]}

    monkeypatch.setenv("FEEDLING_ENCLAVE_URL", "http://enclave.test")
    monkeypatch.setattr(
        mcp_routes.mcp_readside_core,
        "post_enclave_mcp_validate",
        fake_validate,
    )

    res = client.post(
        "/v1/mcp/upsert",
        json=_env(user_id, "bad-url"),
        headers=_headers(api_key),
    )

    assert res.status_code == 400
    assert res.get_json() == {
        "error": "mcp_validate_failed",
        "id": "bad-url",
        "detail": "url must start with https://",
    }
    assert client.get("/v1/mcp/list", headers=_headers(api_key)).get_json() == {"envelopes": []}


def test_mcp_upsert_fails_closed_when_enclave_unavailable(client, monkeypatch):
    user_id, api_key = _register(client)

    def fake_validate(api_key_arg, mcp_servers, *, runtime_token=None):
        raise RuntimeError("enclave_error:ConnectError")

    monkeypatch.setenv("FEEDLING_ENCLAVE_URL", "http://enclave.test")
    monkeypatch.setattr(
        mcp_routes.mcp_readside_core,
        "post_enclave_mcp_validate",
        fake_validate,
    )

    res = client.post(
        "/v1/mcp/upsert",
        json=_env(user_id, "mcp1"),
        headers=_headers(api_key),
    )

    assert res.status_code == 503
    assert res.get_json()["error"] == "mcp_validate_unavailable"
    assert client.get("/v1/mcp/list", headers=_headers(api_key)).get_json() == {"envelopes": []}
