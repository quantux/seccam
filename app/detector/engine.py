"""Motor de deteccao por camera.

Fluxo:
    frame BGR -> ROI -> gate de movimento -> modelo (person)
    presenca continua por >= dwell_seconds -> snapshot + ntfy (1x por evento)
    saida da ROI por > absence_tolerance_seconds -> rearma

Importante: o movimento so serve para *acordar* a inferencia. Uma pessoa parada
nao gera movimento, entao a presenca e sustentada pelas deteccoes do modelo
enquanto o evento estiver ativo.
"""

import datetime
import logging
import os
import time

import cv2

from detector import notify
from detector.model import NanoDet
from detector.stream import RTSPStream

log = logging.getLogger("detector")


def _roi_rect(roi, width, height):
    if not roi:
        return 0, 0, width, height
    x0 = max(0, min(width - 1, int(round(roi.get("x", 0.0) * width))))
    y0 = max(0, min(height - 1, int(round(roi.get("y", 0.0) * height))))
    w = max(1, int(round(roi.get("w", 1.0) * width)))
    h = max(1, int(round(roi.get("h", 1.0) * height)))
    x1 = min(width, x0 + w)
    y1 = min(height, y0 + h)
    return x0, y0, x1 - x0, y1 - y0


class CameraWorker:
    def __init__(self, cam_cfg, settings):
        self.name = cam_cfg["name"]
        self.source = cam_cfg["source"]
        self.roi = cam_cfg.get("roi")
        self.settings = settings
        self.model = NanoDet(prob_threshold=settings.get("probability_threshold", 0.5))
        self.stream = RTSPStream(self.source, fps=settings.get("poll_fps", 5))

        self._bg = None
        self._person = False
        self.state = "idle"          # idle | tracking | alerted
        self.first_seen = 0.0
        self.last_seen = 0.0
        self.last_frame = None
        self.last_boxes = []

    # ── deteccao de movimento na ROI ────────────────────────────────────────
    def _motion(self, roi_gray):
        blur = cv2.GaussianBlur(roi_gray, (21, 21), 0)
        if self._bg is None or self._bg.shape != blur.shape:
            self._bg = blur.astype("float")
            return False
        diff = cv2.absdiff(blur, cv2.convertScaleAbs(self._bg))
        _, th = cv2.threshold(diff, self.settings.get("motion_threshold", 25), 255, cv2.THRESH_BINARY)
        ratio = float(th.mean()) / 255.0
        cv2.accumulateWeighted(blur, self._bg, 0.05)
        return ratio >= self.settings.get("motion_ratio", 0.01)

    def _detect_person(self, roi_bgr, x0, y0):
        dets = self.model.detect(roi_bgr, person_only=True)
        return [(x0 + d[0], y0 + d[1], x0 + d[2], y0 + d[3], d[4]) for d in dets]

    # ── snapshot + alerta ───────────────────────────────────────────────────
    def _snapshot_jpeg(self, frame, boxes, dwell):
        vis = frame.copy()
        for x1, y1, x2, y2, conf in boxes:
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(vis, f"person {conf:.2f}", (x1, max(15, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(vis, f"{self.name}  {ts}  ({dwell:.0f}s na area)", (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        max_w = self.settings.get("snapshot_max_width", 1280)
        if vis.shape[1] > max_w:
            scale = max_w / vis.shape[1]
            vis = cv2.resize(vis, (max_w, int(vis.shape[0] * scale)), interpolation=cv2.INTER_AREA)

        ok, buf = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, self.settings.get("snapshot_quality", 85)])
        if not ok:
            return None
        return buf.tobytes()

    def _save_snapshot(self, jpeg, when):
        folder = os.path.join(self.settings["_snapshots_folder"], self.name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, when.strftime("%Y-%m-%d_%H-%M-%S") + ".jpg")
        with open(path, "wb") as f:
            f.write(jpeg)
        return path

    def _alert(self, dwell):
        now = datetime.datetime.now()
        jpeg = self._snapshot_jpeg(self.last_frame, self.last_boxes, dwell)
        if jpeg is None:
            return
        try:
            self._save_snapshot(jpeg, now)
        except Exception as exc:
            log.warning("[%s] falha ao salvar snapshot: %s", self.name, exc)
        try:
            notify.publish_image(
                self.settings["notify"],
                jpeg,
                title=f"Pessoa na porta ({self.name})",
                message=f"Pessoa parada na area ha {dwell:.0f}s - {now:%H:%M:%S}",
                filename=f"{self.name}_{now:%Y%m%d_%H%M%S}.jpg",
            )
        except Exception as exc:
            log.error("[%s] falha ao enviar ntfy: %s", self.name, exc)

    # ── loop principal ──────────────────────────────────────────────────────
    def run(self):
        log.info("[%s] detector iniciado (fonte=%s, decoder=%s)", self.name, self.source, self.stream.decoder)
        detect_fps = self.settings.get("detect_fps", 5)
        poll_fps = self.settings.get("poll_fps", 5)
        min_interval = 1.0 / detect_fps if detect_fps > 0 else 0.0
        dwell_s = self.settings.get("dwell_seconds", 5)
        tol = self.settings.get("absence_tolerance_seconds", 2)
        last_detect = 0.0

        for frame, _ts in self.stream.frames():
            now = time.monotonic()
            self.last_frame = frame
            h, w = frame.shape[:2]
            x0, y0, rw, rh = _roi_rect(self.roi, w, h)
            roi_bgr = frame[y0:y0 + rh, x0:x0 + rw]
            if roi_bgr.size == 0:
                continue
            roi_gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
            motion = self._motion(roi_gray)

            # So roda inferencia quando ha movimento ou ja estamos rastreando.
            # Frames sem inferencia mantem o ultimo resultado REAL (nao assumem
            # presenca), senao a tolerancia de ausencia nunca expira.
            fresh = False
            if (motion or self.state in ("tracking", "alerted")) and \
                    (now - last_detect) >= min_interval - 1e-6:
                last_detect = now
                self.last_boxes = self._detect_person(roi_bgr, x0, y0)
                self._person = len(self.last_boxes) > 0
                fresh = True
            person = self._person

            if self.state == "idle":
                if person:
                    self.state = "tracking"
                    self.first_seen = self.last_seen = now
                    log.info("[%s] pessoa detectada, iniciando contagem", self.name)
            elif self.state == "tracking":
                if person:
                    if fresh:
                        self.last_seen = now
                        if (now - self.first_seen) >= dwell_s:
                            dwell = now - self.first_seen
                            log.info("[%s] permaneceu %.0fs -> alerta", self.name, dwell)
                            self._alert(dwell)
                            self.state = "alerted"
                elif (now - self.last_seen) > tol:
                    log.info("[%s] pessoa saiu antes de %.0fs -> cancela", self.name, dwell_s)
                    self.state = "idle"
            elif self.state == "alerted":
                if person:
                    if fresh:
                        self.last_seen = now
                elif (now - self.last_seen) > tol:
                    log.info("[%s] area livre, rearmado", self.name)
                    self.state = "idle"
