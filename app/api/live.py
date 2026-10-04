"""API do multicam viewer: descobre as streams no mediamtx (HLS e WebRTC).

O endereco de cada stream e derivado da `rtsp_url` configurada no
`cameras.json` (mesmo host e mesmo path), trocando a porta RTSP pelas portas
HTTP do HLS (padrao 8888) e do WebRTC/WHEP (padrao 8889) do mediamtx. Assim a
grade de cameras le direto do mediamtx, sem passar pelo backend.
"""

import json
import os
from urllib.parse import urlparse

from fastapi import APIRouter

from config import (
    CAMERAS_JSON,
    MEDIAMTX_HLS_PORT,
    MEDIAMTX_HLS_SCHEME,
    MEDIAMTX_HOST,
    MEDIAMTX_WEBRTC_PORT,
    MEDIAMTX_WEBRTC_SCHEME,
)

router = APIRouter(prefix="/api/live", tags=["live"])


def load_cameras():
    """Le a lista de cameras do cameras.json (mesmo formato do recorder)."""
    if not os.path.exists(CAMERAS_JSON):
        return []
    try:
        with open(CAMERAS_JSON, "r") as f:
            return json.load(f).get("cameras", [])
    except Exception:
        return []


def _parse_rtsp(rtsp_url):
    """Extrai (host, path) da rtsp_url. Retorna (None, None) se invalida."""
    if not rtsp_url:
        return None, None
    try:
        parsed = urlparse(rtsp_url)
    except Exception:
        return None, None
    host = MEDIAMTX_HOST or parsed.hostname
    path = (parsed.path or "").strip("/")
    return host, path


def _hls_url(host, path):
    if not host or not path:
        return None
    return f"{MEDIAMTX_HLS_SCHEME}://{host}:{MEDIAMTX_HLS_PORT}/{path}/index.m3u8"


def _webrtc_url(host, path):
    if not host or not path:
        return None
    return f"{MEDIAMTX_WEBRTC_SCHEME}://{host}:{MEDIAMTX_WEBRTC_PORT}/{path}/whep"


@router.get("")
def list_live():
    """Lista as cameras com as URLs HLS e WebRTC (WHEP) do mediamtx."""
    cameras = []
    for cam in load_cameras():
        name = cam.get("name")
        host, path = _parse_rtsp(cam.get("rtsp_url"))
        cameras.append(
            {
                "name": name,
                "path": path,
                "hls_url": _hls_url(host, path),
                "webrtc_url": _webrtc_url(host, path),
            }
        )
    return {"cameras": cameras}
