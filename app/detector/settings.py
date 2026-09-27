"""Configuracao persistente do detector (configs/detection.json)."""

import json
import os
import threading

from config import DETECTION_JSON

_lock = threading.Lock()

DEFAULTS = {
    "poll_fps": 5,
    "dwell_seconds": 5,
    "absence_tolerance_seconds": 2,
    "probability_threshold": 0.5,
    "motion_threshold": 25,
    "motion_ratio": 0.01,
    "detect_fps": 5,
    "snapshot_max_width": 1280,
    "snapshot_quality": 85,
    "snapshot_keep_days": 7,
    "notify": {
        "url": "https://ntfy.sh",
        "topic": "",
        "token": None,
        "tags": "rotating_light",
        "priority": "high",
    },
    "cameras": [],
}


def _merge_defaults(data):
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in (data or {}).items():
        if k == "notify" and isinstance(v, dict):
            out["notify"].update(v)
        else:
            out[k] = v
    return out


def load_settings():
    if not os.path.exists(DETECTION_JSON):
        return json.loads(json.dumps(DEFAULTS))
    try:
        with open(DETECTION_JSON, "r") as f:
            return _merge_defaults(json.load(f))
    except Exception:
        return json.loads(json.dumps(DEFAULTS))


def save_settings(data):
    with _lock:
        os.makedirs(os.path.dirname(DETECTION_JSON), exist_ok=True)
        merged = _merge_defaults(data)
        with open(DETECTION_JSON, "w") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        return merged
