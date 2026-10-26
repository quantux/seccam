"""Watchdog das cameras.

Monitora cada camera por dois sinais:
- o caminho no mediamtx continua no ar (``probe_path``); e
- o recorder continua gerando segmentos novos, ou seja, existem arquivos
  ``.ts`` recentes na pasta de videos (``recording_recent``).

Se a camera ficar doente por tempo suficiente E os APs (``gate_ips``)
estiverem no ar, tenta recuperar sempre nesta ordem:

1. reinicia a propria camera (Tapo, via ``pytapo``);
2. se ainda nao resolver, reinicia a tomada inteligente (Tapo, via python-kasa).

Regras:
- ``unknown`` (nao conseguiu falar com o mediamtx) nao conta como offline,
  para nao reiniciar tudo se for o proprio mediamtx/rede que caiu.
- So age se TODOS os ``gate_ips`` responderem (APs de pe).
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

from config import CAMERAS_JSON, ENABLE_WATCHDOG, VIDEOS_FOLDER, WATCHDOG_JSON

log = logging.getLogger("watchdog")

DEFAULTS = {
    "enabled": True,
    "check_interval_seconds": 30,
    "offline_threshold_seconds": 180,
    # Gravacao considerada parada se nao houver arquivo .ts novo por X segundos.
    "stall_seconds": 300,
    # Tempo de espera para a camera voltar apos o reboot antes de partir p/ tomada.
    "camera_reboot_wait_seconds": 180,
    # Intervalo minimo entre reboots de cameras (evita rate-limit do Tapo).
    "camera_reboot_min_interval_seconds": 30,
    # Quando o Tapo responde "Temporary Suspension", espera antes de tentar de novo.
    "rate_limit_cooldown_seconds": 1800,
    "reboot_cooldown_seconds": 900,
    "power_off_seconds": 15,
    "gate_ips": [],
    "tapo": {
        "username": "",
        "password": "",
        # Conta local da camera (a mesma usada no RTSP). Se vazio, tenta
        # extrair das credenciais da rtsp_url do cameras.json.
        "camera_username": "",
        "camera_password": "",
    },
    "cameras": [],
}

_lock = threading.Lock()
_status = {}  # name -> dict de estado


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


def recording_recent(name, max_age):
    """True se a pasta da camera tem algum .ts escrito nos ultimos max_age s."""
    folder = os.path.join(VIDEOS_FOLDER, name)
    newest = 0.0
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if not entry.name.endswith(".ts"):
                    continue
                try:
                    m = entry.stat().st_mtime
                except OSError:
                    continue
                if m > newest:
                    newest = m
    except OSError:
        return False
    if newest <= 0:
        return False
    return (time.time() - newest) <= max_age


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


def gates_status(gate_ips):
    return {ip: host_up(ip) for ip in (gate_ips or [])}


def _gate_ok(gate_ips):
    return all(host_up(ip) for ip in (gate_ips or []))


# ── Credenciais ───────────────────────────────────────────────────────────────
def camera_creds(name, cfg, cam):
    """Retorna (ip, user, password, cloud_password) da camera.

    A conta local vem da config (global ou por camera) e, se faltar, e
    extraida do userinfo da rtsp_url do cameras.json.
    """
    cam = cam or {}
    tapo = cfg.get("tapo") or {}
    ip = cam.get("camera_ip") or ""
    user = cam.get("camera_username") or tapo.get("camera_username") or ""
    password = cam.get("camera_password") or tapo.get("camera_password") or ""
    cloud = tapo.get("password") or ""

    if not user or not password:
        try:
            u = urlparse(_rtsp_url_for(name) or "")
            if not user and u.username:
                user = u.username
            if not password and u.password:
                password = u.password
        except Exception:
            pass
    return ip, user, password, cloud


# ── Camera (pytapo) ───────────────────────────────────────────────────────────
def _reboot_camera_blocking(ip, user, password, cloud_password):
    from pytapo import Tapo

    tapo = Tapo(
        ip,
        user,
        password,
        cloudPassword=cloud_password or "",
        printDebugInformation=False,
        printWarnInformation=False,
    )
    try:
        tapo.reboot()
    finally:
        try:
            tapo.close()
        except Exception:
            pass


def reboot_camera(ip, user, password, cloud_password, timeout=30):
    """Reinicia a camera Tapo. Roda em thread com timeout (pytapo e' blocking)."""
    result = {}

    def run():
        try:
            _reboot_camera_blocking(ip, user, password, cloud_password)
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise RuntimeError("timeout ao reiniciar a camera")
    if "error" in result:
        raise result["error"]


# ── Tomada (python-kasa) ──────────────────────────────────────────────────────
async def _reboot_plug_async(ip, username, password, off_seconds):
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
    import asyncio

    asyncio.run(_reboot_plug_async(ip, username, password, off_seconds))


# ── Estado / loop ─────────────────────────────────────────────────────────────
_last_camera_reboot_ts = 0.0


def _get_status(name):
    with _lock:
        return _status.setdefault(
            name,
            {
                "online": None,
                "recording": None,
                "unhealthy_since": None,
                "stage": None,
                "stage_since": 0,
                "last_reboot": None,
                "rate_limited_until": 0,
                "last_message": "",
            },
        )


def get_status():
    with _lock:
        return {k: dict(v) for k, v in _status.items()}


def _start_plug_reboot(st, plug, tapo, off_seconds, now, name):
    """Reinicia a tomada e atualiza o estado. Retorna True se deu certo."""
    if not (plug and tapo.get("username")):
        return False
    try:
        log.info("Reiniciando tomada de %s (%s)", name, plug)
        reboot_plug(plug, tapo["username"], tapo.get("password", ""), off_seconds)
        st["stage"] = "plug"
        st["stage_since"] = now
        st["last_reboot"] = now
        st["last_message"] = "tomada reiniciada"
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Falha ao reiniciar tomada de %s: %s", name, exc)
        st["last_message"] = f"falha ao reiniciar tomada: {exc}"
        return False


def _tick():
    global _last_camera_reboot_ts

    cfg = load_settings()
    if not cfg.get("enabled"):
        return

    threshold = int(cfg.get("offline_threshold_seconds", 180))
    stall = int(cfg.get("stall_seconds", 300))
    cam_wait = int(cfg.get("camera_reboot_wait_seconds", 180))
    cam_min_interval = int(cfg.get("camera_reboot_min_interval_seconds", 30))
    rate_cooldown = int(cfg.get("rate_limit_cooldown_seconds", 1800))
    cooldown = int(cfg.get("reboot_cooldown_seconds", 900))
    off_seconds = int(cfg.get("power_off_seconds", 15))
    gate_ips = cfg.get("gate_ips") or []
    tapo = cfg.get("tapo") or {}
    now = time.time()

    gates = _gate_ok(gate_ips)

    # ── 1a passada: avalia todas as cameras ──────────────────────────────────
    entries = []
    for cam in cfg.get("cameras", []):
        name = cam.get("name")
        if not name:
            continue
        st = _get_status(name)
        path_state = probe_path(_rtsp_url_for(name))
        rec_ok = recording_recent(name, stall)
        st["online"] = path_state == "online"
        st["recording"] = rec_ok
        entries.append((cam, st, path_state, rec_ok))

    # Se TODAS estao com stream no ar mas nenhuma gravando, o problema e'
    # global (recorder/backend) e reiniciar cameras nao resolve.
    if entries and all(ps == "online" and not rec for (_c, _s, ps, rec) in entries):
        for _cam, st, _ps, _rec in entries:
            st["last_message"] = "gravacao parada em todas; possivel problema global"
        return

    # ── 2a passada: decide e age ─────────────────────────────────────────────
    for cam, st, path_state, rec_ok in entries:
        name = cam.get("name")

        if path_state == "unknown":
            st["last_message"] = "indeterminado (mediamtx/rede?)"
            continue

        if path_state == "online" and rec_ok:
            st["unhealthy_since"] = None
            st["stage"] = None
            st["last_message"] = "ok"
            continue

        reason = "camera fora" if path_state != "online" else "gravacao parada"
        if st["unhealthy_since"] is None:
            st["unhealthy_since"] = now
        outage = now - st["unhealthy_since"]

        if outage < threshold:
            st["last_message"] = f"{reason} ha {int(outage)}s"
            continue
        if not gates:
            st["last_message"] = "offline; APs (gate) fora do ar"
            continue

        cam_ip, cam_user, cam_pw, cloud = camera_creds(name, cfg, cam)
        plug = cam.get("plug_ip")
        reachable = host_up(cam_ip) if cam_ip else False
        can_camera = bool(cam_ip and cam_user and cam_pw and reachable)

        if st.get("rate_limited_until", 0) > now:
            st["last_message"] = (
                f"{reason}; Tapo bloqueado por {int(st['rate_limited_until'] - now)}s"
            )
            continue

        # ── Etapa 1: reboot da camera ─────────────────────────────────────────
        if st["stage"] is None:
            if st["last_reboot"] and (now - st["last_reboot"]) < cooldown:
                st["last_message"] = (
                    f"{reason}; cooldown {int(cooldown - (now - st['last_reboot']))}s"
                )
                continue

            if not can_camera:
                # Camera inacessivel/sem credenciais: reiniciar por ela e'
                # impossivel -> vai direto para a tomada.
                if _start_plug_reboot(st, plug, tapo, off_seconds, now, name):
                    continue
                if cam_ip and not reachable:
                    st["last_message"] = f"{reason}; camera inacessivel e sem tomada"
                else:
                    st["last_message"] = f"{reason}; sem meio de recuperacao"
                st["stage"] = None
                st["last_reboot"] = now
                continue

            # Evita rajada de logins no Tapo (rate-limit): um por vez.
            if (now - _last_camera_reboot_ts) < cam_min_interval:
                st["last_message"] = f"{reason}; aguardando vez p/ reiniciar camera"
                continue

            try:
                log.info("Reiniciando camera %s (%s) [%s]", name, cam_ip, reason)
                reboot_camera(cam_ip, cam_user, cam_pw, cloud)
                _last_camera_reboot_ts = now
                st["stage"] = "camera"
                st["stage_since"] = now
                st["last_reboot"] = now
                st["last_message"] = "camera reiniciada"
            except Exception as exc:  # noqa: BLE001
                _last_camera_reboot_ts = now
                msg = str(exc)
                log.warning("Falha ao reiniciar camera %s: %s", name, msg)
                if "suspension" in msg.lower():
                    st["rate_limited_until"] = now + rate_cooldown
                # Falhou o reboot da camera -> tenta a tomada imediatamente.
                if _start_plug_reboot(st, plug, tapo, off_seconds, now, name):
                    continue
                st["stage"] = None
                st["last_reboot"] = now
                st["last_message"] = f"falha ao reiniciar camera: {msg}"
            continue

        # ── Etapa 2: camera reiniciou mas nao voltou -> tomada ────────────────
        if st["stage"] == "camera":
            if (now - st["stage_since"]) < cam_wait:
                st["last_message"] = f"{reason}; aguardando camera voltar (reboot)"
                continue
            if not _start_plug_reboot(st, plug, tapo, off_seconds, now, name):
                st["last_message"] = f"{reason}; camera reiniciada mas sem tomada"
                st["stage"] = None
                st["last_reboot"] = now
            continue

        # ── Etapa 3: tomada reiniciou; aguarda cooldown p/ novo ciclo ─────────
        if st["stage"] == "plug":
            if (now - st["stage_since"]) < cooldown:
                st["last_message"] = (
                    f"{reason}; tomada reiniciada ha {int(now - st['stage_since'])}s"
                )
                continue
            # Novo ciclo: comeca de novo pela camera.
            st["stage"] = None
            st["last_reboot"] = now
            continue


def _run():
    log.info("Watchdog iniciado")
    while True:
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001
            log.warning("Erro no watchdog: %s", exc)
        cfg = load_settings()
        time.sleep(max(5, int(cfg.get("check_interval_seconds", 30))))


def start_watchdog():
    if not ENABLE_WATCHDOG:
        log.info("Watchdog desativado (ENABLE_WATCHDOG=0)")
        return
    threading.Thread(target=_run, name="watchdog", daemon=True).start()
