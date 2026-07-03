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


def renderable_servers(servers: list[dict]) -> dict:
    rendered: list[dict] = []
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
        if server.get("enabled") is False:
            continue
        rendered.append({
            "id": entry_id,
            "display_name": str(server.get("display_name") or server.get("name") or ""),
            "slug": str(server.get("slug") or "").strip(),
            "url": str(server.get("url") or "").strip(),
            "transport": str(server.get("transport") or "http").strip(),
            "headers": server.get("headers") if isinstance(server.get("headers"), dict) else {},
            "enabled": True,
        })
    return {"servers": rendered, "invalid": invalid}


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


def post_enclave_mcp_render(
    enclave_url: str,
    mcp_servers: list[dict],
    *,
    runtime_token: str,
) -> dict:
    if not enclave_url:
        raise RuntimeError("enclave_unavailable")
    if not runtime_token:
        raise RuntimeError("runtime_token_unavailable")
    try:
        with httpx.Client(timeout=20, verify=False) as client:
            resp = client.post(
                f"{enclave_url.rstrip('/')}/v1/mcp/render",
                headers={"X-Feedling-Runtime-Token": runtime_token},
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
