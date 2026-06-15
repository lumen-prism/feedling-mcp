"""Cheap context fields for every agent wake.

app.py splices snapshot_for_wake(user_id, api_key) into the per-turn
context_payload so the agent always has the user's coarse current state
(place_label, motion, battery, user_state, etc.) without spending a tool call.
Unauthorized/stale fields are null — the agent treats null as "not permitted,
don't infer."

Perception values are encrypted at rest. The flat agent-facing shape is
produced by the ENCLAVE (GET /v1/perception/snapshot there — it pulls the
ciphertext shape from the backend and decrypts inside the TEE, mirroring the
chat-history path). Without an api_key or a reachable enclave this degrades to
the cleartext operational fields with every sensitive field null — same shape,
same null contract.
"""
from __future__ import annotations

import logging

from . import catalog, service

log = logging.getLogger("perception.wake")


def _flat_fallback(snap: dict) -> dict:
    """Cleartext-only flat shape: operational fields pass through; every
    encrypted signal's output fields are null (the backend can't decrypt)."""
    flat = dict(snap.get("fields") or {})
    for sig in catalog.SIGNALS.values():
        cap = catalog.CAPABILITIES.get(sig.capability)
        if sig.encrypted and cap and cap.context_field:
            for f in sig.outputs:
                flat.setdefault(f, None)
    flat["recent_apps"] = []
    return flat


def snapshot_for_wake(user_id: str, api_key: str | None = None) -> tuple[dict, str]:
    """Returns (flat_snapshot, error). error is "" on the full decrypted path
    and a reason string when degraded to cleartext-only (the snapshot is still
    usable — sensitive fields are just null)."""
    try:
        if api_key:
            from core import enclave as core_enclave  # lazy: keeps perception importable standalone
            data, err = core_enclave._enclave_get_json_for_gate(
                "/v1/perception/snapshot", api_key)
            if isinstance(data, dict) and isinstance(data.get("snapshot"), dict) and not err:
                return data["snapshot"], ""
            log.warning("perception enclave snapshot failed for %s: %s", user_id, err)
            return _flat_fallback(service.snapshot(user_id)), err or "enclave_invalid_response"
        return _flat_fallback(service.snapshot(user_id)), "api_key_unavailable"
    except Exception as e:
        log.error("snapshot_for_wake(%s) failed: %s", user_id, e)
        return {}, f"snapshot_error:{type(e).__name__}"
