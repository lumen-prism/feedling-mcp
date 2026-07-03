"""MCP server config HTTP surface: /v1/mcp/*."""

from __future__ import annotations

from datetime import datetime
import os

from flask import Blueprint, jsonify, request

from accounts import auth
from content.routes import _apply_envelope_fields, _swap_envelope_missing
import mcp_readside_core

bp = Blueprint("mcp", __name__)


def _request_envelope(payload: dict) -> tuple[dict | None, str | None]:
    if not isinstance(payload, dict):
        return None, "body must be a JSON object"
    nested = payload.get("envelope")
    if nested is not None:
        if not isinstance(nested, dict):
            return None, "envelope must be an object"
        outer_id = str(payload.get("id") or "").strip()
        inner_id = str(nested.get("id") or "").strip()
        if outer_id and inner_id and outer_id != inner_id:
            return None, "top-level id must match envelope id"
        return nested, None
    return payload, None


def _validate_envelope(env: dict, owner_user_id: str) -> str | None:
    missing = _swap_envelope_missing(env)
    if missing:
        return f"envelope missing {missing}"
    entry_id = str(env.get("id") or "").strip()
    if not entry_id:
        return "id required"
    if str(env.get("visibility") or "") not in {"shared", "local_only"}:
        return "envelope.visibility must be 'shared' or 'local_only'"
    if env.get("visibility") == "shared" and not env.get("K_enclave"):
        return "shared visibility requires K_enclave"
    if env.get("owner_user_id") != owner_user_id:
        return "owner_user_id does not match caller"
    return None


def _validate_config_with_enclave(record: dict) -> tuple[dict, int] | None:
    """Validate decrypted MCP config before persistence when enclave is enabled."""
    if not os.environ.get("FEEDLING_ENCLAVE_URL", "").strip():
        return None
    api_key = auth._extract_api_key()
    runtime_token = request.headers.get("X-Feedling-Runtime-Token", "").strip() or None
    try:
        result = mcp_readside_core.post_enclave_mcp_validate(
            api_key, [record], runtime_token=runtime_token)
    except RuntimeError as e:
        return {"error": "mcp_validate_unavailable", "detail": str(e)}, 503
    entry_id = str(record.get("id") or "").strip()
    for item in result.get("invalid") or []:
        if str((item or {}).get("id") or "").strip() == entry_id:
            return {
                "error": "mcp_validate_failed",
                "id": entry_id,
                "detail": str((item or {}).get("error") or ""),
            }, 400
    valid_ids = {str(item) for item in result.get("valid_ids") or []}
    if entry_id not in valid_ids:
        return {"error": "mcp_validate_failed", "id": entry_id, "detail": "not validated"}, 400
    return None


@bp.route("/v1/mcp/list", methods=["GET"])
def mcp_list():
    store = auth.require_user()
    with store.mcp_servers_lock:
        envelopes = [dict(item) for item in store.mcp_servers]
    return jsonify({"envelopes": envelopes})


@bp.route("/v1/mcp/upsert", methods=["POST"])
def mcp_upsert():
    store = auth.require_user()
    payload = request.get_json(silent=True) or {}
    env, parse_error = _request_envelope(payload)
    if parse_error:
        return jsonify({"error": parse_error}), 400
    validation_error = _validate_envelope(env or {}, store.user_id)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    record = {"id": str(env.get("id") or "").strip(), "updated_at": datetime.now().isoformat()}
    _apply_envelope_fields(record, env)
    config_error = _validate_config_with_enclave(record)
    if config_error:
        body, status = config_error
        return jsonify(body), status
    saved = store.upsert_mcp_server(record)
    return jsonify({"id": saved["id"]})


@bp.route("/v1/mcp/delete", methods=["DELETE"])
def mcp_delete():
    store = auth.require_user()
    entry_id = str(request.args.get("id") or "").strip()
    if not entry_id:
        return jsonify({"error": "id required"}), 400
    store.delete_mcp_server(entry_id)
    return jsonify({"ok": True})
