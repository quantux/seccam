import os
import datetime
import math
import re
import subprocess
from config import VIDEOS_FOLDER, SEGMENT_TIME

# Tamanho mínimo seguro em bytes para descartar apenas arquivos vazios / corrompidos (< 1KB)
MIN_SEGMENT_SIZE_BYTES = 1024

# Cache em memória para durações de segmentos probed: filepath -> (mtime, size, duration)
_duration_cache = {}

def get_segment_duration(file_path: str) -> float:
    """
    Retorna a duração real em segundos do segmento .ts usando ffprobe com cache em memória.
    """
    try:
        stat = os.stat(file_path)
        mtime = stat.st_mtime
        size = stat.st_size
    except OSError:
        return float(SEGMENT_TIME)

    cached = _duration_cache.get(file_path)
    if cached and cached[0] == mtime and cached[1] == size:
        return cached[2]

    duration = float(SEGMENT_TIME)
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            parsed_dur = float(res.stdout.strip())
            if parsed_dur > 0:
                duration = parsed_dur
    except Exception:
        pass

    _duration_cache[file_path] = (mtime, size, duration)
    return duration

def get_available_dates(camera_name: str):
    """
    Retorna a lista de datas (no formato YYYY-MM-DD) disponíveis para uma câmera.
    """
    camera_dir = os.path.join(VIDEOS_FOLDER, camera_name)
    if not os.path.exists(camera_dir):
        return []

    dates = set()
    try:
        with os.scandir(camera_dir) as entries:
            for entry in entries:
                if entry.is_file() and entry.name.lower().endswith(".ts"):
                    filename = entry.name
                    parts = filename.split("_")
                    if len(parts) >= 2:
                        date_part = parts[0]
                        if re.match(r"^\d{4}-\d{2}-\d{2}$", date_part):
                            dates.add(date_part)
    except OSError:
        return []

    return sorted(list(dates), reverse=True)

def generate_m3u8_playlist(camera_name: str, date_str: str):
    """
    Gera o conteúdo de uma playlist HLS (.m3u8) dinamicamente em memória
    unindo todos os arquivos .ts do dia selecionado.
    """
    camera_dir = os.path.join(VIDEOS_FOLDER, camera_name)
    if not os.path.exists(camera_dir):
        return None

    # Lista arquivos .ts pertencentes ao dia selecionado
    prefix = f"{date_str}_"
    raw_segments = []
    
    try:
        with os.scandir(camera_dir) as entries:
            for entry in entries:
                if entry.is_file() and entry.name.startswith(prefix) and entry.name.endswith(".ts"):
                    filename = entry.name
                    base_name = filename[:-3]
                    try:
                        dt = datetime.datetime.strptime(base_name, "%Y-%m-%d_%H-%M-%S")
                        size = entry.stat().st_size
                        if size >= MIN_SEGMENT_SIZE_BYTES:
                            raw_segments.append({
                                "filename": filename,
                                "datetime": dt,
                                "size": size,
                                "path": os.path.join(camera_dir, filename)
                            })
                    except (ValueError, OSError):
                        continue
    except OSError:
        return None

    if not raw_segments:
        return None

    # Ordena segmentos cronologicamente.
    raw_segments.sort(key=lambda x: x["datetime"])

    now = datetime.datetime.now()
    if date_str == now.strftime("%Y-%m-%d") and len(raw_segments) > 1:
        # Se for o dia de hoje e houver mais de 1 segmento, desconsidera o último
        # arquivo pois o FFmpeg ainda pode estar gravando nele.
        raw_segments.pop()

    if not raw_segments:
        return None

    # Calcula duração de cada segmento (otimizado: usa intervalo cronológico para segmentos contínuos,
    # invocando ffprobe apenas em intervalos irregulares ou no último segmento do dia).
    segments = []
    total_count = len(raw_segments)
    for i, seg in enumerate(raw_segments):
        duration = None
        if i < total_count - 1:
            next_dt = raw_segments[i + 1]["datetime"]
            gap = (next_dt - seg["datetime"]).total_seconds()
            if 50.0 <= gap <= 70.0:
                duration = gap

        if duration is None:
            duration = get_segment_duration(seg["path"])

        segments.append({
            "filename": seg["filename"],
            "datetime": seg["datetime"],
            "duration": duration,
            "size": seg["size"]
        })

    max_duration = float(SEGMENT_TIME)
    playlist_items = []

    for i, seg in enumerate(segments):
        is_discontinuity = False
        if i > 0:
            prev_seg = segments[i - 1]
            expected_start = prev_seg["datetime"] + datetime.timedelta(seconds=prev_seg["duration"])
            gap = (seg["datetime"] - expected_start).total_seconds()
            if abs(gap) > 3.0:
                is_discontinuity = True

        if seg["duration"] > max_duration:
            max_duration = seg["duration"]

        playlist_items.append({
            "discontinuity": is_discontinuity,
            "duration": seg["duration"],
            "datetime": seg["datetime"],
            "filename": seg["filename"]
        })

    target_duration = int(math.ceil(max_duration)) + 2

    # Constrói o texto M3U8 HLS VOD
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        ""
    ]

    for item in playlist_items:
        if item["discontinuity"]:
            lines.append("#EXT-X-DISCONTINUITY")

        lines.append(f"#EXTINF:{item['duration']:.3f},")
        lines.append(f"/api/segment?camera={camera_name}&file={item['filename']}")

    lines.append("#EXT-X-ENDLIST")
    lines.append("")

    return "\n".join(lines)



