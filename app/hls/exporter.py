import os
import glob
import tempfile
import subprocess
import datetime
from fastapi import HTTPException
from fastapi.responses import FileResponse
from config import VIDEOS_FOLDER

def export_video_clip(camera_name: str, date_str: str, start_time_str: str, end_time_str: str):
    """
    Concatena os segmentos .ts do intervalo [start_time_str, end_time_str]
    e gera um arquivo .mp4 leve para download direto no navegador.
    """
    camera_dir = os.path.join(VIDEOS_FOLDER, camera_name)
    if not os.path.exists(camera_dir):
        raise HTTPException(status_code=404, detail="Diretório da câmera não encontrado.")

    # Normaliza horas (permite HH:MM:SS ou HH-MM-SS)
    s_time = start_time_str.replace(":", "-")
    e_time = end_time_str.replace(":", "-")

    try:
        dt_start = datetime.datetime.strptime(f"{date_str}_{s_time}", "%Y-%m-%d_%H-%M-%S")
        dt_end = datetime.datetime.strptime(f"{date_str}_{e_time}", "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de hora inválido. Use HH:MM:SS (ex: 14:30:00).")

    if dt_end <= dt_start:
        raise HTTPException(status_code=400, detail="A hora final deve ser maior que a hora inicial.")

    all_files = sorted(glob.glob(os.path.join(camera_dir, f"{date_str}_*.ts")))
    selected_files = []

    for f in all_files:
        base = os.path.splitext(os.path.basename(f))[0]
        try:
            file_dt = datetime.datetime.strptime(base, "%Y-%m-%d_%H-%M-%S")
            # Inclui arquivos que começam ou cobrem o intervalo
            if (file_dt + datetime.timedelta(seconds=60)) >= dt_start and file_dt <= dt_end:
                selected_files.append(f)
        except ValueError:
            continue

    if not selected_files:
        raise HTTPException(status_code=404, detail="Nenhuma gravação encontrada no intervalo selecionado.")

    # Concatena via FFmpeg sem re-encode para MP4
    temp_dir = tempfile.gettempdir()
    output_filename = f"clip_{camera_name}_{date_str}_{s_time}_a_{e_time}.mp4"
    output_path = os.path.join(temp_dir, output_filename)
    list_path = os.path.join(temp_dir, f"list_{output_filename}.txt")

    with open(list_path, "w") as f:
        for filepath in selected_files:
            f.write(f"file '{os.path.abspath(filepath)}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if os.path.exists(list_path):
        os.remove(list_path)

    if result.returncode != 0 or not os.path.exists(output_path):
        raise HTTPException(status_code=500, detail="Erro de processamento FFmpeg ao gerar MP4.")

    return FileResponse(
        path=output_path,
        media_type="video/mp4",
        filename=output_filename,
        headers={"Content-Disposition": f'attachment; filename="{output_filename}"'}
    )
