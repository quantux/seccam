import importlib
import json
import sys
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

import camera_watchdog as wd  # noqa: E402

CFG = {
    "reboot_cooldown_seconds": 900,
    "offline_threshold_seconds": 180,
    "gate_ips": ["10.0.0.1"],
    "cameras": [{"name": "camera_1"}, {"name": "camera_2"}],
}


@pytest.fixture(autouse=True)
def clean_status(monkeypatch):
    wd._status.clear()
    monkeypatch.setattr(wd, "host_up", lambda ip, timeout=2: True)
    monkeypatch.setattr(wd, "load_settings", lambda: CFG)
    yield
    wd._status.clear()


def set_cam(name, **kw):
    st = wd._get_status(name)
    st.update(kw)


def test_healthy_all(monkeypatch):
    for n in ("camera_1", "camera_2"):
        set_cam(n, online=True, recording=True, unhealthy_since=None, stage=None)
    s = wd.health_state(CFG)
    assert s["ok"] is True and s["alert"] is False


def test_sick_but_not_tried_is_ok(monkeypatch):
    import time
    now = time.time()
    set_cam("camera_1", online=True, recording=False, unhealthy_since=now - 60,
            stage=None, last_reboot=None)
    set_cam("camera_2", online=True, recording=True)
    s = wd.health_state(CFG)
    # 60s < threshold(180) -> nao alerta
    assert s["cameras"]["camera_1"]["alert"] is False
    assert s["ok"] is False


def test_restart_in_progress_is_ok(monkeypatch):
    import time
    now = time.time()
    set_cam("camera_1", online=False, recording=False, unhealthy_since=now - 600,
            stage="camera", last_reboot=now - 60)
    set_cam("camera_2", online=True, recording=True)
    s = wd.health_state(CFG)
    assert s["cameras"]["camera_1"]["alert"] is False


def test_sick_after_plug_reboot_alerts(monkeypatch):
    import time
    now = time.time()
    set_cam("camera_1", online=False, recording=False, unhealthy_since=now - 1200,
            stage="plug", last_reboot=now - 300)
    set_cam("camera_2", online=True, recording=True)
    s = wd.health_state(CFG)
    assert s["cameras"]["camera_1"]["alert"] is True
    assert s["alert"] is True


def test_sick_after_cooldown_alerts(monkeypatch):
    import time
    now = time.time()
    set_cam("camera_1", online=True, recording=False, unhealthy_since=now - 2000,
            stage=None, last_reboot=now - 1000)  # > cooldown 900
    set_cam("camera_2", online=True, recording=True)
    s = wd.health_state(CFG)
    assert s["cameras"]["camera_1"]["alert"] is True


def test_gates_missing_blocks_alert(monkeypatch):
    import time
    now = time.time()
    set_cam("camera_1", online=False, recording=False, unhealthy_since=now - 2000,
            stage="plug", last_reboot=now - 1000)
    set_cam("camera_2", online=True, recording=True)
    s = wd.health_state(CFG)
    # gates ok nesse teste -> alerta
    assert s["gates_ok"] is True
    monkeypatch.setattr(wd, "host_up", lambda ip, timeout=2: False)
    s2 = wd.health_state(CFG)
    assert s2["gates_ok"] is False


def test_health_api_status_codes(monkeypatch, tmp_path):
    import time
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    now = time.time()
    set_cam("camera_1", online=False, recording=False, unhealthy_since=now - 2000,
            stage="plug", last_reboot=now - 1000)
    set_cam("camera_2", online=True, recording=True)

    sys.modules.pop("api.health", None)
    health = importlib.import_module("api.health")
    app = fastapi.FastAPI()
    app.include_router(health.router)
    c = TestClient(app)
    assert c.get("/api/health").status_code == 503
    r = c.get("/api/health/camera_1")
    assert r.status_code == 503 and r.json()["alert"] is True
    assert c.get("/api/health/camera_2").status_code == 200
    assert c.get("/api/health/nope").status_code == 404
