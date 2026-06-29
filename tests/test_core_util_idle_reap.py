import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from core import util


def test_idle_reap_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_IDLE_REAP_ENABLED", raising=False)
    assert util.agent_idle_reap_enabled() is False


def test_idle_reap_enabled_truthy(monkeypatch):
    monkeypatch.setenv("AGENT_IDLE_REAP_ENABLED", "true")
    assert util.agent_idle_reap_enabled() is True
    monkeypatch.setenv("AGENT_IDLE_REAP_ENABLED", "1")
    assert util.agent_idle_reap_enabled() is True


def test_idle_threshold_default(monkeypatch):
    monkeypatch.delenv("AGENT_IDLE_THRESHOLD_SEC", raising=False)
    assert util.agent_idle_threshold_sec() == 1080


def test_idle_threshold_override(monkeypatch):
    monkeypatch.setenv("AGENT_IDLE_THRESHOLD_SEC", "300")
    assert util.agent_idle_threshold_sec() == 300


def test_idle_threshold_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("AGENT_IDLE_THRESHOLD_SEC", "not-a-number")
    assert util.agent_idle_threshold_sec() == 1080
    monkeypatch.setenv("AGENT_IDLE_THRESHOLD_SEC", "0")
    assert util.agent_idle_threshold_sec() == 1  # floored at 1
