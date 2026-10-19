import os
import glob
import json
import re
import subprocess
import datetime
from uploader.uploader import log, get_authenticated_service, initialize_upload
from config import (
    VIDEOS_FOLDER, UPLOAD_TIMES, MAX_UPLOAD_DURATION, SEGMENT_TIME, CONFIG_FOLDER
)
from uploader.token_manager import refresh_access_token


def parse_timestamp_from_filename(filename):
    """Extrai timestamp do nome do arquivo."""
    base = os.path.basename(filename)
    name, _ = os.path.splitext(base)
    try:
        return datetime.datetime.strptime(name, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def format_duration(seconds):
    """Formata segundos como HH:MM:SS para os logs."""
    return str(datetime.timedelta(seconds=int(seconds)))


def remove_if_exists(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


UPLOAD_STATE_FILE = os.path.join(CONFIG_FOLDER, "upload_state.json")
MAX_BACKLOG = datetime.timedelta(hours=24)


def load_upload_state():
    """Retorna o estado salvo ou None se o arquivo ainda não existir."""
    try:
        with open(UPLOAD_STATE_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as e:
        log(f"⚠️ Não foi possível ler {UPLOAD_STATE_FILE}: {e}", "warning")
        return {}


def save_upload_state(state):
    try:
        os.makedirs(CONFIG_FOLDER, exist_ok=True)
        tmp_file = f"{UPLOAD_STATE_FILE}.tmp"
        with open(tmp_file, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, UPLOAD_STATE_FILE)
    except OSError as e:
        log(f"⚠️ Não foi possível salvar {UPLOAD_STATE_FILE}: {e}", "warning")


def parse_state_time(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def select_segments(camera_path, start_time, end_time, carry_start=None):
    """Seleciona vídeos dentro do intervalo [carry_start ou start_time, end_time)."""
    selection_start = start_time
    if carry_start and carry_start < start_time:
        selection_start = carry_start

    log(f"Selecionando vídeos entre {selection_start} e {end_time} em {camera_path}...")
    if selection_start < start_time:
        log(
            f"↩️ Incluindo {format_duration((start_time - selection_start).total_seconds())} "
            "de sobreposição para cobrir o que não foi enviado no ciclo anterior."
        )

    all_files = sorted(glob.glob(os.path.join(camera_path, "*.ts")))
    selected_files = [
        f for f in all_files
        if (ts := parse_timestamp_from_filename(f)) and selection_start <= ts < end_time
    ]

    if selected_files:
        log(f"{len(selected_files)} arquivos encontrados.")
    else:
        log("Nenhum vídeo encontrado no intervalo.")

    return selected_files


def probe_video_file(filepath):
    """Verifica integridade e retorna a duração do vídeo em segundos."""
    log(f"🔍 Checando arquivo: {filepath}")
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=format_name,duration",
        "-of", "default=noprint_wrappers=1:nokey=0",
        filepath
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"⚠️ Arquivo com problema: {filepath}", "warning")
        log(result.stderr.strip(), "warning")
        return None

    duration = None
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "duration":
            try:
                duration = float(value.strip())
            except ValueError:
                duration = None
            break

    if duration is None or duration <= 0:
        log(f"⚠️ Arquivo sem duração válida: {filepath}", "warning")
        return None

    log("✅ Arquivo válido.")
    return duration


def get_file_duration(filepath):
    """Retorna a duração do arquivo final em segundos."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        filepath
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"⚠️ Não foi possível medir a duração de {filepath}: {result.stderr.strip()}", "warning")
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        log(f"⚠️ Duração inválida para {filepath}: {result.stdout.strip()}", "warning")
        return None


def concatenate_videos(file_list, camera_name, start_time):
    """Concatena arquivos válidos e retorna (arquivo, primeiro segmento descartado)."""
    if not file_list:
        log("Nenhum arquivo para concatenar.")
        return None, None

    measured_files = []
    total_duration = 0.0
    first_omitted = None
    for filepath in file_list:
        duration = probe_video_file(filepath)
        if duration is not None:
            measured_files.append((filepath, duration))
            total_duration += duration

    if not measured_files:
        log("Nenhum vídeo válido para concatenar.")
        return None, None

    log(
        f"Duração total dos {len(measured_files)} segmentos válidos: "
        f"{format_duration(total_duration)}"
    )

    # Só descarta o último segmento se a soma realmente passar do limite.
    while measured_files and total_duration > MAX_UPLOAD_DURATION:
        removed_path, removed_duration = measured_files.pop()
        total_duration -= removed_duration
        first_omitted = removed_path  # o último removido é o mais antigo
        log(
            f"⚠️ Duração acima de {format_duration(MAX_UPLOAD_DURATION)}; "
            f"descartando último segmento: {removed_path}",
            "warning",
        )

    if not measured_files:
        log("Nenhum segmento restou após aplicar o limite de duração.", "error")
        return None, None

    title_time_str = start_time.strftime("%Y-%m-%d_%H-%M-%S")
    output_file = os.path.join("/tmp", f"{camera_name}_{title_time_str}.mkv")
    list_file = os.path.join("/tmp", f"concat_list_{camera_name}.txt")

    while measured_files:
        log(f"Concatenando {len(measured_files)} arquivos válidos em {output_file}...")
        with open(list_file, "w") as f:
            for filepath, _ in measured_files:
                f.write(f"file '{os.path.abspath(filepath)}'\n")

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file, "-c", "copy", output_file
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            log("❌ Erro ao concatenar vídeos:", "error")
            log(result.stderr, "error")
            remove_if_exists(list_file)
            remove_if_exists(output_file)
            return None, None

        # Verificação antes de enviar: se o arquivo final passou do limite,
        # descarta e concatena novamente sem o último segmento.
        output_duration = get_file_duration(output_file)
        if output_duration is None:
            log("❌ Não foi possível verificar a duração do arquivo concatenado. Upload cancelado.", "error")
            remove_if_exists(list_file)
            remove_if_exists(output_file)
            return None, None

        if output_duration <= MAX_UPLOAD_DURATION:
            remove_if_exists(list_file)
            log(
                f"✅ Concatenação concluída: {output_file} "
                f"(duração: {format_duration(output_duration)})"
            )
            return output_file, first_omitted

        log(
            f"⚠️ Arquivo concatenado com {format_duration(output_duration)}, "
            f"acima de {format_duration(MAX_UPLOAD_DURATION)}. "
            "Descartando e refazendo sem o último segmento.",
            "warning",
        )
        remove_if_exists(output_file)
        removed_path, removed_duration = measured_files.pop()
        total_duration -= removed_duration
        first_omitted = removed_path  # o último removido é o mais antigo
        log(f"🗑️ Segmento descartado: {removed_path}", "warning")

    remove_if_exists(list_file)
    log("❌ Não foi possível gerar um vídeo dentro do limite de duração.", "error")
    return None, None


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

    state = load_upload_state()
    first_run = state is None
    if state is None:
        state = {}

    for camera_folder in sorted(os.listdir(VIDEOS_FOLDER), key=natural_sort_key):
        camera_path = os.path.join(VIDEOS_FOLDER, camera_folder)
        if not os.path.isdir(camera_path):
            log(f"Ignorando {camera_folder}, não é uma pasta.")
            continue

        log(f"🎥 Processando câmera: {camera_folder}")

        carry_start = parse_state_time(state.get(camera_folder))
        if carry_start is None and first_run:
            # Primeira execução com estado próprio: inclui o segmento anterior
            # ao início do ciclo para fechar a lacuna deixada pela versão antiga.
            carry_start = interval_start - datetime.timedelta(seconds=SEGMENT_TIME)

        if carry_start and carry_start < interval_start - MAX_BACKLOG:
            log(
                f"⚠️ Estado de {camera_folder} é antigo ({carry_start}); "
                f"backlog maior que {format_duration(MAX_BACKLOG.total_seconds())}. "
                "Recomeçando do início do ciclo atual.",
                "warning",
            )
            carry_start = interval_start - datetime.timedelta(seconds=SEGMENT_TIME)

        selected_files = select_segments(
            camera_path, interval_start, interval_end, carry_start
        )
        if not selected_files:
            log(f"Nenhum vídeo disponível para {camera_folder}.")
            continue

        video_start = (
            carry_start
            if carry_start and carry_start < interval_start
            else interval_start
        )
        output_file, first_omitted = concatenate_videos(
            selected_files, camera_folder, video_start
        )
        if not output_file:
            continue

        title = os.path.basename(output_file).replace(".mkv", "")
        try:
            upload_response = initialize_upload(youtube, output_file, title)
        except Exception as e:
            log(f"❌ Erro inesperado no upload de {camera_folder}: {e}", "error")
            upload_response = None

        if upload_response and upload_response.get("id"):
            if first_omitted:
                omitted_start = parse_timestamp_from_filename(first_omitted)
                if omitted_start:
                    state[camera_folder] = omitted_start.isoformat()
                    log(
                        f"↩️ Próximo upload de {camera_folder} começará em "
                        f"{omitted_start}."
                    )
                else:
                    state.pop(camera_folder, None)
                    log(
                        f"⚠️ Não foi possível interpretar {first_omitted}; estado limpo.",
                        "warning",
                    )
            else:
                state.pop(camera_folder, None)
            save_upload_state(state)
        else:
            # O upload não foi confirmado: mantém o ponto de partida para que
            # o próximo ciclo tente cobrir novamente este intervalo.
            if carry_start:
                state[camera_folder] = carry_start.isoformat()
                save_upload_state(state)
            log(
                f"⚠️ Upload de {camera_folder} não confirmado; "
                "estado mantido para nova tentativa.",
                "warning",
            )

        os.remove(output_file)
        log(f"🗑️ Arquivo temporário removido: {output_file}")

    log("✅ Rotina de upload concluída.")
