"""API da deteccao: configuracao e captura de frame para desenhar a ROI."""

import os

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from config import SNAPSHOTS_FOLDER
from detector import notify
from detector.settings import load_settings, save_settings
from detector.stream import grab_frame_jpeg

router = APIRouter(prefix="/api/detection", tags=["detection"])


@router.get("")
def get_detection():
    return load_settings()


@router.post("")
def update_detection(payload: dict):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Corpo deve ser um objeto JSON.")
    return save_settings(payload)


def _resolve_source(camera: str | None, source: str | None):
    if source:
        return source
    if camera:
        for cam in load_settings().get("cameras", []):
            if cam.get("name") == camera:
                return cam.get("source")
    raise HTTPException(status_code=400, detail="Informe 'camera' (cadastrada) ou 'source' (rtsp).")


@router.get("/snapshot")
def snapshot(camera: str | None = None, source: str | None = None):
    src = _resolve_source(camera, source)
    try:
        jpeg = grab_frame_jpeg(src)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Falha ao capturar frame: {exc}")
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.post("/test")
def test_notification(payload: dict | None = None):
    settings = load_settings()
    notify_cfg = dict(settings.get("notify", {}))
    if payload and isinstance(payload.get("notify"), dict):
        notify_cfg.update(payload["notify"])
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(img, "seccam: notificacao de teste", (30, 170),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
    cv2.putText(img, "se esta imagem chegou, o ntfy esta OK", (30, 210),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(status_code=500, detail="Falha ao gerar imagem de teste.")
    try:
        notify.publish_image(notify_cfg, buf.tobytes(),
                             title="Teste seccam", message="Notificacao de teste do detector")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Falha ao enviar ntfy: {exc}")
    return {"status": "ok"}


@router.get("/events")
def list_events(camera: str | None = None):
    if not os.path.isdir(SNAPSHOTS_FOLDER):
        return {"events": []}
    cams = [camera] if camera else os.listdir(SNAPSHOTS_FOLDER)
    events = []
    for cam in cams:
        folder = os.path.join(SNAPSHOTS_FOLDER, cam)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder), reverse=True):
            if name.lower().endswith(".jpg"):
                events.append({"camera": cam, "name": name})
    return {"events": events[:200]}


@router.get("/events/file")
def event_file(camera: str, name: str):
    folder = os.path.realpath(os.path.join(SNAPSHOTS_FOLDER, camera))
    path = os.path.realpath(os.path.join(folder, name))
    if not path.startswith(folder) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Snapshot nao encontrado.")
    return FileResponse(path, media_type="image/jpeg")
