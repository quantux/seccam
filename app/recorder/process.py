import os
import glob
import re
import subprocess
import datetime
from uploader.uploader import log, get_authenticated_service, initialize_upload
from config import VIDEOS_FOLDER, UPLOAD_TIMES
from uploader.token_manager import refresh_access_token


def parse_timestamp_from_filename(filename):
    """Extrai timestamp do nome do arquivo."""
    base = os.path.basename(filename)
    name, _ = os.path.splitext(base)
    try:
        return datetime.datetime.strptime(name, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def select_segments(camera_path, start_time, end_time):
    """Seleciona vídeos dentro do intervalo de tempo."""
    log(f"Selecionando vídeos entre {start_time} e {end_time} em {camera_path}...")
    all_files = sorted(glob.glob(os.path.join(camera_path, "*.ts")))
    selected_files = [
        f for f in all_files
        if (ts := parse_timestamp_from_filename(f)) and start_time <= ts <= end_time
    ]

    if selected_files:
        log(f"{len(selected_files)} arquivos encontrados.")
        selected_files = selected_files[:-1]
    else:
        log("Nenhum vídeo encontrado no intervalo.")

    return selected_files


def test_video_file(filepath):
    """Verifica integridade de um vídeo usando ffprobe."""
    log(f"🔍 Checando arquivo: {filepath}")
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=format_name,duration",
        "-of", "default=noprint_wrappers=1:nokey=0",
        filepath
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        log("✅ Arquivo válido.")
        return True
    else:
        log(f"⚠️ Arquivo com problema: {filepath}", "warning")
        log(result.stderr.strip(), "warning")
        return False


def concatenate_videos(file_list, camera_name, start_time):
    """Concatena arquivos válidos em um único vídeo."""
    if not file_list:
        log("Nenhum arquivo para concatenar.")
        return None

    valid_files = [f for f in file_list if test_video_file(f)]

    if not valid_files:
        log("Nenhum vídeo válido para concatenar.")
        return None

    title_time_str = start_time.strftime("%Y-%m-%d_%H-%M-%S")
    output_file = os.path.join("/tmp", f"{camera_name}_{title_time_str}.mkv")
    list_file = os.path.join("/tmp", f"concat_list_{camera_name}.txt")

    log(f"Concatenando {len(valid_files)} arquivos válidos em {output_file}...")
    with open(list_file, "w") as f:
        for filepath in valid_files:
            f.write(f"file '{os.path.abspath(filepath)}'\n")

    cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_file]
    result = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(list_file)

    if result.returncode != 0:
        log("❌ Erro ao concatenar vídeos:", "error")
        log(result.stderr, "error")
        return None

    log(f"✅ Concatenação concluída: {output_file}")
    return output_file


def get_previous_completed_cycle(now=None):
    """Retorna o ciclo de 12h anterior completo."""
    if now is None:
        now = datetime.datetime.now()

    day_hour, day_min = UPLOAD_TIMES[0]
    night_hour, night_min = UPLOAD_TIMES[1]

    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - datetime.timedelta(days=1)
    tomorrow = today + datetime.timedelta(days=1)

    day_time_today = today + datetime.timedelta(hours=day_hour, minutes=day_min)
    night_time_today = today + datetime.timedelta(hours=night_hour, minutes=night_min)
    day_time_yesterday = yesterday + datetime.timedelta(hours=day_hour, minutes=day_min)
    night_time_yesterday = yesterday + datetime.timedelta(hours=night_hour, minutes=night_min)

    if day_time_today <= now < night_time_today:
        start, end = night_time_yesterday, day_time_today
    elif now >= night_time_today:
        start, end = day_time_today, night_time_today
    else:
        start, end = night_time_yesterday, day_time_today

    return start, end


def natural_sort_key(name):
    """Ordena diretórios numericamente (camera_1, camera_2...)."""
    match = re.search(r'(\d+)$', name)
    return int(match.group(1)) if match else float('inf')


def upload_last_hours(now=None):
    """Executa rotina completa: concatena e envia os vídeos."""
    log("🚀 Iniciando rotina de upload...")
    refresh_access_token()
    youtube = get_authenticated_service()

    interval_start, interval_end = get_previous_completed_cycle(now)
    log(f"Analisando vídeos entre {interval_start} e {interval_end}")

    for camera_folder in sorted(os.listdir(VIDEOS_FOLDER), key=natural_sort_key):
        camera_path = os.path.join(VIDEOS_FOLDER, camera_folder)
        if not os.path.isdir(camera_path):
            log(f"Ignorando {camera_folder}, não é uma pasta.")
            continue

        log(f"🎥 Processando câmera: {camera_folder}")
        selected_files = select_segments(camera_path, interval_start, interval_end)
        if not selected_files:
            log(f"Nenhum vídeo disponível para {camera_folder}.")
            continue

        output_file = concatenate_videos(selected_files, camera_folder, interval_start)
        if output_file:
            title = os.path.basename(output_file).replace(".mkv", "")
            initialize_upload(youtube, output_file, title)
            os.remove(output_file)
            log(f"🗑️ Arquivo temporário removido: {output_file}")

    log("✅ Rotina de upload concluída.")
