"""Pure validation for user-supplied MCP server config (SSRF + slug + scheme).

Lives outside the DB/enclave stack so it unit-tests without Postgres/nacl.
Called on the upsert path (after enclave-decrypt of the payload) and again
before rendering the config into the agent — see the MCP spec §5 security.

Raises ValueError on anything unsafe; returns None on OK.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

_SLUG_RE = re.compile(r"^[a-z0-9_-]+$")
_ALLOWED_TRANSPORTS = {"http"}  # v1: Streamable HTTP only


def _reject_ssrf_host(host: str) -> None:
    h = (host or "").strip().lower()
    if not h:
        raise ValueError("url must have a host")
    if h == "localhost" or h.endswith(".localhost"):
        raise ValueError("host not allowed (localhost)")
    # Literal IP? Reject loopback / private / link-local / reserved / etc.
    # (169.254.169.254 metadata IP is link-local, so covered.)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError(f"host not allowed (non-public IP {host})")
        return
    # Hostname (not a literal IP): we do NOT resolve DNS here (keeps this pure
    # and testable). DNS-rebind / resolves-to-private is a residual risk to
    # backstop at connect time; v1 relies on https + non-literal-private here.


def validate_server(plain: dict) -> None:
    """Validate a decrypted MCPServer payload dict. Raise ValueError if unsafe."""
    slug = str(plain.get("slug") or "")
    if not _SLUG_RE.match(slug):
        raise ValueError("slug must match ^[a-z0-9_-]+$")

    transport = str(plain.get("transport") or "")
    if transport not in _ALLOWED_TRANSPORTS:
        raise ValueError("transport must be 'http' (v1)")

    url = str(plain.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("url must start with https://")
    _reject_ssrf_host(parsed.hostname or "")
