"""API de controle PTZ (pan/tilt) das cameras Tapo."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import ptz

router = APIRouter(prefix="/api/ptz", tags=["ptz"])


class MoveBody(BaseModel):
    direction: str
    amount: int | None = None


class PresetBody(BaseModel):
    id: str


def _raise(exc):
    if isinstance(exc, ptz.PtzError):
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    raise HTTPException(status_code=502, detail=str(exc))


@router.get("")
def list_ptz():
    return {"cameras": ptz.list_cameras()}


@router.get("/{name}/presets")
def get_presets(name: str, refresh: bool = False):
    try:
        return {"camera": name, "presets": ptz.get_presets(name, refresh=refresh)}
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("/{name}/move")
def move(name: str, body: MoveBody):
    try:
        return ptz.move(name, body.direction, body.amount)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("/{name}/preset")
def goto_preset(name: str, body: PresetBody):
    try:
        return ptz.goto_preset(name, body.id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
