"""Flask blueprint for Extended Perception — all endpoints under /v1/perception.

Reuses app.require_user for auth (X-API-Key -> user_id). require_user is imported
lazily inside a helper so this module can be imported during app startup without
an import cycle.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import service

bp = Blueprint("perception", __name__, url_prefix="/v1/perception")


def _uid() -> str:
    from accounts.auth import require_user  # accounts sits below this blueprint — no cycle
    store = require_user()        # aborts 401 on bad auth
    return store.user_id


def _body() -> dict:
    return request.get_json(silent=True) or {}


# ---------------------------------------------------------------------------
# Generic sparse report (one endpoint writes all permission-gated state)
# ---------------------------------------------------------------------------

@bp.route("/report", methods=["POST"])
def report():
    """Single multiplexed ingest. Body may carry any of:
      - context_snapshot : list of signal items. Plain (operational) signals are
        {key, data, message} (data is a JSON string or "null"); SENSITIVE signals
        are {key, envelope, changed} — v1 envelope mandatory, plaintext rejected
        with 400. A `user_state` key sets the manual user_state.
      - items            : {kind: [{item_id?, ts?, expires_at?, envelope}, ...]}
        collections (sleep/workout/vitals); envelope mandatory.
    At least one must be present (else 400). `client_ts` (optional) timestamps the
    context_snapshot for the freshness/ordering guard. Photos use /photo/evaluate.

    `config` is GONE (410): geofence/SSID resolution moved on-device, and the
    config (home/work coordinates) is no longer stored server-side.
    """
    uid = _uid()
    payload = _body()
    results: dict = {}
    provided = False
    status = 200

    config = payload.get("config")
    if isinstance(config, dict) and config:
        return jsonify({
            "error": "config_no_longer_stored_server_side",
            "hint": "geofences/ssid_labels live on the device; resolve labels "
                    "locally and report encrypted signals instead",
        }), 410

    cs = payload.get("context_snapshot")
    if isinstance(cs, list) and cs:
        provided = True
        snap_results = service.ingest_snapshot(uid, cs, client_ts=payload.get("client_ts"))
        if any(isinstance(v, str) and v.startswith("rejected:")
               for v in snap_results.values()):
            status = 400  # plaintext-for-encrypted-signal / bad envelope (valid items still applied)
        results.update(snap_results)

    items = payload.get("items")
    if isinstance(items, dict) and items:
        provided = True
        item_results: dict = {}
        for kind, rows in items.items():
            out, code = service.items_ingest(uid, str(kind), rows)
            item_results[kind] = out
            if code != 200:
                status = 400  # surface rejected/malformed collection uploads, don't 200 them
        results["items"] = item_results

    if not provided:
        return jsonify({"error": "non-empty context_snapshot / items required"}), 400
    return jsonify({"results": results}), status


# ---------------------------------------------------------------------------
# Snapshot (agent: one call for current authorized+fresh state)
# ---------------------------------------------------------------------------

@bp.route("/snapshot", methods=["GET"])
def snapshot():
    return jsonify(service.snapshot(_uid()))


# ---------------------------------------------------------------------------
# Photos (two-step + sensitivity gate)
# ---------------------------------------------------------------------------

@bp.route("/photo/evaluate", methods=["POST"])
def photo_evaluate():
    """Single-step photo ingest: cleartext gate metadata + the encrypted image
    + an optional meta_envelope (encrypted sensitive context, e.g. place_label
    resolved on-device)."""
    uid = _uid()
    p = _body()
    out, code = service.photo_evaluate(
        uid, p.get("metadata") or {}, p.get("content_envelope"),
        meta_envelope=p.get("meta_envelope"))
    return jsonify(out), code


@bp.route("/photos", methods=["GET"])
def photos_recent():
    uid = _uid()
    limit = int(request.args.get("limit", 20))
    out, code = service.photos_recent(uid, limit=limit)
    return jsonify(out), code


@bp.route("/photo/<photo_id>/content", methods=["GET"])
def photo_content(photo_id):
    out, code = service.photo_content(_uid(), photo_id)
    return jsonify(out), code


# ---------------------------------------------------------------------------
# Tier 2 collections (calendar / health) — generic read (writes go via /report)
# ---------------------------------------------------------------------------

@bp.route("/items/<kind>", methods=["GET"])
def items_recent(kind):
    uid = _uid()
    limit = int(request.args.get("limit", 20))
    out, code = service.items_recent(uid, kind, limit=limit)
    return jsonify(out), code


# ---------------------------------------------------------------------------
# App usage — GET endpoint for iOS Shortcuts (everything in the URL, incl. ?key=)
# ---------------------------------------------------------------------------

@bp.route("/app_open", methods=["GET"])
def app_open():
    """iOS Shortcut "open app X -> Get Contents of URL" hits this. ALL params go
    in the query string, including the api key (?key=...), which the standard auth
    (_extract_api_key) already accepts. Example:
        GET /v1/perception/app_open?key=<apikey>&app=Instagram&category=social&ts=1733650000
    """
    uid = _uid()
    app = request.args.get("app") or request.args.get("bundle_id") or ""
    category = request.args.get("category")
    client_ts = request.args.get("ts") or request.args.get("client_ts")
    out, code = service.app_open(uid, app, category=category, client_ts=client_ts)
    return jsonify(out), code
