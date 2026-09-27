"""Captura de RTSP para o detector.

Decodifica a stream com o ffmpeg e entrega frames BGR como numpy. No Raspberry
Pi usa o decoder de hardware H.264 (v4l2m2m, /dev/video10); em x86_64 cai para
software automaticamente (ou sobrescreva com SECCAM_HWACCEL).
"""

import os
import platform
import subprocess
import time

import numpy as np


def pick_hw_decoder():
    """Retorna o nome do decoder ffmpeg a usar (ou None para software)."""
    override = os.getenv("SECCAM_HWACCEL", "auto").strip().lower()
    if override in ("off", "none", "0", "false", "sw", "software"):
        return None
    if override not in ("auto", ""):
        return override
    machine = platform.machine().lower()
    if machine in ("aarch64", "armv7l", "armv6l") and os.path.exists("/dev/video10"):
        return "h264_v4l2m2m"
    return None


def _base_cmd(source, decoder):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if decoder:
        cmd += ["-c:v", decoder]
    cmd += ["-rtsp_transport", "tcp", "-fflags", "nobuffer", "-i", source]
    return cmd


def probe_size(source, decoder=None, timeout=15):
    """Descobre (width, height) do video via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-rtsp_transport", "tcp",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0", source,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    val = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
    if "x" not in val:
        raise RuntimeError(f"Nao foi possivel obter resolucao de {source!r}: {out.stderr.strip()}")
    w, h = val.split("x")[:2]
    return int(w), int(h)


def grab_frame_jpeg(source, decoder=None, timeout=20):
    """Captura um unico frame e retorna bytes JPEG (para a pagina de ROI)."""
    cmd = _base_cmd(source, decoder) + [
        "-frames:v", "1", "-f", "image2", "-vcodec", "mjpeg", "pipe:1",
    ]
    out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(f"Falha ao capturar frame: {out.stderr.decode(errors='ignore').strip()}")
    return out.stdout


class RTSPStream:
    """Le frames BGR de uma RTSP em fps fixo, reconectando sozinho."""

    def __init__(self, source, fps=5, decoder=None, reconnect_delay=5):
        self.source = source
        self.fps = fps
        self.decoder = decoder if decoder is not None else pick_hw_decoder()
        self.reconnect_delay = reconnect_delay
        self.width = None
        self.height = None

    def _spawn(self):
        if self.width is None or self.height is None:
            self.width, self.height = probe_size(self.source, self.decoder)
        cmd = _base_cmd(self.source, self.decoder) + [
            "-an", "-vf", f"fps={self.fps}", "-pix_fmt", "bgr24",
            "-f", "rawvideo", "pipe:1",
        ]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def frames(self):
        """Gerador que produz (frame_bgr, timestamp) indefinidamente."""
        frame_size = None
        while True:
            proc = self._spawn()
            frame_size = self.width * self.height * 3
            try:
                while True:
                    buf = proc.stdout.read(frame_size)
                    if not buf or len(buf) < frame_size:
                        break
                    frame = np.frombuffer(buf, dtype=np.uint8).reshape(
                        (self.height, self.width, 3)
                    )
                    yield frame.copy(), time.time()
            finally:
                proc.kill()
                proc.wait()
            time.sleep(self.reconnect_delay)
