import threading
import time
import uvicorn
from recorder.recorder import start_recording_for_all_cameras
from recorder.process import upload_last_hours
from cleaner import clean_old_videos
from config import UPLOAD_TIMES
from api.server import app

def run_web_server():
    """Inicia o servidor API FastAPI/Uvicorn para o player HLS no navegador."""
    print("Iniciando servidor web HTTP em http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

def schedule_upload():
    """Agenda uploads automáticos nos horários definidos em UPLOAD_TIMES."""
    last_run = {}

    while True:
        now = time.localtime()
        for hour, minute in UPLOAD_TIMES:
            key = f"{hour:02d}:{minute:02d}"
            # executa apenas uma vez por dia em cada horário
            if now.tm_hour == hour and now.tm_min == minute and last_run.get(key) != now.tm_mday:
                print(f"Iniciando upload programado: {time.strftime('%Y-%m-%d %H:%M')}")
                try:
                    upload_last_hours()
                except Exception as e:
                    print(f"Erro no upload programado: {e}")
                last_run[key] = now.tm_mday
        time.sleep(20)

def main():
    """Inicia servidor web, gravação, limpeza e rotina de upload em threads paralelas."""
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=start_recording_for_all_cameras, daemon=True).start()
    threading.Thread(target=clean_old_videos, daemon=True).start()
    threading.Thread(target=schedule_upload, daemon=True).start()

    # Mantém o programa principal ativo
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
