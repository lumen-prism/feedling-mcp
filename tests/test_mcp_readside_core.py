from __future__ import annotations

import sys
from pathlib import Path

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
