import os

CONFIG_FOLDER = os.getenv("CONFIG_FOLDER", "/configs")
VIDEOS_FOLDER = os.getenv("VIDEOS_FOLDER", "/videos")
CAMERAS_JSON = os.path.join(CONFIG_FOLDER, "cameras.json")

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
