import os
import datetime
import re
import statistics
from config import VIDEOS_FOLDER, SEGMENT_TIME


MIN_COMPLETE_SEGMENT_RATIO = 0.80

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
    
    for filename in sorted(os.listdir(camera_dir)):
        if filename.startswith(prefix) and filename.endswith(".ts"):
            base_name = os.path.splitext(filename)[0]
            try:
                # Converte o timestamp para objeto datetime
                dt = datetime.datetime.strptime(base_name, "%Y-%m-%d_%H-%M-%S")
                segments.append({"filename": filename, "datetime": dt})
            except ValueError:
                continue

    if not segments:
        return None

    # Ordena segmentos cronologicamente. O segmentador escreve diretamente no
    # arquivo final; portanto, o último arquivo do dia corrente pode ainda
    # estar aberto pelo FFmpeg e não deve ser anunciado ao player.
    segments.sort(key=lambda x: x["datetime"])

    now = datetime.datetime.now()
    if date_str == now.strftime("%Y-%m-%d") and segments:
        segments.pop()

    if not segments:
        return None

    # Após uma queda de RTSP/FFmpeg podem ficar arquivos truncados no diretório.
    # Como cada câmera mantém bitrate aproximadamente estável, o tamanho mediano
    # dos segmentos do próprio dia permite identificá-los sem executar ffprobe
    # para cada item a cada carregamento da playlist.
    sizes = [os.path.getsize(os.path.join(camera_dir, seg["filename"])) for seg in segments]
    typical_size = statistics.median(sizes)
    minimum_complete_size = typical_size * MIN_COMPLETE_SEGMENT_RATIO
    complete_segments = [
        seg for seg, size in zip(segments, sizes)
        if size >= minimum_complete_size
    ]

    if not complete_segments:
        return None

    # Constrói o texto M3U8 HLS VOD
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{SEGMENT_TIME}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        ""
    ]

    previous_datetime = None
    for seg in complete_segments:
        # Um intervalo grande indica que a gravação foi reiniciada. Informa o
        # player para não continuar os timestamps do arquivo anterior.
        if previous_datetime and (seg["datetime"] - previous_datetime).total_seconds() > SEGMENT_TIME * 1.5:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{float(SEGMENT_TIME):.3f},")
        lines.append(f"/api/segment?camera={camera_name}&file={seg['filename']}")
        previous_datetime = seg["datetime"]

    lines.append("#EXT-X-ENDLIST")
    lines.append("")

    return "\n".join(lines)
