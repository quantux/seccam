import os

CONFIG_FOLDER = os.getenv("CONFIG_FOLDER", "/configs")
if not os.path.exists(CONFIG_FOLDER) and os.path.exists("configs"):
    CONFIG_FOLDER = os.path.abspath("configs")

VIDEOS_FOLDER = os.getenv("VIDEOS_FOLDER", "/videos")
if not os.path.exists(VIDEOS_FOLDER) and os.path.exists("videos"):
    VIDEOS_FOLDER = os.path.abspath("videos")

CAMERAS_JSON = os.path.join(CONFIG_FOLDER, "cameras.json")

# --- Multicam viewer (HLS/WebRTC servidos pelo mediamtx) ---
# O host de cada stream é extraído da própria rtsp_url do cameras.json.
# Portas HTTP do HLS (padrão 8888) e do WebRTC/WHEP (padrão 8889) no mediamtx.
MEDIAMTX_HLS_PORT = int(os.getenv("MEDIAMTX_HLS_PORT", "8888"))
MEDIAMTX_HLS_SCHEME = os.getenv("MEDIAMTX_HLS_SCHEME", "http").strip() or "http"
MEDIAMTX_WEBRTC_PORT = int(os.getenv("MEDIAMTX_WEBRTC_PORT", "8889"))
MEDIAMTX_WEBRTC_SCHEME = os.getenv("MEDIAMTX_WEBRTC_SCHEME", MEDIAMTX_HLS_SCHEME).strip() or "http"
# Sobrescreve o host detectado na rtsp_url (ex.: usar o mesmo host do navegador).
MEDIAMTX_HOST = os.getenv("MEDIAMTX_HOST", "").strip() or None

# --- Detecção de pessoas (ntfy) ---
DETECTION_JSON = os.path.join(CONFIG_FOLDER, "detection.json")
SNAPSHOTS_FOLDER = os.path.join(CONFIG_FOLDER, "snapshots")
ENABLE_DETECTOR = os.getenv("ENABLE_DETECTOR", "1").strip().lower() not in ("0", "false", "no", "off")

# --- Watchdog das câmeras (reinicia tomada Tapo se a câmera não voltar) ---
WATCHDOG_JSON = os.path.join(CONFIG_FOLDER, "watchdog.json")
ENABLE_WATCHDOG = os.getenv("ENABLE_WATCHDOG", "1").strip().lower() not in ("0", "false", "no", "off")

SEGMENT_TIME = int(os.getenv("SEGMENT_TIME", 60))  # segundos por arquivo (1 minuto)
DAYS_TO_KEEP = int(os.getenv("DAYS_TO_KEEP", 3)) # manter gravações por X dias
RETRY_DELAY = int(os.getenv("RETRY_DELAY", 10)) # Tempo de espera em segundos antes de reiniciar a gravação se o ffmpeg terminar ou falhar
UPLOAD_TIMES = [(7, 0), (19, 0)]  # horários de upload

CLIENT_SECRETS_FILE = os.path.join(CONFIG_FOLDER, "secrets", "client_secrets.json")
TOKENS_FILE = os.path.join(CONFIG_FOLDER, "secrets", "tokens.json")

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

# --- Limpeza de vídeos e logs ---
LOGS_FOLDER = os.path.join(CONFIG_FOLDER, "logs")
MAX_LOG_SIZE_MB = 20        # tamanho máximo de cada log antes de truncar
LOG_DAYS_TO_KEEP = 14       # remover logs com mais de X dias
CHECK_INTERVAL_HOURS = 6    # intervalo de verificação em horas
