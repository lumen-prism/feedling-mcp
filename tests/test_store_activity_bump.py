import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import db
from core.store import UserStore


def test_append_chat_bumps_last_active(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "bump_agent_last_active", lambda uid: calls.append(uid))
    store = UserStore("u_chat")
    env = {"id": "m1", "v": 1, "body_ct": "x", "nonce": "n", "K_user": "k"}
    store.append_chat("user", "app", env)
    assert "u_chat" in calls


def test_save_frame_bumps_last_active(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "bump_agent_last_active", lambda uid: calls.append(uid))
    monkeypatch.setattr(db, "frame_upsert", lambda *a, **k: True)
    from screen import frames
    store = UserStore("u_frame")
    env = {"v": 1, "id": "f1", "body_ct": "x"}
    frames._save_frame_envelope(store, {"ts": 1.0, "envelope": env}, env)
    assert "u_frame" in calls
