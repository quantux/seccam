"""Detector de objetos leve (NanoDet, ONNX) via OpenCV DNN.

Modelo: NanoDet-m-plus-1.5x 416 (OpenCV Zoo, Apache-2.0).
Treinado no COCO; a classe de interesse e 'person' (indice 0).

O pós-processamento foi reescrito porque o `nanodet.py` do proprio OpenCV Zoo
esta desatualizado em relacao ao ONNX publicado: o modelo tem 3 niveis
(strides 8/16/32) e as saidas vem agrupadas como
[cls_8, cls_16, cls_32, bbox_8, bbox_16, bbox_32], nao intercaladas.
"""

import os

import cv2
import numpy as np

STRIDES = (8, 16, 32)
IMAGE_SIZE = (416, 416)
REG_MAX = 7
PROJECT = np.arange(REG_MAX + 1)
MEAN = np.array([103.53, 116.28, 123.675], dtype=np.float32).reshape(1, 1, 3)
STD = np.array([57.375, 57.12, 58.395], dtype=np.float32).reshape(1, 1, 3)

PERSON_CLASS_ID = 0

DEFAULT_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "nanodet.onnx")


def _build_anchors():
    anchors = {}
    for stride in STRIDES:
        fh = IMAGE_SIZE[0] // stride
        fw = IMAGE_SIZE[1] // stride
        sx = np.arange(fw) * stride
        sy = np.arange(fh) * stride
        xv, yv = np.meshgrid(sx, sy)
        cx = xv.flatten() + 0.5 * (stride - 1)
        cy = yv.flatten() + 0.5 * (stride - 1)
        anchors[stride] = np.column_stack((cx, cy))
    return anchors


def _letterbox(src, target=IMAGE_SIZE):
    """Redimensiona mantendo proporcao e centraliza com bordas (pad 0)."""
    img = src
    top, left, newh, neww = 0, 0, target[0], target[1]
    if img.shape[0] != img.shape[1]:
        scale = img.shape[0] / img.shape[1]
        if scale > 1:
            newh, neww = target[0], max(1, int(target[1] / scale))
            img = cv2.resize(img, (neww, newh), interpolation=cv2.INTER_AREA)
            left = int((target[1] - neww) * 0.5)
            img = cv2.copyMakeBorder(
                img, 0, 0, left, target[1] - neww - left, cv2.BORDER_CONSTANT, value=0
            )
        else:
            newh, neww = max(1, int(target[0] * scale)), target[1]
            img = cv2.resize(img, (neww, newh), interpolation=cv2.INTER_AREA)
            top = int((target[0] - newh) * 0.5)
            img = cv2.copyMakeBorder(
                img, top, target[0] - newh - top, 0, 0, cv2.BORDER_CONSTANT, value=0
            )
    else:
        img = cv2.resize(img, target, interpolation=cv2.INTER_AREA)
    return img, [top, left, newh, neww]


class NanoDet:
    """Wrapper fino do NanoDet para OpenCV DNN."""

    def __init__(self, model_path=DEFAULT_MODEL_PATH, prob_threshold=0.5, iou_threshold=0.6):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Modelo nao encontrado: {model_path}")
        self.net = cv2.dnn.readNet(model_path)
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.prob_threshold = prob_threshold
        self.iou_threshold = iou_threshold
        self.input_names = self.net.getUnconnectedOutLayersNames()
        self.anchors = _build_anchors()

    def _decode(self, outs):
        """Pareia saidas cls/bbox por numero de anchors e decodifica.

        Diferentes versoes do OpenCV devolvem as 6 saidas em ordens distintas,
        entao nao confiamos na posicao: agrupamos por formato.
        """
        num_boxes = (REG_MAX + 1) * 4
        bbox_by_n = {}
        cls_by_n = {}
        for out in outs:
            out = out.reshape(-1, out.shape[-1])
            if out.shape[-1] == num_boxes:
                bbox_by_n[out.shape[0]] = out
            else:
                cls_by_n[out.shape[0]] = out

        boxes, scores = [], []
        for n in sorted(cls_by_n.keys(), reverse=True):
            if n not in bbox_by_n:
                continue
            stride = int(round(IMAGE_SIZE[0] / (n ** 0.5)))
            anchor = self.anchors.get(stride)
            if anchor is None or anchor.shape[0] != n:
                continue
            cls_score = cls_by_n[n]
            bbox_pred = bbox_by_n[n].reshape(-1, 4, REG_MAX + 1)
            exp = np.exp(bbox_pred - bbox_pred.max(axis=2, keepdims=True))
            prob = exp / exp.sum(axis=2, keepdims=True)
            dist = (prob * PROJECT).sum(axis=2) * stride
            x1 = np.clip(anchor[:, 0] - dist[:, 0], 0, IMAGE_SIZE[1])
            y1 = np.clip(anchor[:, 1] - dist[:, 1], 0, IMAGE_SIZE[0])
            x2 = np.clip(anchor[:, 0] + dist[:, 2], 0, IMAGE_SIZE[1])
            y2 = np.clip(anchor[:, 1] + dist[:, 3], 0, IMAGE_SIZE[0])
            boxes.append(np.column_stack([x1, y1, x2, y2]))
            scores.append(cls_score)
        return np.concatenate(boxes, 0), np.concatenate(scores, 0)

    def detect(self, image_bgr, person_only=True, prob_threshold=None):
        """Recebe BGR (H, W, 3) e devolve [(x1,y1,x2,y2,conf,class_id)] no espaco da imagem."""
        prob_threshold = self.prob_threshold if prob_threshold is None else prob_threshold
        padded, lb = _letterbox(image_bgr)
        blob = cv2.dnn.blobFromImage((padded.astype(np.float32) - MEAN) / STD)
        self.net.setInput(blob)
        outs = self.net.forward(self.input_names)
        boxes, scores = self._decode(outs)

        class_ids = scores.argmax(axis=1) if scores.size else np.array([])
        conf = scores.max(axis=1) if scores.size else np.array([])

        if person_only:
            keep = class_ids == PERSON_CLASS_ID
            boxes, conf, class_ids = boxes[keep], conf[keep], class_ids[keep]

        if len(boxes) == 0:
            return []

        wh = boxes.copy()
        wh[:, 2:4] -= wh[:, 0:2]
        idx = cv2.dnn.NMSBoxes(
            wh.tolist(), conf.tolist(), prob_threshold, self.iou_threshold
        )
        if len(idx) == 0:
            return []
        idx = np.array(idx).flatten()

        top, left, newh, neww = lb
        h, w = image_bgr.shape[:2]
        results = []
        for i in idx:
            b = boxes[i].astype(np.float32)
            if h == w:
                b = b * (h / newh)
            else:
                ratioh, ratiow = h / newh, w / neww
                b[0] = max((b[0] - left) * ratiow, 0)
                b[1] = max((b[1] - top) * ratioh, 0)
                b[2] = min((b[2] - left) * ratiow, w)
                b[3] = min((b[3] - top) * ratioh, h)
            results.append(
                (int(b[0]), int(b[1]), int(b[2]), int(b[3]), float(conf[i]), int(class_ids[i]))
            )
        return results
