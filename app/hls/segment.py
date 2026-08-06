import os
from fastapi import HTTPException
from fastapi.responses import FileResponse
from config import VIDEOS_FOLDER

def get_segment_file_response(camera_name: str, filename: str):
    """
    Retorna o arquivo .ts para o player HLS.
    """
    # Evita Directory Traversal
    safe_filename = os.path.basename(filename)
    if safe_filename != filename or not filename.endswith(".ts"):
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido.")

    file_path = os.path.join(VIDEOS_FOLDER, camera_name, safe_filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Segmento de vídeo não encontrado.")

    return FileResponse(
        path=file_path,
        media_type="video/MP2T",
        filename=safe_filename,
        headers={
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=31536000, immutable"
        }
    )
