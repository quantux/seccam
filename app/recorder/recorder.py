import os
import subprocess
import threading
import datetime
import time
import json

from config import CONFIG_FOLDER, VIDEOS_FOLDER, CAMERAS_JSON, SEGMENT_TIME, RETRY_DELAY

active_camera_threads = set()

def start_recording(camera_name, rtsp_url):
    camera_folder = os.path.join(VIDEOS_FOLDER, camera_name)
    os.makedirs(camera_folder, exist_ok=True)
    os.makedirs(os.path.join(CONFIG_FOLDER, "logs"), exist_ok=True)

    output_pattern = os.path.join(camera_folder, "%Y-%m-%d_%H-%M-%S.ts")
    log_file = os.path.join(CONFIG_FOLDER, "logs", f"{camera_name}.log")

    while True:
        cmd = [
            "ffmpeg",
            "-fflags", "+genpts",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-c:v", "copy",
            "-c:a", "aac",
            "-f", "segment",
            "-segment_time", str(SEGMENT_TIME),
            "-strftime", "1",
            # Sem -use_wallclock_as_timestamps: ele atribuía PTS pelo relógio de
            # chegada dos pacotes (bursty na rede), gerando offset de ~1.4s e
            # desync de áudio que variava a cada segmento. Com o reset abaixo,
            # cada segmento agora começa em PTS 0 limpo e consistente.
            "-reset_timestamps", "1",
            "-segment_format", "mpegts",
            "-max_muxing_queue_size", "4096",
            output_pattern
        ]

        try:
            with open(log_file, "a") as log:
                log.write(f"[{datetime.datetime.now()}] Iniciando gravação FFmpeg de {camera_name} ({rtsp_url})...\n")
                process = subprocess.Popen(cmd, stdout=log, stderr=log)
                process.wait()
                log.write(f"[{datetime.datetime.now()}] FFmpeg terminou com código {process.returncode}\n")
        except Exception as e:
            with open(log_file, "a") as log:
                log.write(f"[{datetime.datetime.now()}] Erro no FFmpeg: {e}\n")

        time.sleep(RETRY_DELAY)

def start_recording_for_all_cameras():
    """
    Monitora continuamente o arquivo cameras.json e inicia o FFmpeg para qualquer nova câmera cadastrada.
    """
    while True:
        if os.path.exists(CAMERAS_JSON):
            try:
                with open(CAMERAS_JSON, "r") as f:
                    cameras = json.load(f).get("cameras", [])

                for camera in cameras:
                    cam_name = camera.get("name")
                    rtsp_url = camera.get("rtsp_url")
                    
                    if cam_name and rtsp_url and cam_name not in active_camera_threads:
                        print(f"[recorder] Iniciando gravação para nova câmera: {cam_name}")
                        active_camera_threads.add(cam_name)
                        t = threading.Thread(
                            target=start_recording,
                            args=(cam_name, rtsp_url),
                            daemon=True
                        )
                        t.start()
            except Exception as e:
                print(f"[recorder] Erro ao ler {CAMERAS_JSON}: {e}")
        else:
            print(f"[recorder] Arquivo {CAMERAS_JSON} não encontrado. Aguardando cadastro via interface web ou arquivo...")

        time.sleep(10)
