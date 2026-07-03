from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import mcp_readside_core as readside  # noqa: E402


def test_validate_plain_servers_returns_valid_and_invalid_ids():
    servers = [
        {"id": "ok", "slug": "github", "transport": "http", "url": "https://mcp.example.com"},
        {"id": "bad", "slug": "Bad Slug", "transport": "http", "url": "https://mcp.example.com"},
    ]

    out = readside.validate_plain_servers(servers)

    assert out["valid_ids"] == ["ok"]
    assert out["invalid"] == [{"id": "bad", "error": "slug must match ^[a-z0-9_-]+$"}]


def test_renderable_servers_filters_disabled_and_keeps_headers():
    out = readside.renderable_servers([
        {
            "id": "enabled",
            "display_name": "GitHub",
            "slug": "github",
            "transport": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer token"},
            "enabled": True,
        },
        {
            "id": "disabled",
            "slug": "off",
            "transport": "http",
            "url": "https://off.example.com/mcp/",
            "enabled": False,
        },
    ])

    assert out["servers"] == [{
        "id": "enabled",
        "display_name": "GitHub",
        "slug": "github",
        "url": "https://api.githubcopilot.com/mcp/",
        "transport": "http",
        "headers": {"Authorization": "Bearer token"},
        "enabled": True,
    }]
    assert out["invalid"] == []


def test_post_enclave_mcp_render_does_not_include_response_body_in_error(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return httpx.Response(500, text="bad config Authorization: Bearer secret-token")

    monkeypatch.setattr(readside.httpx, "Client", FakeClient)

    with pytest.raises(RuntimeError) as exc:
        readside.post_enclave_mcp_render(
            "https://enclave.test",
            [{"id": "mcp1"}],
            runtime_token="runtime-token",
        )

    assert str(exc.value) == "enclave_http_500"
    assert "secret-token" not in str(exc.value)
