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
    
    for filename in sorted(os.listdir(camera_dir)):
        if filename.startswith(prefix) and filename.endswith(".ts"):
            base_name = os.path.splitext(filename)[0]
            try:
                # Converte o timestamp para objeto datetime
                dt = datetime.datetime.strptime(base_name, "%Y-%m-%d_%H-%M-%S")
                file_path = os.path.join(camera_dir, filename)
                size = os.path.getsize(file_path)
                # Descarta apenas arquivos realmente vazios ou corrompidos
                if size >= MIN_SEGMENT_SIZE_BYTES:
                    segments.append({"filename": filename, "datetime": dt, "size": size})
            except (ValueError, OSError):
                continue

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

    for i, seg in enumerate(segments):
        # 1. Se houver lacuna de tempo em relação ao segmento anterior, insere descontinuidade
        if i > 0:
            gap_prev = (seg["datetime"] - segments[i-1]["datetime"]).total_seconds()
            if gap_prev > SEGMENT_TIME * 2.5:
                lines.append("#EXT-X-DISCONTINUITY")

        # 2. Determina a duração exata do segmento baseada no horário do próximo segmento
        if i < len(segments) - 1:
            gap_next = (segments[i+1]["datetime"] - seg["datetime"]).total_seconds()
            if 0 < gap_next <= SEGMENT_TIME * 2.5:
                duration = gap_next
            else:
                duration = float(SEGMENT_TIME)
        else:
            duration = float(SEGMENT_TIME)

        lines.append(f"#EXTINF:{duration:.3f},")
        lines.append(f"/api/segment?camera={camera_name}&file={seg['filename']}")

    lines.append("#EXT-X-ENDLIST")
    lines.append("")

    return "\n".join(lines)

