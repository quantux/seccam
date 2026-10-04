"""API do watchdog das cameras: configuracao, status e reboot manual."""

import json

from fastapi import APIRouter, HTTPException

from camera_watchdog import (
    gates_status,
    get_status,
    load_settings,
    reboot_plug,
    save_settings,
)

router = APIRouter(prefix="/api/watchdog", tags=["watchdog"])

MASK = "********"


@router.get("")
def get_watchdog():
    cfg = load_settings()
    safe = json.loads(json.dumps(cfg))
    if safe.get("tapo", {}).get("password"):
        safe["tapo"]["password"] = MASK
    return {
        "config": safe,
        "status": get_status(),
        "gates": gates_status(cfg.get("gate_ips") or []),
    }


@router.post("")
def update_watchdog(payload: dict):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Corpo deve ser um objeto JSON.")
    existing = load_settings()
    tapo = payload.get("tapo")
    if isinstance(tapo, dict) and tapo.get("password") == MASK:
        tapo["password"] = (existing.get("tapo") or {}).get("password", "")
    return save_settings(payload)


@router.post("/reboot/{name}")
def manual_reboot(name: str):
    cfg = load_settings()
    cam = next((c for c in cfg.get("cameras", []) if c.get("name") == name), None)
    if not cam or not cam.get("plug_ip"):
        raise HTTPException(status_code=404, detail="Camera/tomada nao configurada.")
    tapo = cfg.get("tapo") or {}
    if not tapo.get("username"):
        raise HTTPException(status_code=400, detail="Credenciais Tapo nao configuradas.")
    try:
        reboot_plug(
            cam["plug_ip"],
            tapo["username"],
            tapo.get("password", ""),
            int(cfg.get("power_off_seconds", 15)),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Falha ao reiniciar tomada: {exc}")
    return {"status": "ok", "camera": name, "plug_ip": cam["plug_ip"]}
