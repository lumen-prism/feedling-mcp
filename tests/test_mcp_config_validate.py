from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import mcp_config_validate as v  # noqa: E402


def _srv(**kw):
    base = {"slug": "github", "url": "https://mcp.example.com", "transport": "http"}
    base.update(kw)
    return base


def test_ok():
    v.validate_server(_srv())  # no raise


def test_ok_with_port_and_path():
    v.validate_server(_srv(url="https://mcp.example.com:8443/sse"))


def test_bad_slug():
    for s in ["Git Hub", "gh!", "", "UPPER", "a.b", "a/b"]:
        with pytest.raises(ValueError):
            v.validate_server(_srv(slug=s))


def test_non_https():
    for u in ["http://x.com", "ws://x.com", "ftp://x.com", "x.com"]:
        with pytest.raises(ValueError):
            v.validate_server(_srv(url=u))


def test_bad_transport():
    for t in ["sse", "stdio", ""]:
        with pytest.raises(ValueError):
            v.validate_server(_srv(transport=t))


def test_ssrf_hosts():
    for u in [
        "https://localhost/x",
        "https://sub.localhost/x",
        "https://127.0.0.1/x",
        "https://10.0.0.1/x",
        "https://172.16.5.4/x",
        "https://192.168.1.5/x",
        "https://169.254.169.254/x",
        "https://[::1]/x",
        "https://0.0.0.0/x",
    ]:
        with pytest.raises(ValueError):
            v.validate_server(_srv(url=u))


def test_public_ip_ok():
    v.validate_server(_srv(url="https://8.8.8.8/mcp"))
