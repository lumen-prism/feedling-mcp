"""Extended Perception — a self-contained backend feature module.

Gives the resident agent coarse, permission-gated awareness of the user's
context (location label, wifi label, app category, motion, device signals, iOS
Focus, plus Tier 2: calendar / health / photos) so it can act like a companion
even when screen broadcast is off. Sensitive values are encrypted at rest (v1
envelopes); only the enclave or the user's device can read them.

Integration with the rest of the backend is two one-liners in app.py:
    from perception import register as register_perception
    register_perception(app)
and, in the per-turn wake context (context_payload):
    from perception import snapshot_for_wake
    snap, err = snapshot_for_wake(store.user_id, api_key)

Everything else (routing, DB access, wake triggering) lives here.

NOTE: imports here are intentionally LAZY (inside the functions): the enclave
imports `perception.catalog` for the signal table, and must not drag in
routes/wake -> service -> store -> db (psycopg) at import time.
"""
from __future__ import annotations

__all__ = ["register", "snapshot_for_wake"]


def register(app) -> None:
    """Mount the /v1/perception blueprint onto the Flask app."""
    from .routes import bp
    app.register_blueprint(bp)


def snapshot_for_wake(user_id: str, api_key: str | None = None) -> tuple[dict, str]:
    from . import wake
    return wake.snapshot_for_wake(user_id, api_key)
