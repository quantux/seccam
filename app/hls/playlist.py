import os
import datetime
import re
from config import VIDEOS_FOLDER, SEGMENT_TIME

# Tamanho mínimo seguro em bytes para descartar apenas arquivos vazios / corrompidos (< 1KB)
MIN_SEGMENT_SIZE_BYTES = 1024

def get_available_dates(camera_name: str):
    """
    Retorna a lista de datas (no formato YYYY-MM-DD) disponíveis para uma câmera.
    """
    camera_dir = os.path.join(VIDEOS_FOLDER, camera_name)
    if not os.path.exists(camera_dir):
        return []

    dates = set()
    for filename in os.listdir(camera_dir):
        if filename.endswith(".ts"):
            # Exemplo: 2026-07-28_14-30-00.ts -> 2026-07-28
            parts = filename.split("_")
            if len(parts) >= 2:
                date_part = parts[0]
                # Valida formato YYYY-MM-DD
                if re.match(r"^\d{4}-\d{2}-\d{2}$", date_part):
                    dates.add(date_part)

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
    segments = []
    
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
                            segments.append({"filename": filename, "datetime": dt, "size": size})
                    except (ValueError, OSError):
                        continue
    except OSError:
        return None

    if not segments:
        return None

    # Ordena segmentos cronologicamente.
    segments.sort(key=lambda x: x["datetime"])

    now = datetime.datetime.now()
    if date_str == now.strftime("%Y-%m-%d") and len(segments) > 1:
        # Se for o dia de hoje e houver mais de 1 segmento, desconsidera o último
        # arquivo pois o FFmpeg ainda pode estar gravando nele.
        segments.pop()

    if not segments:
        return None

    max_duration = float(SEGMENT_TIME)
    playlist_items = []

    for i, seg in enumerate(segments):
        is_discontinuity = False
        if i > 0:
            gap_prev = (seg["datetime"] - segments[i-1]["datetime"]).total_seconds()
            if gap_prev > SEGMENT_TIME + 3:
                is_discontinuity = True

        if i < len(segments) - 1:
            gap_next = (segments[i+1]["datetime"] - seg["datetime"]).total_seconds()
            if 0 < gap_next <= SEGMENT_TIME + 3:
                duration = gap_next
            else:
                duration = float(SEGMENT_TIME)
        else:
            duration = float(SEGMENT_TIME)

        if duration > max_duration:
            max_duration = duration

        playlist_items.append({
            "discontinuity": is_discontinuity,
            "duration": duration,
            "datetime": seg["datetime"],
            "filename": seg["filename"]
        })

    target_duration = int(max_duration + 5)

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

        iso_time = item["datetime"].strftime("%Y-%m-%dT%H:%M:%S.000Z")
        lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{iso_time}")
        lines.append(f"#EXTINF:{item['duration']:.3f},")
        lines.append(f"/api/segment?camera={camera_name}&file={item['filename']}")

    lines.append("#EXT-X-ENDLIST")
    lines.append("")

    return "\n".join(lines)


