"""Controle PTZ (pan/tilt) das cameras Tapo via pytapo.

Configuracao em ``/configs/ptz.json`` (ou ``PTZ_CAMERAS=camera_3`` no ambiente):

{
  "cameras": [
    {
      "name": "camera_3",
      "label": "Entrada 1",
      "enabled": true,
      "step_degrees": 5,
      "max_step_degrees": 15,
      "invert_x": false,
      "invert_y": false,
      "auth": "auto",
      "transport": "auto"
    }
  ]
}

IP e credenciais vem do ``watchdog.json`` (mesmo ``camera_creds`` usado pelo
watchdog). Nada de senha fica exposto para o navegador.

O pytapo e' bloqueante; cada camera tem um lock e uma sessao reaproveitada.
Comandos e logins sao serializados para nao bater no rate-limit do Tapo.
"""

import json
import logging
import os
import threading
import time

from config import CONFIG_FOLDER
from camera_watchdog import camera_creds, load_settings as load_watchdog_settings

log = logging.getLogger("ptz")

PTZ_JSON = os.path.join(CONFIG_FOLDER, "ptz.json")

SESSION_IDLE_SECONDS = int(os.getenv("PTZ_SESSION_IDLE_SECONDS", "300"))
AUTH_COOLDOWN_SECONDS = int(os.getenv("PTZ_AUTH_COOLDOWN_SECONDS", "1800"))
AUTH_RETRY_SECONDS = int(os.getenv("PTZ_AUTH_RETRY_SECONDS", "60"))
CALL_TIMEOUT_SECONDS = int(os.getenv("PTZ_CALL_TIMEOUT_SECONDS", "15"))
PRESET_CACHE_SECONDS = int(os.getenv("PTZ_PRESET_CACHE_SECONDS", "60"))

DEFAULTS = {
    "label": "",
    "enabled": True,
    "step_degrees": 5,
    "max_step_degrees": 15,
    "invert_x": False,
    "invert_y": False,
    # auto = tenta a Camera Account local e depois admin + senha da nuvem;
    # local = so a conta local; cloud = so admin + senha da conta TP-Link.
    "auth": "auto",
    # auto = deixa o pytapo detectar; pytapo/tpap/klap forcam o transporte.
    "transport": "auto",
}

DIRECTIONS = {
    # Mesmo mapeamento do app/scripts do Tapo: x+ = direita, y+ = cima.
    "up": (0, 1),
    "down": (0, -1),
    "left": (-1, 0),
    "right": (1, 0),
}


class PtzError(Exception):
    """Erro de PTZ com status HTTP sugerido."""

    def __init__(self, detail, status_code=502):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


# ── Config ────────────────────────────────────────────────────────────────────
def _merge_camera(data):
    out = json.loads(json.dumps(DEFAULTS))
    if isinstance(data, dict):
        out.update(data)
    out["enabled"] = bool(out.get("enabled", True))
    try:
        out["step_degrees"] = max(1, int(out.get("step_degrees", 5)))
    except (TypeError, ValueError):
        out["step_degrees"] = 5
    try:
        out["max_step_degrees"] = max(out["step_degrees"], int(out.get("max_step_degrees", 15)))
    except (TypeError, ValueError):
        out["max_step_degrees"] = max(out["step_degrees"], 15)
    return out


def load_settings():
    """Le a configuracao de PTZ.

    Se ``ptz.json`` nao existir, aceita ``PTZ_CAMERAS=camera_3,camera_4`` para
    facilitar testes/deploy sem criar arquivo.
    """
    if os.path.exists(PTZ_JSON):
        try:
            with open(PTZ_JSON, "r") as f:
                raw = json.load(f)
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha ao ler %s: %s", PTZ_JSON, exc)
            raw = {}

        cams = raw.get("cameras", []) if isinstance(raw, dict) else []
        if isinstance(cams, dict):
            cams = [dict({"name": name}, **(cfg or {})) for name, cfg in cams.items()]
        out = []
        for item in cams:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict) or not item.get("name"):
                continue
            merged = _merge_camera(item)
            merged["name"] = str(item["name"])
            out.append(merged)
        return {"cameras": out}

    names = [n.strip() for n in os.getenv("PTZ_CAMERAS", "").split(",") if n.strip()]
    return {
        "cameras": [
            dict(_merge_camera({"name": name}), name=name)
            for name in names
        ]
    }


def get_camera(name):
    for cam in load_settings().get("cameras", []):
        if cam.get("name") == name and cam.get("enabled", True):
            return cam
    raise PtzError(f"camera '{name}' nao esta habilitada para PTZ", 404)


def list_cameras():
    """Lista as cameras PTZ sem abrir sessao (usa apenas presets em cache)."""
    out = []
    for cam in load_settings().get("cameras", []):
        if not cam.get("enabled", True):
            continue
        st = _state(cam["name"])
        out.append(
            {
                "name": cam["name"],
                "label": cam.get("label") or cam["name"],
                "step_degrees": cam["step_degrees"],
                "max_step_degrees": cam["max_step_degrees"],
                "presets": _presets_list(st.get("presets") or {}),
                "rate_limited_until": st.get("rate_limited_until", 0),
            }
        )
    return out


# ── Sessoes pytapo ────────────────────────────────────────────────────────────
_sessions = {}
_sessions_lock = threading.Lock()


def _state(name):
    with _sessions_lock:
        st = _sessions.get(name)
        if st is None:
            st = {
                "tapo": None,
                "lock": threading.Lock(),
                "last_used": 0.0,
                "auth_mode": None,
                "presets": {},
                "presets_at": 0.0,
                "rate_limited_until": 0.0,
                "auth_retry_after": 0.0,
            }
            _sessions[name] = st
        return st


def _run_with_timeout(fn, timeout):
    box = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise TimeoutError(f"timeout de {timeout}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _is_rate_limit(exc):
    msg = str(exc).lower()
    return "suspension" in msg or "too many" in msg or "lockout" in msg


def _is_auth_error(exc):
    msg = str(exc).lower()
    return "invalid authentication" in msg or "authentication data" in msg or "-40401" in msg


def _is_motor_error(exc):
    msg = str(exc).lower()
    return "motor_locked_rotor" in msg or "maximum pan/tilt" in msg or "motor" in msg


def _close_session_locked(st):
    tapo = st.get("tapo")
    st["tapo"] = None
    st["auth_mode"] = None
    st["presets"] = {}
    st["presets_at"] = 0.0
    if tapo is not None:
        try:
            tapo.close()
        except Exception:  # noqa: BLE001
            pass


def _create_session(name, cam):
    """Cria uma sessao pytapo. Tenta a conta local e depois admin+senha da nuvem."""
    from pytapo import Tapo

    wd = load_watchdog_settings()
    wd_cam = next(
        (c for c in wd.get("cameras", []) if c.get("name") == name),
        {},
    )
    ip, user, password, cloud = camera_creds(name, wd, wd_cam)
    ip = cam.get("camera_ip") or ip
    if not ip:
        raise PtzError(f"IP da camera '{name}' nao configurado", 500)

    attempts = []
    auth_mode = (cam.get("auth") or "auto").strip().lower()
    if auth_mode in ("auto", "local") and user and password:
        attempts.append((user, password, "local"))
    if auth_mode in ("auto", "cloud") and cloud:
        # Firmwares mais novos (SPAKE2+) aceitam admin + senha da conta TP-Link.
        attempts.append(("admin", cloud, "cloud"))

    if not attempts:
        raise PtzError(f"sem credenciais para a camera '{name}'", 500)

    last_exc = None
    transport = (cam.get("transport") or "auto").strip().lower()
    transport_kwargs = {}
    if transport in ("pytapo", "tpap", "klap"):
        transport_kwargs["transportMethod"] = transport
    for username, pw, mode in attempts:
        try:
            tapo = Tapo(
                ip,
                username,
                pw,
                cloudPassword=cloud or "",
                printDebugInformation=False,
                printWarnInformation=False,
                **transport_kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _is_rate_limit(exc):
                break
            if _is_auth_error(exc):
                continue
            break

        try:
            presets = tapo.presets or {}
        except Exception as exc:  # noqa: BLE001
            log.info("PTZ: camera %s sem presets (%s)", name, exc)
            presets = {}

        log.info("PTZ: sessao criada para %s via %s", name, mode)
        return tapo, mode, presets

    if last_exc is None:
        raise PtzError(f"falha ao autenticar na camera '{name}'", 502)
    raise last_exc


def _ensure_session_locked(name, cam, st):
    now = time.time()
    if st["rate_limited_until"] > now:
        wait = int(st["rate_limited_until"] - now)
        raise PtzError(f"Tapo em cooldown por rate-limit; tente em {wait}s", 429)

    if st.get("auth_retry_after", 0) > now:
        wait = int(st["auth_retry_after"] - now)
        raise PtzError(f"ultima autenticacao falhou; aguarde {wait}s e tente de novo", 502)

    if st["tapo"] is not None and (now - st["last_used"]) < SESSION_IDLE_SECONDS:
        return

    _close_session_locked(st)
    try:
        tapo, mode, presets = _run_with_timeout(
            lambda: _create_session(name, cam),
            CALL_TIMEOUT_SECONDS + 10,
        )
    except PtzError:
        raise
    except TimeoutError:
        raise PtzError("timeout ao conectar na camera", 504)
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limit(exc):
            st["rate_limited_until"] = time.time() + AUTH_COOLDOWN_SECONDS
            raise PtzError(
                f"Tapo bloqueado por rate-limit; tente em {AUTH_COOLDOWN_SECONDS}s",
                429,
            )
        if _is_auth_error(exc):
            st["auth_retry_after"] = time.time() + AUTH_RETRY_SECONDS
            raise PtzError(
                "falha de autenticacao na camera; confira no app Tapo a Camera Account "
                "(Advanced Settings) e o Third-Party Compatibility (desligue e ligue). "
                "O RTSP pode continuar funcionando mesmo com o controle bloqueado.",
                502,
            )
        raise PtzError(f"falha ao conectar na camera: {exc}", 502)

    st["tapo"] = tapo
    st["auth_mode"] = mode
    st["presets"] = presets or {}
    st["presets_at"] = time.time()
    st["last_used"] = time.time()
    st["auth_retry_after"] = 0.0


def _call(name, fn):
    cam = get_camera(name)
    st = _state(name)
    with st["lock"]:
        _ensure_session_locked(name, cam, st)
        tapo = st["tapo"]
        try:
            result = _run_with_timeout(
                lambda: fn(tapo, st),
                CALL_TIMEOUT_SECONDS,
            )
        except PtzError:
            raise
        except TimeoutError:
            _close_session_locked(st)
            raise PtzError("timeout ao falar com a camera", 504)
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limit(exc):
                st["rate_limited_until"] = time.time() + AUTH_COOLDOWN_SECONDS
                _close_session_locked(st)
                raise PtzError(
                    f"Tapo bloqueado por rate-limit; tente em {AUTH_COOLDOWN_SECONDS}s",
                    429,
                )
            if _is_auth_error(exc):
                st["auth_retry_after"] = time.time() + AUTH_RETRY_SECONDS
                _close_session_locked(st)
                raise PtzError("sessao da camera expirou; tente de novo", 502)
            if _is_motor_error(exc):
                raise PtzError(str(exc), 409)
            raise PtzError(f"falha no comando PTZ: {exc}", 502)

        st["last_used"] = time.time()
        return result


# ── Comandos ──────────────────────────────────────────────────────────────────
def move(name, direction, amount=None):
    cam = get_camera(name)
    direction = (direction or "").strip().lower()
    if direction not in DIRECTIONS:
        raise PtzError("direcao invalida; use up, down, left ou right", 400)

    step = cam["step_degrees"] if amount is None else amount
    try:
        step = int(step)
    except (TypeError, ValueError):
        raise PtzError("amount invalido; use um numero inteiro de graus", 400)
    step = max(1, min(step, cam["max_step_degrees"]))

    dx, dy = DIRECTIONS[direction]
    if cam.get("invert_x"):
        dx = -dx
    if cam.get("invert_y"):
        dy = -dy
    dx *= step
    dy *= step

    def op(tapo, _st):
        return tapo.moveMotor(dx, dy)

    _call(name, op)
    return {
        "camera": name,
        "direction": direction,
        "amount": step,
        "dx": dx,
        "dy": dy,
    }


def _presets_list(presets):
    def sort_key(item):
        key = str(item[0])
        return (0, int(key)) if key.isdigit() else (1, key)

    return [
        {"id": str(k), "name": str(v).strip()}
        for k, v in sorted((presets or {}).items(), key=sort_key)
    ]


def get_presets(name, refresh=False):
    def op(tapo, st):
        now = time.time()
        stale = (now - st["presets_at"]) > PRESET_CACHE_SECONDS
        if refresh or not st["presets"] or stale:
            st["presets"] = tapo.getPresets() or {}
            st["presets_at"] = now
        return st["presets"]

    presets = _call(name, op)
    return _presets_list(presets)


def goto_preset(name, preset_id):
    cam = get_camera(name)
    pid = str(preset_id)

    def op(tapo, st):
        if not st["presets"] or (time.time() - st["presets_at"]) > PRESET_CACHE_SECONDS:
            st["presets"] = tapo.getPresets() or {}
            st["presets_at"] = time.time()
        valid = {str(k) for k in st["presets"]}
        if pid not in valid:
            raise PtzError(f"preset '{pid}' nao existe no app Tapo", 404)
        return tapo.setPreset(pid)

    _call(name, op)
    return {"camera": name, "preset": pid, "name": cam.get("label") or name}
