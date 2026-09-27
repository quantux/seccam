"""Inicializacao do detector: uma thread por camera + limpeza de snapshots."""

import datetime
import logging
import os
import threading
import time

from config import SNAPSHOTS_FOLDER
from detector.engine import CameraWorker
from detector.settings import load_settings

log = logging.getLogger("detector")

_stop = threading.Event()


def _cleanup_snapshots(keep_days):
    cutoff = time.time() - keep_days * 86400
    for root, _dirs, files in os.walk(SNAPSHOTS_FOLDER):
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass


def _cleanup_loop(keep_days):
    while not _stop.is_set():
        try:
            _cleanup_snapshots(keep_days)
        except Exception as exc:
            log.warning("limpeza de snapshots falhou: %s", exc)
        _stop.wait(6 * 3600)


def start_detection():
    """Sobe uma thread por camera habilitada. Idempotente por execucao."""
    settings = load_settings()
    settings["_snapshots_folder"] = SNAPSHOTS_FOLDER
    os.makedirs(SNAPSHOTS_FOLDER, exist_ok=True)

    cameras = [c for c in settings.get("cameras", []) if c.get("enabled") and c.get("source")]
    if not cameras:
        log.warning("detector: nenhuma camera habilitada em detection.json")
        return

    if not settings.get("notify", {}).get("topic"):
        log.warning("detector: 'notify.topic' vazio - alertas nao serao enviados")

    threading.Thread(
        target=_cleanup_loop,
        args=(settings.get("snapshot_keep_days", 7),),
        daemon=True,
        name="detector-cleaner",
    ).start()

    for cam in cameras:
        worker = CameraWorker(cam, settings)
        threading.Thread(target=worker.run, daemon=True, name=f"detector-{cam['name']}").start()
        log.info("detector: thread iniciada para %s", cam["name"])
        time.sleep(0.5)
