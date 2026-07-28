import os
import time
import datetime
from config import (
    VIDEOS_FOLDER,
    DAYS_TO_KEEP,
    CONFIG_FOLDER,
    LOGS_FOLDER,
    MAX_LOG_SIZE_MB,
    LOG_DAYS_TO_KEEP,
    CHECK_INTERVAL_HOURS,
)

def clean_old_videos():
    """Remove vídeos antigos e limpa logs periodicamente"""
    while True:
        if not os.path.exists(VIDEOS_FOLDER):
            time.sleep(24 * 60 * 60)
            continue

        now = datetime.datetime.now()
        cutoff_time = now - datetime.timedelta(days=DAYS_TO_KEEP)

        for camera_folder in os.listdir(VIDEOS_FOLDER):
            full_camera_path = os.path.join(VIDEOS_FOLDER, camera_folder)
            if not os.path.isdir(full_camera_path):
                continue

            for f in os.listdir(full_camera_path):
                if not f.lower().endswith("ts"):
                    continue

                file_path = os.path.join(full_camera_path, f)
                base = os.path.splitext(f)[0]

                try:
                    file_time = datetime.datetime.strptime(base, "%Y-%m-%d_%H-%M-%S")
                except ValueError:
                    continue

                if file_time < cutoff_time:
                    try:
                        os.remove(file_path)
                        print(f"[cleaner] Removendo vídeo antigo: {file_path}")
                    except Exception as e:
                        print(f"[cleaner] Erro ao remover arquivo {file_path}: {e}")

        # Também roda a limpeza de logs
        clean_logs_once()

        # Espera o intervalo configurado
        time.sleep(CHECK_INTERVAL_HOURS * 3600)

def clean_logs_once():
    """Executa uma verificação única nos logs: truncar e apagar antigos."""
    folders_to_check = [LOGS_FOLDER, os.path.join(LOGS_FOLDER, "uploader")]

    now = datetime.datetime.now()
    cutoff_time = now - datetime.timedelta(days=LOG_DAYS_TO_KEEP)

    for folder in folders_to_check:
        if not os.path.exists(folder):
            continue

        for f in os.listdir(folder):
            path = os.path.join(folder, f)
            if not os.path.isfile(path):
                continue

            # Apagar logs antigos
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
            if mtime < cutoff_time:
                try:
                    os.remove(path)
                    print(f"[cleaner] Log antigo removido: {path}")
                    continue
                except Exception as e:
                    print(f"[cleaner] Erro ao remover log {path}: {e}")

            # Truncar logs grandes
            trim_log_file(path, MAX_LOG_SIZE_MB)

def trim_log_file(file_path, max_size_mb):
    """Trunca o log se ele for maior que o limite."""
    max_bytes = max_size_mb * 1024 * 1024
    size = os.path.getsize(file_path)
    if size <= max_bytes:
        return

    try:
        with open(file_path, "rb") as f:
            f.seek(-min(size, max_bytes // 2), os.SEEK_END)
            data = f.read()

        with open(file_path, "wb") as f:
            f.write(b"[Log truncado automaticamente - arquivo excedeu o limite]\n\n")
            f.write(data)

        print(f"[cleaner] Log truncado: {file_path} (reduzido para ~{max_size_mb//2} MB)")
    except Exception as e:
        print(f"[cleaner] Erro ao truncar {file_path}: {e}")
