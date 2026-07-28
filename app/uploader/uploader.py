import os
import json
import random
import time
import datetime
import logging
import requests
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from config import (
    CLIENT_SECRETS_FILE, TOKENS_FILE, YOUTUBE_UPLOAD_SCOPE,
    YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, LOGS_FOLDER
)
from uploader.token_manager import refresh_access_token


# --- CONFIGURAÇÃO DO LOG ---
os.makedirs(LOGS_FOLDER, exist_ok=True)
LOG_FILE = os.path.join(LOGS_FOLDER, f"uploader_{datetime.date.today()}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def log(msg, level="info"):
    """Escreve e exibe mensagem no log."""
    if level == "error":
        logger.error(msg)
    elif level == "warning":
        logger.warning(msg)
    else:
        logger.info(msg)


# --- AUTENTICAÇÃO ---
def get_authenticated_service():
    """Autentica e retorna serviço da API do YouTube."""
    log("Carregando credenciais do YouTube...")
    with open(CLIENT_SECRETS_FILE, "r") as f:
        client_secrets = json.load(f)["web"]
    with open(TOKENS_FILE, "r") as f:
        tokens = json.load(f)

    credentials_data = {
        "token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "token_uri": client_secrets["token_uri"],
        "client_id": client_secrets["client_id"],
        "client_secret": client_secrets["client_secret"],
    }
    credentials = Credentials.from_authorized_user_info(
        credentials_data, scopes=[YOUTUBE_UPLOAD_SCOPE]
    )
    log("Autenticação concluída.")
    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=credentials)


# --- UPLOAD ---
def initialize_upload(youtube, file_path, title):
    """Inicializa upload para o YouTube."""
    log(f"Iniciando upload de '{title}' ({file_path})...")
    body = {
        "snippet": {"title": title},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    }
    insert_request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(
            file_path, chunksize=-1, resumable=True, mimetype="video/mp2t"
        )
    )
    resumable_upload(insert_request, title)


def resumable_upload(insert_request, title):
    """Executa upload com repetição e log de progresso."""
    response = None
    error = None
    retry = 0
    while response is None:
        try:
            status, response = insert_request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                log(f"Upload '{title}': {progress}% concluído")
            if response and "id" in response:
                log(f"✅ Upload concluído: {title} (ID: {response['id']})")
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                error = f"Erro HTTP {e.resp.status}: {e.content}"
            else:
                log(f"❌ Erro irreversível no upload: {e}", "error")
                break
        except requests.exceptions.RequestException as e:
            error = f"Erro de conexão: {e}"

        if error:
            retry += 1
            if retry > 10:
                log(f"❌ Falha no upload '{title}', máximo de tentativas atingido.", "error")
                break
            sleep_time = random.random() * 2 ** retry
            log(f"{error}. Tentando novamente em {sleep_time:.1f}s...", "warning")
            time.sleep(sleep_time)
            error = None
