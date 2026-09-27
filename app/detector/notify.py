"""Envio de notificacoes com imagem via ntfy."""

import logging

import requests

log = logging.getLogger("detector.notify")


def publish_image(notify_cfg, jpeg_bytes, title, message, filename=None,
                  tags=None, priority=None, timeout=15):
    """Publica uma imagem como anexo no ntfy.

    notify_cfg esperado:
        {"url": "https://ntfy.sh", "topic": "...", "token": null,
         "tags": "rotating_light", "priority": "high"}
    """
    url = (notify_cfg.get("url") or "https://ntfy.sh").rstrip("/")
    topic = notify_cfg.get("topic")
    if not topic:
        raise ValueError("ntfy: 'topic' nao configurado na deteccao")

    headers = {
        "X-Filename": filename or "snapshot.jpg",
        "X-Message": message,
        "X-Title": title,
        "X-Tags": tags or notify_cfg.get("tags") or "rotating_light",
        "X-Priority": priority or notify_cfg.get("priority") or "high",
    }
    token = notify_cfg.get("token")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.put(f"{url}/{topic}", data=jpeg_bytes, headers=headers, timeout=timeout)
    resp.raise_for_status()
    log.info("ntfy: notificacao enviada (%d bytes)", len(jpeg_bytes))
    return resp
