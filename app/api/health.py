"""API de saude para o Uptime Kuma.

- GET /api/health            -> 200 se as 5 cameras gravam; 503 se alguma alerta
- GET /api/health/<camera>   -> 200/503 para uma camera

503 so quando o watchdog JA tentou recuperar e a camera continua fora/parada
(ver camera_watchdog.health_state). Tentativas em andamento e cooldown de
reboot retornam 200 para nao alarmar reinicios normais.
"""

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from camera_watchdog import health_state, load_settings

router = APIRouter(prefix="/api/health", tags=["health"])


def _resp(payload: dict, ok: bool) -> JSONResponse:
    return JSONResponse(status_code=200 if ok else 503, content=payload)


@router.get("")
def health_all():
    state = health_state(load_settings())
    ok = (not state["alert"]) and state["ok"]
    return _resp(state, ok)


@router.get("/{camera}")
def health_camera(camera: str):
    state = health_state(load_settings())
    cam = state["cameras"].get(camera)
    if cam is None:
        return JSONResponse(status_code=404, content={"error": "camera desconhecida",
                                                      "camera": camera})
    return _resp({"camera": camera, **cam}, not cam["alert"])
