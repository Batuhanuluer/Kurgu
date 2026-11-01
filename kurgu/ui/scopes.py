"""Lumetri Skopları: dalga formu (luma), RGB parade, vektörskop, histogram.

Program monitöründe görünen kareden hesaplanır (küçültülmüş kopyadan; birkaç ms).
Panel görünür değilse hiç hesap yapılmaz.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from ..i18n import _

MODES = [("waveform", "Waveform (Luma)"), ("parade", "RGB Parade"),
         ("vectorscope", "Vectorscope"), ("histogram", "Histogram")]
SCOPE_W = 360  # hesaplama genişliği


def qimage_to_rgb(img: QImage, max_w: int = SCOPE_W) -> np.ndarray | None:
    if img is None or img.isNull():
        return None
    if img.width() > max_w:
        img = img.scaledToWidth(max_w, Qt.FastTransformation)
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * h)
    return buf.reshape(h, img.bytesPerLine())[:, :w * 3].reshape(h, w, 3).copy()


# ------------------------------------------------------------ hesaplar (Qt'siz, test edilebilir)
def waveform(chan: np.ndarray, bins: int = 256) -> np.ndarray:
    """(H, W) uint8 -> (bins, W) sayımlar: her sütunda değer dağılımı (üst = 255)."""
    h, w = chan.shape
    idx = (chan.astype(np.int32) * bins // 256)
    flat = idx * w + np.arange(w)[None, :]
    counts = np.bincount(flat.ravel(), minlength=bins * w).reshape(bins, w)
    return counts[::-1]


def luma(rgb: np.ndarray) -> np.ndarray:
    r, g, b = (rgb[..., i].astype(np.uint32) for i in range(3))
    return ((r * 54 + g * 183 + b * 19 + 128) >> 8).astype(np.uint8)  # BT.709, tam sayı


def vectorscope(rgb: np.ndarray, size: int = 256) -> np.ndarray:
    """Cb/Cr (BT.709) 2B histogramı (size, size); merkez = renksiz, yukarı = +Cr."""
    f = rgb.reshape(-1, 3).astype(np.float32) / 255
    cb = -0.1146 * f[:, 0] - 0.3854 * f[:, 1] + 0.5 * f[:, 2]
    cr = 0.5 * f[:, 0] - 0.4542 * f[:, 1] - 0.0458 * f[:, 2]
    x = np.clip(((cb + 0.5) * (size - 1)).astype(np.int32), 0, size - 1)
    y = np.clip(((0.5 - cr) * (size - 1)).astype(np.int32), 0, size - 1)
    return np.bincount(y * size + x, minlength=size * size).reshape(size, size)


def histogram(rgb: np.ndarray) -> np.ndarray:
    """(3, 256) kanal histogramları."""
    return np.stack([np.bincount(rgb[..., c].ravel(), minlength=256) for c in range(3)])


def intensity_image(counts: np.ndarray, color, gain: float = 1.0) -> QImage:
    """Sayımları parlaklığa çevirip renkli, saydam bir QImage yapar (log ölçek)."""
    v = np.log1p(counts.astype(np.float32))
    mx = float(v.max()) or 1.0
    a = np.clip(v / mx * 1.4 * gain, 0, 1)
    h, w = a.shape
    out = np.zeros((h, w, 4), np.uint8)
    out[..., 0] = color[0]
    out[..., 1] = color[1]
    out[..., 2] = color[2]
    out[..., 3] = (a * 255).astype(np.uint8)
    img = QImage(out.data, w, h, w * 4, QImage.Format_RGBA8888)
    return img.copy()


# Vektörskop hedefleri (%75 renk çubukları) ve cilt tonu çizgisi
def _target(r, g, b):
    cb = -0.1146 * r - 0.3854 * g + 0.5 * b
    cr = 0.5 * r - 0.4542 * g - 0.0458 * b
    return cb, cr


TARGETS = [("R", (0.75, 0, 0)), ("Mg", (0.75, 0, 0.75)), ("B", (0, 0, 0.75)),
           ("Cy", (0, 0.75, 0.75)), ("G", (0, 0.75, 0)), ("Yl", (0.75, 0.75, 0))]


class ScopeView(QWidget):
    def __init__(self):
        super().__init__()
        self.mode = "waveform"
        self.rgb = None
        self._cache = None
        self.setMinimumSize(240, 180)

    def set_frame(self, rgb):
        self.rgb = rgb
        self._cache = None
        self.update()

    def _images(self):
        if self._cache is not None or self.rgb is None:
            return self._cache
        rgb = self.rgb
        if self.mode == "waveform":
            self._cache = [intensity_image(waveform(luma(rgb)), (190, 255, 190))]
        elif self.mode == "parade":
            self._cache = [intensity_image(waveform(rgb[..., c]), col) for c, col in
                           enumerate([(255, 80, 80), (80, 255, 80), (90, 140, 255)])]
        elif self.mode == "vectorscope":
            self._cache = [intensity_image(vectorscope(rgb), (200, 255, 200), 1.2)]
        else:
            self._cache = [histogram(rgb)]
        return self._cache

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#0d0e10"))
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() * 0.8))
        p.setFont(f)
        imgs = self._images()
        r = QRectF(self.rect()).adjusted(34, 8, -8, -8)
        if self.mode in ("waveform", "parade"):
            self._grid_ire(p, r)
            if imgs:
                if self.mode == "waveform":
                    p.drawImage(r, imgs[0])
                else:
                    w = r.width() / 3
                    for i, im in enumerate(imgs):
                        p.drawImage(QRectF(r.left() + i * w + 2, r.top(), w - 4, r.height()), im)
        elif self.mode == "vectorscope":
            s = min(r.width(), r.height())
            vr = QRectF(r.center().x() - s / 2, r.center().y() - s / 2, s, s)
            self._grid_vector(p, vr)
            if imgs:
                p.drawImage(vr, imgs[0])
            self._targets(p, vr)  # hedefler izin üstünde kalsın
        else:
            self._grid_ire(p, r, labels=False)
            if imgs:
                self._draw_hist(p, r, imgs[0])
        if self.rgb is None:
            p.setPen(QColor("#5f6368"))
            p.drawText(self.rect(), Qt.AlignCenter, _("No frame"))
        p.end()

    def _grid_ire(self, p, r, labels=True):
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        for v in (0, 25, 50, 75, 100):
            y = r.bottom() - r.height() * v / 100
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            if labels:
                p.setPen(QColor("#6f757c"))
                p.drawText(QRectF(0, y - 7, 30, 14), Qt.AlignRight | Qt.AlignVCenter, str(v))
                p.setPen(QPen(QColor(255, 255, 255, 30), 1))

    def _grid_vector(self, p, vr):
        c = vr.center()
        rad = vr.width() / 2
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawEllipse(c, rad, rad)
        p.drawEllipse(c, rad * 0.5, rad * 0.5)
        p.drawLine(QPointF(c.x() - rad, c.y()), QPointF(c.x() + rad, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - rad), QPointF(c.x(), c.y() + rad))
        # Cilt tonu çizgisi (~123°, I ekseni)
        a = math.radians(123)
        p.setPen(QPen(QColor(255, 200, 150, 90), 1, Qt.DashLine))
        p.drawLine(c, QPointF(c.x() + math.cos(a) * rad, c.y() - math.sin(a) * rad))

    def _targets(self, p, vr):
        c = vr.center()
        rad = vr.width() / 2
        for name, rgb in TARGETS:
            cb, cr = _target(*rgb)
            q = QPointF(c.x() + cb * 2 * rad, c.y() - cr * 2 * rad)
            p.setPen(QPen(QColor(255, 255, 255, 120), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(q.x() - 5, q.y() - 5, 10, 10))
            p.setPen(QColor("#9aa0a6"))
            p.drawText(QRectF(q.x() + 6, q.y() - 14, 24, 12), Qt.AlignLeft, name)

    def _draw_hist(self, p, r, hist):
        from PySide6.QtGui import QPainterPath
        mx = float(np.log1p(hist).max()) or 1.0
        for c, col in enumerate([QColor(255, 80, 80, 150), QColor(80, 255, 80, 150),
                                 QColor(90, 140, 255, 150)]):
            path = QPainterPath(QPointF(r.left(), r.bottom()))
            vals = np.log1p(hist[c]) / mx
            for i, v in enumerate(vals):
                path.lineTo(QPointF(r.left() + r.width() * i / 255,
                                    r.bottom() - r.height() * float(v)))
            path.lineTo(QPointF(r.right(), r.bottom()))
            path.closeSubpath()
            fill = QColor(col)
            fill.setAlpha(60)
            p.setPen(QPen(col, 1))
            p.setBrush(fill)
            p.drawPath(path)


class ScopesPanel(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.mode = QComboBox()
        for k, label in MODES:
            self.mode.addItem(_(label), k)
        saved = main.settings.value("scope_mode", "waveform")
        self.mode.setCurrentIndex(max(0, self.mode.findData(saved)))
        self.mode.currentIndexChanged.connect(self._mode)
        self.view = ScopeView()
        self.view.mode = self.mode.currentData()
        top = QHBoxLayout()
        top.addWidget(self.mode, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._update)

    def _mode(self, _i):
        self.view.mode = self.mode.currentData()
        self.main.settings.setValue("scope_mode", self.view.mode)
        self.view._cache = None
        self.view.update()

    def request_update(self):
        """Monitör yenilendi: görünürse kısa bir gecikmeyle (birleştirerek) hesapla."""
        if self.isVisible() and not self.timer.isActive():
            self.timer.start(60 if self.main.player.playing else 15)

    def _update(self):
        self.view.set_frame(qimage_to_rgb(self.main.monitor.view.image))

    def showEvent(self, ev):
        super().showEvent(ev)
        self.request_update()
