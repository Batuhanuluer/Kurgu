"""Lumetri Renk paneli: temel düzeltme, eğriler ve renk tekerlekleri.

Seçili video klibindeki "lumetri" efektini düzenler; klipte yoksa ilk değişiklikte ekler.
Sayısal değerler anahtar karelenebilir (Denetçi'deki ◆ ile aynı sistem).
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSlider, QTabBar, QVBoxLayout,
                               QWidget)

from .. import effects as fx
from .. import grading as gr
from .. import keyframes as kf
from ..i18n import _
from .panels import SliderRow


# ------------------------------------------------------------ renk tekerleği
class ColorWheel(QWidget):
    """Premiere'deki renk tekerleği: noktayı sürükle (renk kayması), çift tık sıfırlar.
    Shift ile sürükleme hassas (yavaş) hareket eder."""

    changed = Signal(float, float)
    pressed = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(84, 84)
        self.xy = (0.0, 0.0)
        self._drag = None

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(130, 130)

    def _geom(self):
        s = min(self.width(), self.height()) - 8
        return QPointF(self.width() / 2, self.height() / 2), s / 2

    def set_value(self, xy):
        self.xy = (float(xy[0]), float(xy[1]))
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        c, r = self._geom()
        cg = QConicalGradient(c, gr.HUE_OFFSET)
        # Qt konik gradyanı saat yönünün tersine; 0° = kırmızı (wheel_rgb ile aynı yön)
        for i, col in enumerate(["#ff3030", "#ffff30", "#30ff30", "#30ffff", "#3030ff",
                                 "#ff30ff", "#ff3030"]):
            cg.setColorAt(i / 6, QColor(col))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(cg))
        p.drawEllipse(c, r, r)
        rg = QRadialGradient(c, r)
        rg.setColorAt(0, QColor(40, 42, 46, 255))
        rg.setColorAt(0.55, QColor(40, 42, 46, 200))
        rg.setColorAt(1, QColor(40, 42, 46, 40))
        p.setBrush(QBrush(rg))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(QColor(255, 255, 255, 50), 1))
        p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
        x, y = self.xy
        pt = QPointF(c.x() + x * r, c.y() - y * r)
        if abs(x) > 1e-4 or abs(y) > 1e-4:
            p.setPen(QPen(QColor(255, 255, 255, 140), 1))
            p.drawLine(c, pt)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(pt, 5, 5)
        p.end()

    def mousePressEvent(self, ev):
        self.pressed.emit()
        self._drag = (ev.position(), self.xy)
        if not (ev.modifiers() & Qt.ShiftModifier):
            self._set_from(ev.position())

    def mouseMoveEvent(self, ev):
        if self._drag is None:
            return
        if ev.modifiers() & Qt.ShiftModifier:  # hassas: başlangıçtan göreli, 1/5 hız
            p0, xy0 = self._drag
            c, r = self._geom()
            d = (ev.position() - p0) / (r * 5)
            self._apply(xy0[0] + d.x(), xy0[1] - d.y())
        else:
            self._set_from(ev.position())

    def mouseReleaseEvent(self, ev):
        self._drag = None

    def mouseDoubleClickEvent(self, ev):
        self.pressed.emit()
        self._apply(0.0, 0.0)

    def _set_from(self, pos):
        c, r = self._geom()
        self._apply((pos.x() - c.x()) / r, -(pos.y() - c.y()) / r)

    def _apply(self, x, y):
        d = math.hypot(x, y)
        if d > 1:
            x, y = x / d, y / d
        if d < 0.03:  # merkeze yapış
            x = y = 0.0
        self.xy = (round(x, 4), round(y, 4))
        self.update()
        self.changed.emit(*self.xy)


# ------------------------------------------------------------ eğri düzenleyici
class CurveEditor(QWidget):
    """Tıkla: nokta ekle · sürükle: taşı · çift tık / sağ tık: noktayı sil."""

    changed = Signal(list)
    pressed = Signal()
    COLORS = {"curve_master": "#e8e8e8", "curve_r": "#ff5a5a", "curve_g": "#5ad65a",
              "curve_b": "#5a8cff"}

    def __init__(self):
        super().__init__()
        self.setMinimumSize(160, 160)
        self.channel = "curve_master"
        self.points = [list(p) for p in gr.IDENTITY_CURVE]
        self.others: dict = {}
        self._drag = None

    def heightForWidth(self, w):
        return w

    def set_points(self, channel, points, others=None):
        self.channel = channel
        self.points = [list(map(float, p)) for p in (points or gr.IDENTITY_CURVE)]
        self.others = others or {}
        self.update()

    def _rect(self):
        s = min(self.width(), self.height()) - 12
        return QRectF((self.width() - s) / 2, 6, s, s)

    def _to_px(self, pt):
        r = self._rect()
        return QPointF(r.left() + pt[0] * r.width(), r.bottom() - pt[1] * r.height())

    def _from_px(self, pos):
        r = self._rect()
        return [min(max((pos.x() - r.left()) / r.width(), 0.0), 1.0),
                min(max((r.bottom() - pos.y()) / r.height(), 0.0), 1.0)]

    def _hit(self, pos):
        for i, pt in enumerate(self.points):
            q = self._to_px(pt)
            if abs(q.x() - pos.x()) < 8 and abs(q.y() - pos.y()) < 8:
                return i
        return None

    def _path(self, pts):
        ys = gr.monotone_curve(pts, 128)
        r = self._rect()
        path = QPainterPath()
        for i, y in enumerate(ys):
            q = QPointF(r.left() + i / 127 * r.width(), r.bottom() - float(y) * r.height())
            path.moveTo(q) if i == 0 else path.lineTo(q)
        return path

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self._rect()
        p.fillRect(r, QColor("#1a1c1f"))
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        for i in range(1, 4):
            x = r.left() + r.width() * i / 4
            y = r.top() + r.height() * i / 4
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.DashLine))
        p.drawLine(r.bottomLeft(), r.topRight())
        for ch, pts in self.others.items():  # diğer kanalların eğrileri soluk
            if ch != self.channel and pts and not gr.is_identity_curve(pts):
                col = QColor(self.COLORS[ch])
                col.setAlpha(70)
                p.setPen(QPen(col, 1.2))
                p.drawPath(self._path(pts))
        col = QColor(self.COLORS[self.channel])
        p.setPen(QPen(col, 2))
        p.drawPath(self._path(self.points))
        p.setBrush(QColor("#1a1c1f"))
        for pt in self.points:
            p.drawEllipse(self._to_px(pt), 4.5, 4.5)
        p.setPen(QPen(QColor("#3a3f47"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        p.end()

    def mousePressEvent(self, ev):
        pos = ev.position()
        i = self._hit(pos)
        if ev.button() == Qt.RightButton:
            if i is not None:
                self._delete(i)
            return
        self.pressed.emit()
        if i is None:
            self.points.append(self._from_px(pos))
            self.points.sort()
            i = self._hit(pos)
            self._emit()
        self._drag = i

    def mouseMoveEvent(self, ev):
        if self._drag is None:
            return
        x, y = self._from_px(ev.position())
        pts = self.points
        i = self._drag
        lo = pts[i - 1][0] + 0.01 if i > 0 else 0.0  # komşuları geçemez
        hi = pts[i + 1][0] - 0.01 if i < len(pts) - 1 else 1.0
        pts[i] = [round(min(max(x, lo), hi), 4), round(y, 4)]
        self._emit()

    def mouseReleaseEvent(self, ev):
        self._drag = None

    def mouseDoubleClickEvent(self, ev):
        i = self._hit(ev.position())
        if i is not None:
            self._delete(i)

    def _delete(self, i):
        if len(self.points) <= 2:
            return
        self.pressed.emit()
        del self.points[i]
        self._drag = None
        self._emit()

    def _emit(self):
        self.update()
        self.changed.emit([list(p) for p in self.points])


class LumaSlider(QSlider):
    """Tekerlek altındaki kompakt parlaklık kaydırıcısı (-100..100, çift tık sıfırlar)."""

    def __init__(self, on_change):
        super().__init__(Qt.Horizontal)
        self.setRange(-100, 100)
        self.on_change = on_change
        self._lock = False
        self.valueChanged.connect(self._changed)
        self.setToolTip("0")

    def _changed(self, v):
        self.setToolTip(str(v))
        if not self._lock:
            self.on_change(float(v))

    def set_value(self, v):
        self._lock = True
        self.setValue(int(round(v)))
        self.setToolTip(str(int(round(v))))
        self._lock = False

    def mouseDoubleClickEvent(self, ev):
        self.sliderPressed.emit()
        self.setValue(0)


# ------------------------------------------------------------ panel
class ColorPanel(QScrollArea):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.clip = None
        self._loading = False
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 8, 8, 8)
        self.title = QLabel(_("No clip selected"))
        self.title.setTextFormat(Qt.PlainText)
        self.title.setStyleSheet("font-weight:bold;")
        lay.addWidget(self.title)
        top = QHBoxLayout()
        self.enabled = QCheckBox(_("Enabled"))
        self.enabled.setChecked(True)
        self.enabled.toggled.connect(self._toggle)
        reset = QPushButton(_("Reset All"))
        reset.clicked.connect(self.reset_all)
        top.addWidget(self.enabled)
        top.addStretch(1)
        top.addWidget(reset)
        lay.addLayout(top)

        # Temel
        basic = QGroupBox(_("Basic Correction"))
        bf = QFormLayout(basic)
        self.sliders = {}
        for key, label, lo, hi, d, step in gr.BASIC:
            dec = 2 if step < 1 else 0
            sl = SliderRow(lo, hi, step, dec, lambda v, k=key: self._set(k, v))
            sl.slider.sliderPressed.connect(lambda k=key: self._push(k))
            self.sliders[key] = sl
            bf.addRow(_(label), sl)
        auto = QPushButton(_("Auto"))
        auto.setToolTip(_("Automatic exposure, contrast and white balance from the current "
                          "frame"))
        auto.clicked.connect(self.auto_correct)
        bf.addRow(auto)
        lay.addWidget(basic)

        # Eğriler
        curves = QGroupBox(_("Curves"))
        cl = QVBoxLayout(curves)
        self.curve_tabs = QTabBar()
        for name in ("RGB", "R", "G", "B"):
            self.curve_tabs.addTab(name)
        self.curve_tabs.currentChanged.connect(self._curve_tab)
        cl.addWidget(self.curve_tabs)
        self.curve = CurveEditor()
        self.curve.setMinimumHeight(220)
        self.curve.pressed.connect(lambda: self._push("curve", force=True))
        self.curve.changed.connect(lambda pts: self._set_list(self.curve.channel, pts))
        cl.addWidget(self.curve)
        hint = QLabel(_("Click to add a point, drag to move, double-click to remove."))
        hint.setStyleSheet("color:#80868b;")
        hint.setWordWrap(True)
        cl.addWidget(hint)
        rc = QPushButton(_("Reset Curve"))
        rc.clicked.connect(self._reset_curve)
        cl.addWidget(rc)
        lay.addWidget(curves)

        # Renk tekerlekleri
        wheels = QGroupBox(_("Color Wheels"))
        wl = QHBoxLayout(wheels)
        self.wheels = {}
        self.wheel_luma = {}
        for key, label in gr.WHEELS:
            col = QVBoxLayout()
            t = QLabel(_(label))
            t.setAlignment(Qt.AlignCenter)
            col.addWidget(t)
            w = ColorWheel()
            w.pressed.connect(lambda k=key: self._push(k, force=True))
            w.changed.connect(lambda x, y, k=key: self._set_list(k, [x, y]))
            col.addWidget(w, 1)
            sl = LumaSlider(lambda v, k=key + "_luma": self._set(k, v))
            sl.sliderPressed.connect(lambda k=key + "_luma": self._push(k))
            col.addWidget(sl)
            self.wheels[key] = w
            self.wheel_luma[key + "_luma"] = sl
            wl.addLayout(col)
        lay.addWidget(wheels)
        tip = QLabel(_("Wheels: drag the dot to tint shadows, midtones or highlights. "
                       "Shift = fine, double-click = reset."))
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#80868b;")
        lay.addWidget(tip)
        lay.addStretch(1)
        self.set_clip(None)

    # ------------------------------------------------------------ bağlama
    def effect(self, create=False):
        c = self.clip
        if c is None:
            return None
        for e in c.effects:
            if e["type"] == "lumetri":
                return e
        if not create:
            return None
        e = fx.new_effect("lumetri")
        c.effects.append(e)
        return e

    def set_clip(self, clip):
        self.clip = clip if (clip is not None and clip.kind != "audio"
                             and self.main.project.track_is_video(clip.track)) else None
        self.setEnabled(True)
        self.widget().setEnabled(self.clip is not None)
        self.title.setText(self.clip.name if self.clip else _("Select a video clip to grade it"))
        self.refresh()

    def params_now(self) -> dict:
        p = gr.defaults()
        e = self.effect()
        if e is None:
            return p
        p.update(e["params"])
        # Animasyonlu değerler: oynatma kafasındaki değer gösterilsin
        c = self.clip
        t = min(max(self.main.timeline.playhead, c.start), c.end)
        ac = kf.animated(c, t)
        for ae in ac.effects:
            if ae.get("id") == e.get("id"):
                p.update(ae["params"])
        return p

    def refresh(self):
        self._loading = True
        p = self.params_now()
        e = self.effect()
        self.enabled.setChecked(e.get("enabled", True) if e else True)
        for k, sl in self.sliders.items():
            sl.set_value(float(p[k]))
        for k, sl in self.wheel_luma.items():
            sl.set_value(float(p[k]))
        for k, w in self.wheels.items():
            w.set_value(p[k])
        ch = gr.CURVES[self.curve_tabs.currentIndex()]
        self.curve.set_points(ch, p[ch], {k: p[k] for k in gr.CURVES})
        self._loading = False

    def sync_time(self, t):
        if self.clip is not None and self.clip.keyframes:
            self.refresh()

    # ------------------------------------------------------------ düzenleme
    def _push(self, key, force=False):
        """Bir sürükleme = tek geri alma adımı."""
        if self.clip is None:
            return
        if force:
            self.main.before_edit()
            self.main.inspector._last_edit = ((self.clip.id, "lumetri", key),
                                              __import__("time").monotonic())
        else:
            self.main.inspector._push_coalesced((self.clip.id, "lumetri", key))

    def _set(self, key, value):
        if self._loading or self.clip is None:
            return
        created = self.effect() is None
        if created:
            self.main.before_edit()
        e = self.effect(create=True)
        self.main.prop_edit(self.clip, kf.fx_prop(e, key), float(value), push=not created)
        if created:
            self.main.on_edited()

    def _set_list(self, key, value):
        if self._loading or self.clip is None:
            return
        e = self.effect(create=True)
        e["params"][key] = value
        self.main.on_edited(refresh_inspector=False)

    def _toggle(self, on):
        if self._loading:
            return
        e = self.effect()
        if e is not None:
            self.main.before_edit()
            e["enabled"] = bool(on)
            self.main.on_edited()

    def _curve_tab(self, i):
        p = self.params_now()
        ch = gr.CURVES[i]
        self.curve.set_points(ch, p[ch], {k: p[k] for k in gr.CURVES})

    def _reset_curve(self):
        e = self.effect()
        if e is None:
            return
        self.main.before_edit()
        e["params"][self.curve.channel] = [list(pt) for pt in gr.IDENTITY_CURVE]
        self.main.on_edited(refresh_inspector=False)
        self.refresh()

    def reset_all(self):
        e = self.effect()
        if e is None:
            return
        self.main.before_edit()
        e["params"] = gr.defaults()
        pre = f"fx.{kf.effect_id(e)}."
        for k in [k for k in self.clip.keyframes if k.startswith(pre)]:
            del self.clip.keyframes[k]
        self.main.on_edited()

    def auto_correct(self):
        """Mevcut kareden otomatik ton ve beyaz dengesi (Premiere "Otomatik")."""
        if self.clip is None:
            return
        arr = self.main.current_frame_array()
        if arr is None:
            return
        vals = auto_params(arr)
        self.main.before_edit()
        e = self.effect(create=True)
        e["params"].update(vals)
        self.main.on_edited()


def auto_params(arr: np.ndarray) -> dict:
    """Karenin %0.5 / %99.5 parlaklık noktalarından pozlama/kontrast/siyah/beyaz ve gri
    dünya varsayımıyla beyaz dengesi önerir."""
    rgb = arr[..., :3].reshape(-1, 3).astype(np.float32)
    if len(rgb) > 200000:
        rgb = rgb[:: len(rgb) // 200000]
    y = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    lo, mid, hi = np.percentile(y, [0.5, 50, 99.5])
    out = {}
    # Siyah ve beyaz noktası: aralığı açmak için
    out["blacks"] = float(np.clip(-(lo / 255) * 100 / 0.15, -100, 0)) if lo > 4 else 0.0
    out["whites"] = float(np.clip((1 - hi / 255) * 100 / 0.25, 0, 100)) if hi < 250 else 0.0
    # Pozlama: orta tonu ~%45'e çek (doğrusal ışıkta)
    m = max(mid / 255, 1e-3)
    out["exposure"] = float(np.clip(math.log2((0.45 ** 2.2) / (m ** 2.2)), -2, 2)) \
        if abs(m - 0.45) > 0.05 else 0.0
    # Beyaz dengesi: orta parlaklıktaki piksellerin ortalaması griye
    sel = (y > 40) & (y < 220)
    if sel.sum() > 100:
        r, g, b = rgb[sel].mean(axis=0)
        t = (b - r) / max((r + b) / 2, 1) / 0.44 * 100   # sıcaklık kazancı 0.22·t (iki yön)
        tint = (g - (r + b) / 2) / max(g, 1) / 0.26 * 100  # yeşilse + (macenta)
        out["temperature"] = float(np.clip(t, -100, 100))
        out["tint"] = float(np.clip(tint, -100, 100))
    return {k: round(v, 1) for k, v in out.items()}
