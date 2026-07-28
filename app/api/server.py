import os
import json
from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from config import CONFIG_FOLDER, CAMERAS_JSON, VIDEOS_FOLDER
from hls.playlist import get_available_dates, generate_m3u8_playlist
from hls.segment import get_segment_file_response
from hls.exporter import export_video_clip

app = FastAPI(title="Security Camera HLS Playback API")

# Permite acesso CORS total para o player no navegador
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CameraSchema(BaseModel):
    name: str
    rtsp_url: str

@app.get("/api/cameras")
def list_cameras():
    """
    Retorna a lista de câmeras configuradas.
    """
    if os.path.exists(CAMERAS_JSON):
        try:
            with open(CAMERAS_JSON, "r") as f:
                data = json.load(f)
                return data.get("cameras", [])
        except Exception:
            pass

    # Fallback: listar diretórios dentro de VIDEOS_FOLDER
    if os.path.exists(VIDEOS_FOLDER):
        dirs = [d for d in os.listdir(VIDEOS_FOLDER) if os.path.isdir(os.path.join(VIDEOS_FOLDER, d))]
        return [{"name": d, "rtsp_url": ""} for d in sorted(dirs)]

    return []

@app.post("/api/cameras")
def save_camera(cam: CameraSchema):
    """
    Adiciona ou atualiza uma câmera no arquivo cameras.json para iniciar a gravação.
    """
    os.makedirs(CONFIG_FOLDER, exist_ok=True)
    cameras = []
    if os.path.exists(CAMERAS_JSON):
        try:
            with open(CAMERAS_JSON, "r") as f:
                cameras = json.load(f).get("cameras", [])
        except Exception:
            cameras = []

    # Atualiza URL se a câmera já existir ou insere nova
    updated = False
    for c in cameras:
        if c["name"] == cam.name:
            c["rtsp_url"] = cam.rtsp_url
            updated = True
            break
    if not updated:
        cameras.append({"name": cam.name, "rtsp_url": cam.rtsp_url})

    with open(CAMERAS_JSON, "w") as f:
        json.dump({"cameras": cameras}, f, indent=2)

    return {"status": "success", "message": f"Câmera '{cam.name}' salva com sucesso!", "cameras": cameras}

@app.get("/api/days")
def list_days(camera: str):
    """
    Retorna a lista de dias disponíveis para gravação de uma determinada câmera.
    """
    if not camera:
        raise HTTPException(status_code=400, detail="Parâmetro 'camera' é obrigatório.")
    
    dates = get_available_dates(camera)
    return {"camera": camera, "dates": dates}

@app.get("/api/playlist.m3u8")
def get_playlist(camera: str, date: str):
    """
    Gera dinamicamente e retorna a playlist M3U8 para a câmera e data especificadas.
    """
    if not camera or not date:
        raise HTTPException(status_code=400, detail="Parâmetros 'camera' e 'date' são obrigatórios.")

    m3u8_content = generate_m3u8_playlist(camera, date)
    if not m3u8_content:
        raise HTTPException(status_code=404, detail="Nenhuma gravação encontrada para esta data.")

    return Response(
        content=m3u8_content,
        media_type="application/x-mpegURL",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Access-Control-Allow-Origin": "*"
        }
    )

@app.get("/api/segment")
def get_segment(camera: str, file: str):
    """
    Serve um segmento individual de vídeo (.ts).
    """
    if not camera or not file:
        raise HTTPException(status_code=400, detail="Parâmetros 'camera' e 'file' são obrigatórios.")

    return get_segment_file_response(camera, file)

@app.get("/api/export_clip")
def export_clip(camera: str, date: str, start_time: str, end_time: str):
    """
    Exporta e baixa um trecho de vídeo MP4 concatenando os blocos .ts do intervalo informado.
    """
    if not camera or not date or not start_time or not end_time:
        raise HTTPException(status_code=400, detail="Parâmetros 'camera', 'date', 'start_time' e 'end_time' são obrigatórios.")

    return export_video_clip(camera, date, start_time, end_time)

# Servir arquivos estáticos do frontend (HTML, CSS, JS)
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
