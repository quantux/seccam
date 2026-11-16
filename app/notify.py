"""Notificacoes de eventos do watchdog (Telegram e/ou ntfy).

Desligado por padrao (``NOTIFY_ENABLED=0``) para a imagem continuar portatil.
Cada destino e independente: se um falhar, o outro continua. Nao ha fila
(retry) — alerta de infraestrutura nao deve atrasar o watchdog.

Variaveis de ambiente:
  NOTIFY_ENABLED=1
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  NTFY_URL (ex.: http://192.168.1.2:2586), NTFY_TOPIC, NTFY_TOKEN (opcional)
"""

import logging
import os
import threading
import time

import requests

log = logging.getLogger("notify")

TIMEOUT = 8
_QUEUE: list[tuple[str, str, str]] = []
_LOCK = threading.Lock()
_SENT: dict[str, float] = {}


def enabled() -> bool:
    return os.getenv("NOTIFY_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def _telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            log.warning("telegram: HTTP %s %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram: falhou (%s)", exc)
        return False


def _ntfy(text: str, title: str, priority: str) -> bool:
    url = os.getenv("NTFY_URL", "").strip().rstrip("/")
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not url or not topic:
        return False
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority or "default",
        "Tags": "camera",
    }
    token = os.getenv("NTFY_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.post(f"{url}/{topic}", data=text.encode("utf-8"),
                          headers=headers, timeout=TIMEOUT)
        if r.status_code >= 300:
            log.warning("ntfy: HTTP %s %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("ntfy: falhou (%s)", exc)
        return False


def _send(item: tuple[str, str, str]) -> None:
    title, text, priority = item
    full = f"{title}\n{text}" if title else text
    ok_tg = _telegram(full)
    ok_ntfy = _ntfy(text, title or "seccam", priority)
    if not ok_tg and not ok_ntfy:
        log.error("notify: nenhum canal entregou: %s", title)


def _worker() -> None:
    while True:
        with _LOCK:
            item = _QUEUE.pop(0) if _QUEUE else None
        if item is None:
            time.sleep(1)
            continue
        _send(item)


def notify(title: str, text: str, priority: str = "default", key: str | None = None,
           min_interval: float = 0) -> None:
    """Enfileira uma notificacao (nao bloqueia o watchdog)."""
    if not enabled():
        return
    if key and min_interval > 0:
        now = time.time()
        last = _SENT.get(key, 0)
        if now - last < min_interval:
            return
        _SENT[key] = now
    with _LOCK:
        _QUEUE.append((title, text, priority))
        if len(_QUEUE) > 50:
            _QUEUE.pop(0)


def start_worker() -> None:
    if not enabled():
        log.info("notificacoes desativadas (NOTIFY_ENABLED=0)")
        return
    threading.Thread(target=_worker, name="notify", daemon=True).start()
    log.info("notificacoes ativas (telegram=%s, ntfy=%s)",
             bool(os.getenv("TELEGRAM_BOT_TOKEN")), bool(os.getenv("NTFY_URL")))
