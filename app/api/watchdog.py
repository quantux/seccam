"""API do watchdog das cameras: configuracao, status e reboot manual."""

import json

from fastapi import APIRouter, HTTPException

from camera_watchdog import (
    camera_creds,
    gates_status,
    get_status,
    load_settings,
    reboot_camera,
    reboot_plug,
    save_settings,
)

router = APIRouter(prefix="/api/watchdog", tags=["watchdog"])

MASK = "********"


def _mask(cfg):
    safe = json.loads(json.dumps(cfg))
    tapo = safe.get("tapo") or {}
    for key in ("password", "camera_password"):
        if tapo.get(key):
            tapo[key] = MASK
    return safe


@router.get("")
def get_watchdog():
    cfg = load_settings()
    return {
        "config": _mask(cfg),
        "status": get_status(),
        "gates": gates_status(cfg.get("gate_ips") or []),
    }


@router.post("")
def update_watchdog(payload: dict):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Corpo deve ser um objeto JSON.")
    existing = load_settings()
    tapo = payload.get("tapo")
    if isinstance(tapo, dict):
        current = existing.get("tapo") or {}
        for key in ("password", "camera_password"):
            if tapo.get(key) == MASK:
                tapo[key] = current.get(key, "")
    return save_settings(payload)


@router.post("/reboot/{name}")
def manual_reboot(name: str):
    cfg = load_settings()
    cam = next((c for c in cfg.get("cameras", []) if c.get("name") == name), None)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera nao configurada.")
    tapo = cfg.get("tapo") or {}

    # 1) Tenta reiniciar a propria camera.
    cam_ip, cam_user, cam_pw, cloud = camera_creds(name, cfg, cam)
    if cam_ip and cam_user and cam_pw:
        try:
            reboot_camera(cam_ip, cam_user, cam_pw, cloud)
            return {"status": "ok", "camera": name, "target": "camera", "camera_ip": cam_ip}
        except Exception as exc:  # noqa: BLE001
            camera_error = str(exc)
    else:
        camera_error = "credenciais/IP da camera nao configurados"

    # 2) Fallback: reinicia a tomada inteligente.
    plug = cam.get("plug_ip")
    if plug and tapo.get("username"):
        try:
            reboot_plug(
                plug,
                tapo["username"],
                tapo.get("password", ""),
                int(cfg.get("power_off_seconds", 15)),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Falha na camera ({camera_error}) e na tomada ({exc}).",
            )
        return {
            "status": "ok",
            "camera": name,
            "target": "plug",
            "plug_ip": plug,
            "camera_error": camera_error,
        }

    raise HTTPException(status_code=502, detail=f"Falha ao reiniciar camera: {camera_error}")
