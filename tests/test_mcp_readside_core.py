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
