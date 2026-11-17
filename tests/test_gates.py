#!/usr/bin/env python3
"""Host up: contagem de pings com tolerancia a perda isolada."""
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

import camera_watchdog as wd  # noqa: E402


class Out:
    def __init__(self, text, rc=0):
        self.stdout = text
        self.returncode = rc


def stats(recv, sent=3):
    pct = int((sent - recv) / sent * 100)
    return (f"\n--- 1.2.3.4 ping statistics ---\n"
            f"{sent} packets transmitted, {recv} received, "
            f"{pct}% packet loss, time 1000ms\n")


def test_all_lost(monkeypatch):
    monkeypatch.setattr(wd.subprocess, "run", lambda *a, **k: Out(stats(0), 1))
    assert wd.host_up("1.2.3.4") is False


def test_one_lost_still_up(monkeypatch):
    """Perder 1 de 3 nao derruba o gate (falso positivo do blip de Wi-Fi)."""
    monkeypatch.setattr(wd.subprocess, "run", lambda *a, **k: Out(stats(2), 0))
    assert wd.host_up("1.2.3.4") is True


def test_two_lost_is_down(monkeypatch):
    monkeypatch.setattr(wd.subprocess, "run", lambda *a, **k: Out(stats(1), 0))
    assert wd.host_up("1.2.3.4") is False


def test_timeout_is_down(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError()
    monkeypatch.setattr(wd.subprocess, "run", boom)
    assert wd.host_up("1.2.3.4") is False


def test_empty_ip(monkeypatch):
    assert wd.host_up("") is False


def test_uses_configured_count(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return Out(stats(3, 3))

    monkeypatch.setenv("GATE_PING_COUNT", "3")
    monkeypatch.setenv("GATE_PING_NEED", "2")
    monkeypatch.setattr(wd.subprocess, "run", run)
    assert wd.host_up("1.2.3.4") is True
    assert calls[0][:2] == ["ping", "-c"]
    assert calls[0][2] == "3"
