"""Video geçişleri.

Bir geçiş iki katmanı (A: giden, B: gelen; ikisi de None olabilir) ilerleme
p (0→1) ile birleştirir. Katmanlar tam tuval boyutunda, saydam arka planlı
QImage'lardır. Ses tarafında tüm geçişler sabit güçlü crossfade uygular.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

# kimlik, ad, kategori
TRANSITIONS = [
    ("cross_dissolve", "Cross Dissolve", "Dissolve"),
    ("dip_black", "Dip to Black", "Dissolve"),
    ("dip_white", "Dip to White", "Dissolve"),
    ("wipe_lr", "Wipe Right", "Wipe"),
    ("wipe_rl", "Wipe Left", "Wipe"),
    ("wipe_tb", "Wipe Down", "Wipe"),
    ("wipe_bt", "Wipe Up", "Wipe"),
    ("barn_door", "Barn Door", "Wipe"),
    ("clock", "Clock Wipe", "Wipe"),
    ("push_l", "Push Left", "Slide"),
    ("push_r", "Push Right", "Slide"),
    ("slide_l", "Slide Left", "Slide"),
    ("slide_r", "Slide Right", "Slide"),
    ("iris_round", "Iris Round", "Iris"),
    ("iris_box", "Iris Box", "Iris"),
    ("cross_zoom", "Cross Zoom", "Zoom"),
]
NAMES = {k: n for k, n, _c in TRANSITIONS}
CATEGORIES = ["Dissolve", "Wipe", "Slide", "Iris", "Zoom"]
DEFAULT = "cross_dissolve"


def _ease(p):
    return p * p * (3 - 2 * p)


def _blank(w, h):
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    return img


def _blend(painter, layers, w, h):
    """layers: [(görüntü, opaklık, ölçek)] — doğrusal toplama (A·(1-p) + B·p)."""
    tmp = _blank(w, h)
    q = QPainter(tmp)
    q.setRenderHint(QPainter.SmoothPixmapTransform, True)
    q.setCompositionMode(QPainter.CompositionMode_Plus)
    for img, op, sc in layers:
        if img is None or op <= 0:
            continue
        q.setOpacity(min(op, 1.0))
        if sc == 1.0:
            q.drawImage(0, 0, img)
        else:
            q.drawImage(QRectF(w / 2 - w * sc / 2, h / 2 - h * sc / 2, w * sc, h * sc), img)
    q.end()
    painter.drawImage(0, 0, tmp)


def draw(painter: QPainter, kind: str, a: QImage | None, b: QImage | None, p: float,
         w: int, h: int):
    p = min(max(p, 0.0), 1.0)
    if kind == "cross_dissolve" or kind not in NAMES:
        _blend(painter, [(a, 1 - p, 1.0), (b, p, 1.0)], w, h)
        return
    if kind in ("dip_black", "dip_white"):
        color = QColor(0, 0, 0) if kind == "dip_black" else QColor(255, 255, 255)
        if p < 0.5:
            img, amount = a, p * 2
        else:
            img, amount = b, 2 - p * 2
        if img is not None:
            painter.drawImage(0, 0, img)
        painter.save()
        painter.setOpacity(amount)
        painter.fillRect(0, 0, w, h, color)
        painter.restore()
        return
    if kind == "cross_zoom":
        _blend(painter, [(a, 1 - p, 1 + p), (b, p, 2 - p)], w, h)
        return
    e = _ease(p)
    if kind.startswith(("push", "slide")):
        sign = -1 if kind.endswith("_l") else 1
        if kind.startswith("push"):
            if a is not None:
                painter.drawImage(QPointF(sign * e * w, 0), a)
        elif a is not None:
            painter.drawImage(0, 0, a)
        if b is not None:
            painter.drawImage(QPointF(-sign * (1 - e) * w, 0), b)
        return
    # Maske tabanlı geçişler: A tam, B maske içinde
    if a is not None:
        painter.drawImage(0, 0, a)
    if b is None:
        return
    path = QPainterPath()
    if kind == "wipe_lr":
        path.addRect(QRectF(0, 0, w * e, h))
    elif kind == "wipe_rl":
        path.addRect(QRectF(w * (1 - e), 0, w * e, h))
    elif kind == "wipe_tb":
        path.addRect(QRectF(0, 0, w, h * e))
    elif kind == "wipe_bt":
        path.addRect(QRectF(0, h * (1 - e), w, h * e))
    elif kind == "barn_door":
        path.addRect(QRectF(w / 2 * (1 - e), 0, w * e, h))
    elif kind == "iris_round":
        r = e * math.hypot(w, h) / 2
        path.addEllipse(QPointF(w / 2, h / 2), r, r)
    elif kind == "iris_box":
        path.addRect(QRectF(w / 2 * (1 - e), h / 2 * (1 - e), w * e, h * e))
    elif kind == "clock":
        big = math.hypot(w, h)
        c = QPointF(w / 2, h / 2)
        path.moveTo(c)
        path.arcTo(QRectF(c.x() - big, c.y() - big, 2 * big, 2 * big), 90, -360 * p)
        path.closeSubpath()
    painter.save()
    painter.setClipPath(path, Qt.IntersectClip)
    painter.drawImage(0, 0, b)
    painter.restore()
