"""MCP config readside core: validate decrypted user MCP server configs."""

from __future__ import annotations

import os
from typing import Any

import httpx

import mcp_config_validate


def validate_plain_servers(servers: list[dict]) -> dict:
    valid_ids: list[str] = []
    invalid: list[dict] = []
    for server in servers or []:
        if not isinstance(server, dict):
            continue
        entry_id = str(server.get("id") or "").strip()
        try:
            mcp_config_validate.validate_server(server)
        except ValueError as e:
            invalid.append({"id": entry_id, "error": str(e)})
            continue
        if entry_id:
            valid_ids.append(entry_id)
    return {"valid_ids": valid_ids, "invalid": invalid}


def post_enclave_mcp_validate(
    api_key: str | None,
    mcp_servers: list[dict],
    *,
    runtime_token: str | None = None,
) -> dict:
    enclave_url = os.environ.get("FEEDLING_ENCLAVE_URL", "").rstrip("/")
    if not enclave_url:
        raise RuntimeError("enclave_unavailable")
    if runtime_token:
        auth_headers = {"X-Feedling-Runtime-Token": runtime_token}
    elif api_key:
        auth_headers = {"X-API-Key": api_key}
    else:
        raise RuntimeError("api_key_unavailable")
    try:
        with httpx.Client(timeout=20, verify=False) as client:
            resp = client.post(
                f"{enclave_url}/v1/mcp/validate",
                headers=auth_headers,
                json={"mcp_servers": mcp_servers},
            )
    except httpx.HTTPError as e:
        raise RuntimeError(f"enclave_error:{type(e).__name__}") from e
    if resp.status_code >= 400:
        raise RuntimeError(f"enclave_http_{resp.status_code}:{resp.text[:180]}")
    response: Any = resp.json()
    if not isinstance(response, dict):
        raise RuntimeError("enclave_invalid_mcp_response")
    return response
