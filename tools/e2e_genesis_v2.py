#!/usr/bin/env python3
"""Genesis v2 foreground-fast onboarding e2e harness.

This script drives the deployed TEST API through the plaintext genesis import
path, then records the two user-visible milestones:

1. foreground-ready: job.status first becomes done, so the app can greet.
2. full-done: background enrichment reaches genesis_v2_done.

It intentionally does not print API keys. Provider key is read from
GENESIS_E2E_PROVIDER_API_KEY when --register is used.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from nacl.public import PrivateKey
except Exception as e:  # noqa: BLE001
    print(json.dumps({"ok": False, "error": f"PyNaCl required: {e}"}, ensure_ascii=False))
    sys.exit(2)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def _http(method: str, url: str, api_key: str = "", *, json_body: dict | None = None, timeout: float = 60.0) -> tuple[int, dict]:
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(raw or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw or "{}")
        except Exception:
            body = {"error": raw[:500]}
        return e.code, body
    except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
        return 0, {"error": f"{type(e).__name__}:{e}"}


def _require_ok(step: str, status: int, body: dict) -> dict:
    if status < 200 or status >= 300:
        raise RuntimeError(f"{step} failed status={status} body={body}")
    return body


def _register_user(api_url: str) -> tuple[str, str]:
    for _ in range(5):
        sk = PrivateKey.generate()
        public_key = base64.b64encode(bytes(sk.public_key)).decode("ascii")
        status, body = _http("POST", f"{api_url}/v1/users/register", json_body={"public_key": public_key})
        if status == 409:
            continue
        _require_ok("register", status, body)
        api_key = str(body.get("api_key") or body.get("apiKey") or "")
        user_id = str(body.get("user_id") or body.get("userId") or "")
        if api_key and user_id:
            return api_key, user_id
    raise RuntimeError("register failed after retries")


def _setup_provider(api_url: str, api_key: str, *, provider: str, model: str, base_url: str) -> None:
    provider_key = os.environ.get("GENESIS_E2E_PROVIDER_API_KEY", "").strip()
    if not provider_key:
        raise RuntimeError("GENESIS_E2E_PROVIDER_API_KEY env required when --register is used")
    status, body = _http("POST", f"{api_url}/v1/model_api/setup", api_key, json_body={
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": provider_key,
    })
    _require_ok("model_api/setup", status, body)
    test_status = body.get("test_status")
    if test_status not in ("ok", None):
        raise RuntimeError(f"model_api/setup did not validate: {body}")


def _upload_plaintext(api_url: str, api_key: str, transcript_path: Path, *, client_job_id: str) -> str:
    content = transcript_path.read_text(encoding="utf-8")
    payload = {
        "format": "auto",
        "content": content,
        "fresh_start": False,
        "client_job_id": client_job_id,
    }
    status, body = _http("POST", f"{api_url}/v1/genesis/imports/plaintext", api_key, json_body=payload)
    _require_ok("genesis plaintext upload", status, body)
    job_id = str((body.get("job") or {}).get("job_id") or body.get("job_id") or "")
    if not job_id:
        raise RuntimeError(f"upload returned no job_id: {body}")
    return job_id


def _job_stage(body: dict) -> str:
    job = body.get("job") if isinstance(body.get("job"), dict) else {}
    output = job.get("output") if isinstance(job.get("output"), dict) else {}
    return str(output.get("stage") or "")


def _job_status(body: dict) -> str:
    job = body.get("job") if isinstance(body.get("job"), dict) else {}
    return str(job.get("status") or "").lower()


def _memory_index(api_url: str, api_key: str) -> list[dict]:
    status, body = _http("POST", f"{api_url}/v1/memory/index", api_key, json_body={})
    if status < 200 or status >= 300:
        return []
    items = body.get("items")
    return items if isinstance(items, list) else []


def _norm(text: str) -> str:
    text = re.sub(r"\s+", "", str(text or "").lower())
    text = re.sub(r"[，。！？、,.!?;；:：\"'“”‘’`（）()【】\\[\\]-]", "", text)
    return text[:180]


def _duplicate_summaries(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    label: dict[str, str] = {}
    for item in items:
        key = _norm(str(item.get("summary") or item.get("title") or ""))
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        label.setdefault(key, str(item.get("summary") or item.get("title") or "")[:120])
    return {label[k]: v for k, v in counts.items() if v > 1}


def _bigrams(text: str) -> set[str]:
    text = _norm(text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _similarity(a: str, b: str) -> float:
    left = _bigrams(a)
    right = _bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def _fuzzy_duplicate_pairs(items: list[dict], *, threshold: float = 0.42) -> list[dict]:
    pairs: list[dict] = []
    summaries = [
        (str(item.get("id") or ""), str(item.get("summary") or item.get("title") or ""))
        for item in items
        if str(item.get("summary") or item.get("title") or "").strip()
    ]
    for idx, (left_id, left_summary) in enumerate(summaries):
        for right_id, right_summary in summaries[idx + 1:]:
            score = _similarity(left_summary, right_summary)
            if score >= threshold:
                pairs.append({
                    "score": round(score, 3),
                    "left_id": left_id,
                    "right_id": right_id,
                    "left": left_summary[:140],
                    "right": right_summary[:140],
                })
    return pairs


def _foreground_duplicate_hits(foreground: list[dict], final: list[dict]) -> dict[str, int]:
    final_counts: dict[str, int] = {}
    final_labels: dict[str, str] = {}
    for item in final:
        key = _norm(str(item.get("summary") or ""))
        if not key:
            continue
        final_counts[key] = final_counts.get(key, 0) + 1
        final_labels.setdefault(key, str(item.get("summary") or "")[:120])
    out: dict[str, int] = {}
    for item in foreground:
        key = _norm(str(item.get("summary") or ""))
        if key and final_counts.get(key, 0) > 1:
            out[final_labels.get(key, key)] = final_counts[key]
    return out


def _foreground_fuzzy_duplicate_pairs(foreground: list[dict], final: list[dict], *, threshold: float = 0.42) -> list[dict]:
    pairs: list[dict] = []
    for fg in foreground:
        fg_id = str(fg.get("id") or "")
        fg_summary = str(fg.get("summary") or "")
        if not fg_summary:
            continue
        for item in final:
            item_id = str(item.get("id") or "")
            if item_id == fg_id:
                continue
            item_summary = str(item.get("summary") or "")
            score = _similarity(fg_summary, item_summary)
            if score >= threshold:
                pairs.append({
                    "score": round(score, 3),
                    "foreground_id": fg_id,
                    "duplicate_id": item_id,
                    "foreground": fg_summary[:140],
                    "duplicate": item_summary[:140],
                })
    return pairs


def _poll(api_url: str, api_key: str, job_id: str, *, timeout: float, poll: float) -> tuple[dict, list[dict], list[dict], list[dict]]:
    start = time.time()
    deadline = start + timeout
    timeline: list[dict] = []
    foreground_items: list[dict] = []
    final_items: list[dict] = []
    first_done_seen = False
    last_body: dict = {}

    while time.time() < deadline:
        status, body = _http("GET", f"{api_url}/v1/genesis/imports/{job_id}", api_key)
        if status >= 200 and status < 300:
            last_body = body
            job_status = _job_status(body)
            stage = _job_stage(body)
            elapsed = round(time.time() - start, 2)
            if not timeline or timeline[-1].get("status") != job_status or timeline[-1].get("stage") != stage:
                timeline.append({"t_sec": elapsed, "status": job_status, "stage": stage})
            if job_status == "done" and not first_done_seen:
                first_done_seen = True
                foreground_items = _memory_index(api_url, api_key)
                timeline.append({"t_sec": elapsed, "event": "foreground_ready_observed", "memory_count": len(foreground_items)})
            if stage in {"genesis_v2_done", "genesis_v2_background_deferred"}:
                final_items = _memory_index(api_url, api_key)
                timeline.append({"t_sec": elapsed, "event": "full_terminal_observed", "memory_count": len(final_items)})
                return body, timeline, foreground_items, final_items
            if job_status == "failed":
                final_items = _memory_index(api_url, api_key)
                return body, timeline, foreground_items, final_items
        time.sleep(poll)

    final_items = _memory_index(api_url, api_key)
    return last_body, timeline, foreground_items, final_items


def run(args: argparse.Namespace) -> int:
    api_url = args.api_url.rstrip("/")
    api_key = args.api_key.strip()
    user_id = args.user_id.strip()
    if args.register:
        api_key, user_id = _register_user(api_url)
        _setup_provider(api_url, api_key, provider=args.provider, model=args.model, base_url=args.base_url)
    elif not api_key:
        raise RuntimeError("--api-key required unless --register")

    transcript_path = Path(args.transcript)
    client_job_id = "genesis_v2_e2e_" + hashlib.sha256(
        (transcript_path.read_text(encoding="utf-8") + str(time.time())).encode("utf-8")
    ).hexdigest()[:24]
    job_id = _upload_plaintext(api_url, api_key, transcript_path, client_job_id=client_job_id)
    body, timeline, foreground_items, final_items = _poll(
        api_url, api_key, job_id, timeout=args.timeout, poll=args.poll
    )

    stage = _job_stage(body)
    job = body.get("job") if isinstance(body.get("job"), dict) else {}
    persona = body.get("persona") if isinstance(body.get("persona"), dict) else {}
    foreground_ready = any(e.get("event") == "foreground_ready_observed" for e in timeline)
    full_done = stage == "genesis_v2_done"
    background_deferred = stage == "genesis_v2_background_deferred"
    duplicate_final = _duplicate_summaries(final_items)
    duplicate_core = _foreground_duplicate_hits(foreground_items, final_items)
    fuzzy_duplicate_final = _fuzzy_duplicate_pairs(final_items)
    fuzzy_duplicate_core = _foreground_fuzzy_duplicate_pairs(foreground_items, final_items)

    foreground_t = next((e.get("t_sec") for e in timeline if e.get("event") == "foreground_ready_observed"), None)
    full_t = next((e.get("t_sec") for e in timeline if e.get("event") == "full_terminal_observed"), None)
    persona_landed = bool(persona.get("content_envelope") or persona.get("sha256") or job.get("persona_ref"))

    checks = {
        "foreground_ready": foreground_ready,
        "foreground_core_memory_count_1_to_5": 1 <= len(foreground_items) <= 5,
        "final_memory_count_gte_foreground": len(final_items) >= len(foreground_items),
        "no_duplicate_summaries": not duplicate_final,
        "foreground_core_not_duplicated": not duplicate_core,
        "no_fuzzy_duplicate_summaries": not fuzzy_duplicate_final,
        "foreground_core_not_fuzzy_duplicated": not fuzzy_duplicate_core,
        "persona_landed": persona_landed,
        # There is no public voice blob endpoint in this response; genesis_v2_done is
        # the observable signal that background wrote persona + voice artifacts.
        "voice_inferred_from_full_done_stage": full_done,
    }
    if background_deferred:
        checks["voice_inferred_from_full_done_stage"] = False

    failed = [name for name, ok in checks.items() if not ok]
    result = {
        "ok": not failed,
        "failed": failed,
        "user_id": user_id,
        "job_id": job_id,
        "timeline": timeline,
        "foreground_ready_sec": foreground_t,
        "full_done_sec": full_t,
        "job_status": _job_status(body),
        "job_stage": stage,
        "foreground_memory_count": len(foreground_items),
        "final_memory_count": len(final_items),
        "persona_landed": persona_landed,
        "voice_check_note": "No public voice blob in status; stage=genesis_v2_done is used as the deploy-level signal.",
        "duplicate_summaries": duplicate_final,
        "foreground_core_duplicates": duplicate_core,
        "fuzzy_duplicate_summaries": fuzzy_duplicate_final,
        "foreground_core_fuzzy_duplicates": fuzzy_duplicate_core,
        "foreground_samples": [
            {
                "id": item.get("id"),
                "bucket": item.get("bucket") or item.get("bucket_refs"),
                "summary": item.get("summary"),
            }
            for item in foreground_items[:8]
        ],
        "final_samples": [
            {
                "id": item.get("id"),
                "bucket": item.get("bucket") or item.get("bucket_refs"),
                "summary": item.get("summary"),
            }
            for item in final_items[:12]
        ],
        "checks": checks,
    }
    print(_json(result))
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Genesis v2 foreground-fast e2e against a deployed API.")
    parser.add_argument("--api-url", default=os.environ.get("FEEDLING_API_URL", "https://test-api.feedling.app"))
    parser.add_argument("--register", action="store_true", help="Register a throwaway user and setup model_api.")
    parser.add_argument("--provider", default=os.environ.get("GENESIS_E2E_PROVIDER", "openai"))
    parser.add_argument("--model", default=os.environ.get("GENESIS_E2E_MODEL", "claude-sonnet-4-6"))
    parser.add_argument("--base-url", default=os.environ.get("GENESIS_E2E_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("FEEDLING_API_KEY", ""))
    parser.add_argument("--user-id", default=os.environ.get("FEEDLING_USER_ID", ""))
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--poll", type=float, default=5)
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
