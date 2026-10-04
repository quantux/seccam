"""Watchdog das cameras.

Monitora se cada caminho do mediamtx continua no ar. Se uma camera ficar
offline por tempo suficiente E os APs (``gate_ips``) estiverem no ar, reinicia
a camera desligando/ligando a tomada Tapo via python-kasa.

Regras:
- ``unknown`` (nao conseguiu falar com o mediamtx) nao conta como offline,
  para nao reiniciar tudo se for o proprio mediamtx/rede que caiu.
- So reinicia se TODOS os ``gate_ips`` responderem (APs de pe).
- Cooldown evita reinicios em loop.
"""

import asyncio
import json
import logging
import os
import socket
import subprocess
import threading
import time
from urllib.parse import urlparse

from config import CAMERAS_JSON, ENABLE_WATCHDOG, WATCHDOG_JSON

log = logging.getLogger("watchdog")

DEFAULTS = {
    "enabled": True,
    "check_interval_seconds": 30,
    "offline_threshold_seconds": 180,
    "reboot_cooldown_seconds": 900,
    "power_off_seconds": 15,
    "gate_ips": [],
    "tapo": {"username": "", "password": ""},
    "cameras": [],
}

_lock = threading.Lock()
_status = {}  # name -> {online, offline_since, last_reboot, last_message}


# ── Config ────────────────────────────────────────────────────────────────────
def _merge(data):
    out = json.loads(json.dumps(DEFAULTS))
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "tapo" and isinstance(v, dict):
                out["tapo"].update(v)
            else:
                out[k] = v
    return out


def load_settings():
    if not os.path.exists(WATCHDOG_JSON):
        return json.loads(json.dumps(DEFAULTS))
    try:
        with open(WATCHDOG_JSON, "r") as f:
            return _merge(json.load(f))
    except Exception:
        return json.loads(json.dumps(DEFAULTS))


def save_settings(data):
    with _lock:
        merged = _merge(data)
        os.makedirs(os.path.dirname(WATCHDOG_JSON), exist_ok=True)
        with open(WATCHDOG_JSON, "w") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        return merged


def _load_cameras():
    if not os.path.exists(CAMERAS_JSON):
        return []
    try:
        with open(CAMERAS_JSON, "r") as f:
            return json.load(f).get("cameras", [])
    except Exception:
        return []


def _rtsp_url_for(name):
    for cam in _load_cameras():
        if cam.get("name") == name:
            return cam.get("rtsp_url")
    return None


# ── Probes ────────────────────────────────────────────────────────────────────
def probe_path(rtsp_url, timeout=5):
    """Retorna 'online', 'offline' ou 'unknown' consultando o mediamtx."""
    if not rtsp_url:
        return "unknown"
    u = urlparse(rtsp_url)
    host = u.hostname
    port = u.port or 554
    if not host:
        return "unknown"
    req = (
        f"DESCRIBE {rtsp_url} RTSP/1.0\r\n"
        "CSeq: 1\r\n"
        "Accept: application/sdp\r\n"
        "User-Agent: seccam-watchdog\r\n\r\n"
    )
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(req.encode())
            data = s.recv(256).decode(errors="ignore")
    except OSError:
        return "unknown"
    if data.startswith("RTSP/1.0 200"):
        return "online"
    return "offline"


def host_up(ip, timeout=2):
    """Ping simples (o container tem iputils-ping)."""
    if not ip:
        return False
    try:
        res = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 2,
        )
        return res.returncode == 0
    except Exception:
        return False


# ── Tomada via python-kasa ────────────────────────────────────────────────────
async def _reboot_plug(ip, username, password, off_seconds):
    from kasa import Credentials, Discover

    creds = Credentials(username, password)

    dev = await Discover.discover_single(ip, credentials=creds)
    if dev is None:
        raise RuntimeError(f"tomada {ip} nao encontrada")
    try:
        await dev.update()
        await dev.turn_off()
    finally:
        await dev.disconnect()

    await asyncio.sleep(off_seconds)

    dev = await Discover.discover_single(ip, credentials=creds)
    if dev is None:
        raise RuntimeError(f"tomada {ip} nao respondeu apos desligar")
    try:
        await dev.turn_on()
        await dev.update()
    finally:
        await dev.disconnect()


def reboot_plug(ip, username, password, off_seconds):
    asyncio.run(_reboot_plug(ip, username, password, off_seconds))


# ── Estado / loop ─────────────────────────────────────────────────────────────
def _get_status(name):
    with _lock:
        return _status.setdefault(
            name,
            {"online": None, "offline_since": None, "last_reboot": None, "last_message": ""},
        )


def get_status():
    with _lock:
        return {k: dict(v) for k, v in _status.items()}


def gates_status(gate_ips):
    return {ip: host_up(ip) for ip in (gate_ips or [])}


def _gate_ok(gate_ips):
    return all(host_up(ip) for ip in (gate_ips or []))


def _tick():
    cfg = load_settings()
    if not cfg.get("enabled"):
        return

    threshold = int(cfg.get("offline_threshold_seconds", 180))
    cooldown = int(cfg.get("reboot_cooldown_seconds", 900))
    off_seconds = int(cfg.get("power_off_seconds", 15))
    gate_ips = cfg.get("gate_ips") or []
    tapo = cfg.get("tapo") or {}
    now = time.time()

    gates = _gate_ok(gate_ips)

    for cam in cfg.get("cameras", []):
        name = cam.get("name")
        plug = cam.get("plug_ip")
        if not name:
            continue

        st = _get_status(name)
        state = probe_path(_rtsp_url_for(name))

        if state == "online":
            st["online"] = True
            st["offline_since"] = None
            st["last_message"] = "online"
            continue

        st["online"] = False

        if state == "unknown":
            st["last_message"] = "indeterminado (mediamtx/rede?)"
            continue

        if st["offline_since"] is None:
            st["offline_since"] = now
        outage = now - st["offline_since"]
        since_reboot = now - (st["last_reboot"] or 0)

        if outage < threshold:
            st["last_message"] = f"offline ha {int(outage)}s"
            continue
        if not gates:
            st["last_message"] = "offline; APs (gate) fora do ar"
            continue
        if since_reboot < cooldown:
            st["last_message"] = f"offline; cooldown {int(cooldown - since_reboot)}s"
            continue
        if not plug or not tapo.get("username"):
            st["last_message"] = "offline; tomada/credenciais nao configuradas"
            continue

        try:
            log.info("Reiniciando tomada de %s (%s)", name, plug)
            reboot_plug(plug, tapo["username"], tapo.get("password", ""), off_seconds)
            st["offline_since"] = None
            st["last_message"] = "tomada reiniciada"
        except Exception as exc:
            st["last_message"] = f"falha ao reiniciar tomada: {exc}"
            log.warning("Falha ao reiniciar tomada de %s: %s", name, exc)
        finally:
            st["last_reboot"] = now


def _run():
    log.info("Watchdog iniciado")
    while True:
        try:
            _tick()
        except Exception as exc:
            log.warning("Erro no watchdog: %s", exc)
        cfg = load_settings()
        time.sleep(max(5, int(cfg.get("check_interval_seconds", 30))))


def start_watchdog():
    if not ENABLE_WATCHDOG:
        log.info("Watchdog desativado (ENABLE_WATCHDOG=0)")
        return
    threading.Thread(target=_run, name="watchdog", daemon=True).start()
