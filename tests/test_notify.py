import sys
import time
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

import notify  # noqa: E402


class Resp:
    def __init__(self, code=200, text=""):
        self.status_code = code
        self.text = text


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    notify._QUEUE.clear()
    notify._SENT.clear()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("NTFY_URL", "http://ntfy")
    monkeypatch.setenv("NTFY_TOPIC", "top")
    monkeypatch.setenv("NOTIFY_ENABLED", "1")
    yield
    notify._QUEUE.clear()


def test_disabled_does_not_enqueue(monkeypatch):
    monkeypatch.setenv("NOTIFY_ENABLED", "0")
    notify.notify("t", "x")
    assert notify._QUEUE == []


def test_both_channels_called(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, **kw: calls.append(url) or Resp())
    notify.notify("T", "texto")
    notify._send(notify._QUEUE.pop(0))
    assert any("api.telegram.org" in c for c in calls)
    assert any(c.startswith("http://ntfy/") for c in calls)


def test_telegram_fails_ntfy_still_sent(monkeypatch):
    calls = []

    def post(url, **kw):
        calls.append(url)
        if "telegram" in url:
            raise RuntimeError("sem internet")
        return Resp()

    monkeypatch.setattr(notify.requests, "post", post)
    notify._send(("T", "texto", "high"))
    assert any(c.startswith("http://ntfy/") for c in calls)


def test_min_interval_throttles(monkeypatch):
    monkeypatch.setattr(notify.requests, "post", lambda url, **kw: Resp())
    notify.notify("T", "a", key="k", min_interval=60)
    notify.notify("T", "b", key="k", min_interval=60)
    assert len(notify._QUEUE) == 1


def test_queue_caps_at_50(monkeypatch):
    for i in range(80):
        notify.notify("T", str(i))
    assert len(notify._QUEUE) == 50
