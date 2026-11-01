"""Program monitörü ve oynatma motoru (görüntü + ses senkronu)."""
from __future__ import annotations

import os
import time

import numpy as np
from PySide6.QtCore import QIODevice, QObject, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget, QStyle)

from .. import keyframes as kf
from ..i18n import _
from ..media import SourcePool
from ..render import Compositor
from .timeline import timecode

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
    HAVE_AUDIO = True
except Exception:  # bazı Linux kurulumlarında QtMultimedia eksik olabilir
    HAVE_AUDIO = False

PREVIEW_MAX_W = 1280

# ------------------------------------------------------------ ortak monitör parçaları
# Kaynak ve program monitörü aynı iskeleti kullanır: başlık şeridi, görüntü, zaman çubuğu,
# alt satır [zaman kodu | ortalanmış düğmeler | süre]. Böylece ikisi yan yana hizalı durur.
MONITOR_CSS = """
QFrame#MonitorFrame { background:#16181b; border:1px solid #2a2d33; border-radius:6px; }
QFrame#MonitorHeader { background:#1f2226; border-top-left-radius:6px;
                       border-top-right-radius:6px; border-bottom:1px solid #2a2d33; }
QLabel#MonitorTitle { color:#c9cdd2; font-weight:600; }
QLabel#MonitorSub { color:#80868b; }
QLabel#MonitorTC { color:#4fc3f7; font-family: monospace; font-size:15px; }
QLabel#MonitorDur { color:#9aa0a6; font-family: monospace; }
QToolButton#MonitorBtn { padding:3px; border-radius:4px; min-width:26px; min-height:24px;
                         color:#d5d9de; font-weight:bold; font-size:14px; }
QToolButton#MonitorBtn:hover { background:#2c3036; }
QToolButton#MonitorText { padding:3px 10px; border:1px solid #3a3f47; border-radius:4px;
                          min-height:22px; }
QToolButton#MonitorText:hover { background:#2c3036; }
"""
HEADER_H = 30
BAR_H = 18
ROW_H = 34


_GLYPHS = {QStyle.SP_MediaPlay: "play", QStyle.SP_MediaPause: "pause",
           QStyle.SP_MediaSeekBackward: "prev", QStyle.SP_MediaSeekForward: "next",
           QStyle.SP_MediaSkipBackward: "start", QStyle.SP_MediaSkipForward: "end"}


def glyph_icon(kind, color="#d5d9de"):
    """Koyu temada net görünen, her sistemde aynı oynatma simgeleri (vektörden çizilir)."""
    from PySide6.QtGui import QIcon, QPixmap
    kind = _GLYPHS.get(kind, kind)
    size = 40
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    c = size / 2

    def tri(x0, x1, h=11):  # x0 -> x1 yönünde üçgen
        p.drawPolygon(QPolygonF([QPointF(x0, c - h), QPointF(x0, c + h), QPointF(x1, c)]))
    if kind == "play":
        tri(13, 31, 12)
    elif kind == "pause":
        p.drawRoundedRect(QRectF(12, 9, 6, 22), 1.5, 1.5)
        p.drawRoundedRect(QRectF(22, 9, 6, 22), 1.5, 1.5)
    elif kind == "next":  # tek kare ileri: ▶|
        tri(11, 25, 9)
        p.drawRect(QRectF(26, 11, 3, 18))
    elif kind == "prev":
        tri(29, 15, 9)
        p.drawRect(QRectF(11, 11, 3, 18))
    elif kind == "end":  # sona: ▶▶|
        tri(7, 18, 8)
        tri(17, 28, 8)
        p.drawRect(QRectF(29, 12, 3, 16))
    elif kind == "start":
        tri(33, 22, 8)
        tri(23, 12, 8)
        p.drawRect(QRectF(8, 12, 3, 16))
    p.end()
    return QIcon(pm)


def monitor_button(parent, tip, slot, icon=None, text=None):
    b = QToolButton()
    if icon is not None:
        b.setIcon(glyph_icon(icon))
        from PySide6.QtCore import QSize
        b.setIconSize(QSize(18, 18))
        b.setObjectName("MonitorBtn")
    elif len(text) <= 2:
        b.setText(text)
        b.setObjectName("MonitorBtn")
    else:
        b.setText(text)
        b.setObjectName("MonitorText")
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.setFocusPolicy(Qt.NoFocus)
    b.clicked.connect(slot)
    return b


class MonitorFrame(QFrame):
    """Monitör iskeleti: başlık (sol: ad, sağ: ek araçlar), görüntü, zaman çubuğu, alt satır."""

    def __init__(self, owner, view, bar, title, left, center, right, header_right=()):
        super().__init__()
        self.setObjectName("MonitorFrame")
        self.setStyleSheet(MONITOR_CSS)
        head = QFrame()
        head.setObjectName("MonitorHeader")
        head.setFixedHeight(HEADER_H)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 0, 6, 0)
        hl.setSpacing(6)
        title.setObjectName("MonitorTitle")
        title.setTextFormat(Qt.PlainText)
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # uzun ad kırpılsın
        hl.addWidget(title, 1)
        for w in header_right:
            hl.addWidget(w)
        row = QWidget()
        row.setFixedHeight(ROW_H)
        g = QGridLayout(row)
        g.setContentsMargins(8, 0, 8, 0)
        g.setHorizontalSpacing(4)
        left.setObjectName("MonitorTC")
        right.setObjectName("MonitorDur")
        g.addWidget(left, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        mid = QHBoxLayout()
        mid.setSpacing(2)
        for w in center:
            if w is None:
                mid.addSpacing(10)
            else:
                mid.addWidget(w)
        g.addLayout(mid, 0, 1, Qt.AlignCenter)
        g.addWidget(right, 0, 2, Qt.AlignRight | Qt.AlignVCenter)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(2, 1)  # eşit yan sütunlar: düğmeler tam ortada
        bar.setFixedHeight(BAR_H)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(head)
        body = QVBoxLayout()
        body.setContentsMargins(6, 6, 6, 0)
        body.setSpacing(4)
        body.addWidget(view, 1)
        body.addWidget(bar)
        lay.addLayout(body, 1)
        lay.addWidget(row)
        outer = QVBoxLayout(owner)
        outer.setContentsMargins(3, 3, 3, 3)
        outer.addWidget(self)


class ScrubBar(QWidget):
    """İnce zaman çubuğu: oynatma kafası ve (varsa) giriş/çıkış aralığı; tıkla/sürükle ile gez."""

    def __init__(self, duration, now, seek, marks=lambda: (None, None), on_press=None):
        super().__init__()
        self.duration, self.now, self.seek, self.marks = duration, now, seek, marks
        self.on_press = on_press
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def _t(self, x):
        d = self.duration()
        return min(max((x - 6) / max(self.width() - 12, 1) * d, 0.0), d)

    def mousePressEvent(self, ev):
        if self.on_press:
            self.on_press()
        self.seek(self._t(ev.position().x()))

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.LeftButton:
            self.seek(self._t(ev.position().x()))

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        d = max(self.duration(), 1e-6)
        x_of = lambda t: 6 + (w - 12) * min(max(t, 0.0), d) / d  # noqa: E731
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2f333a"))
        p.drawRoundedRect(QRectF(6, h / 2 - 2, w - 12, 4), 2, 2)
        a, b = self.marks()
        if a is not None or b is not None:
            xa, xb = x_of(a or 0.0), x_of(b if b is not None else d)
            p.setBrush(QColor(79, 140, 255, 110))
            p.drawRect(QRectF(xa, 3, max(xb - xa, 1), h - 6))
            p.setPen(QPen(QColor("#4f8cff"), 2))
            if a is not None:
                p.drawLine(QPointF(xa, 2), QPointF(xa, h - 2))
            if b is not None:
                p.drawLine(QPointF(xb, 2), QPointF(xb, h - 2))
        x = x_of(self.now())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#4fc3f7"))
        p.drawPolygon(QPolygonF([QPointF(x - 5, 1), QPointF(x + 5, 1), QPointF(x, 8)]))
        p.setPen(QPen(QColor("#4fc3f7"), 1.5))
        p.drawLine(QPointF(x, 7), QPointF(x, h - 1))
        p.end()


class MixDevice(QIODevice):
    """QAudioSink'in çektiği (pull) ses akışı; veriyi anında mikserden üretir."""

    def __init__(self, compositor: Compositor, start: float, sample_rate: int, as_float: bool):
        super().__init__()
        self.comp = compositor
        self.pos = start
        self.sr = sample_rate
        self.as_float = as_float

    def isSequential(self):
        return True

    def bytesAvailable(self):
        return 1 << 20

    def readData(self, maxlen):
        bpf = 8 if self.as_float else 4
        frames = min(int(maxlen) // bpf, self.sr // 5)
        if frames <= 0:
            return b""
        try:
            pcm = self.comp.mix(self.pos, frames)
        except Exception:
            pcm = np.zeros((frames, 2), np.float32)
        self.pos += frames / self.sr
        if self.as_float:
            return pcm.astype(np.float32).tobytes()
        return (pcm * 32767).astype(np.int16).tobytes()

    def writeData(self, data):
        return 0


class Player(QObject):
    position = Signal(float)
    state_changed = Signal(bool)

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.playing = False
        self.rate = 1.0
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.sink = None
        self.device = None
        self._audio_pool = None
        self._t0 = 0.0
        self._clock0 = 0.0
        self.silent = False  # seslendirme kaydında zaman çizelgesi sesi kapalı oynar

    def toggle(self):
        self.pause() if self.playing else self.play()

    def play(self, rate: float = 1.0):
        proj = self.main.project
        start = self.main.timeline.playhead
        if start >= proj.duration - 1e-3 and rate > 0:
            start = 0.0
            self.main.timeline.set_playhead(0.0)
        self.stop_audio()
        self.rate = rate
        self._t0 = start
        self._clock0 = time.perf_counter()
        if HAVE_AUDIO and rate == 1.0 and not self.silent:
            self.start_audio(start)
        self.playing = True
        self.timer.start(max(5, int(1000 / proj.fps / 2)))
        self.state_changed.emit(True)

    def pause(self):
        self.timer.stop()
        self.stop_audio()
        self.playing = False
        self.rate = 1.0
        self.state_changed.emit(False)

    def shuttle(self, direction: int):
        """J/K/L: ileri/geri hızlanarak oynatma."""
        if direction == 0:
            self.pause()
            return
        if self.playing and (self.rate > 0) == (direction > 0):
            new_rate = min(abs(self.rate) * 2, 8.0) * direction
        else:
            new_rate = float(direction)
        self.pause()
        self.play(new_rate)

    def now(self) -> float:
        if self.sink is not None and self.rate == 1.0:
            return self._t0 + self.sink.processedUSecs() / 1e6
        return self._t0 + (time.perf_counter() - self._clock0) * self.rate

    def _tick(self):
        t = self.now()
        dur = self.main.project.duration
        if t >= dur or t < 0:
            t = min(max(t, 0.0), dur)
            self.pause()
        self.position.emit(t)

    def start_audio(self, start: float):
        proj = self.main.project
        try:
            dev = QMediaDevices.defaultAudioOutput()
            if dev.isNull():
                return
            fmt = QAudioFormat()
            fmt.setSampleRate(proj.sample_rate)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.Int16)
            as_float = False
            if not dev.isFormatSupported(fmt):
                fmt.setSampleFormat(QAudioFormat.Float)
                as_float = True
                if not dev.isFormatSupported(fmt):
                    return
            self._audio_pool = SourcePool(proj.sample_rate)
            comp = Compositor(proj, self._audio_pool)
            self.device = MixDevice(comp, start, proj.sample_rate, as_float)
            self.device.open(QIODevice.ReadOnly)
            self.sink = QAudioSink(dev, fmt, self)
            self.sink.setBufferSize(int(proj.sample_rate * (8 if as_float else 4) * 0.12))
            self.sink.start(self.device)
        except Exception:
            self.stop_audio()

    def stop_audio(self):
        if self.sink is not None:
            self.sink.stop()
            self.sink.deleteLater()
            self.sink = None
        if self.device is not None:
            self.device.close()
            self.device = None
        if self._audio_pool is not None:
            self._audio_pool.close()
            self._audio_pool = None


class VideoView(QWidget):
    def __init__(self):
        super().__init__()
        self.image: QImage | None = None
        self.on_click = None  # damlalık: (nx, ny) normalize koordinat alır
        self.monitor = None  # ProgramMonitor: doğrudan düzenleme kutusu için
        self._edit = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 180)
        self.aspect = 16 / 9

    def target_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        if w / max(h, 1) > self.aspect:
            vw, vh = h * self.aspect, h
        else:
            vw, vh = w, w / self.aspect
        return QRectF((w - vw) / 2, (h - vh) / 2, vw, vh)

    def mousePressEvent(self, ev):
        if self.on_click is None:
            if self.monitor is not None:
                self._edit = self.monitor.begin_edit(ev.position())
            return
        r = self.target_rect()
        pos = ev.position()
        if r.contains(pos):
            cb, self.on_click = self.on_click, None
            self.unsetCursor()
            cb((pos.x() - r.left()) / r.width(), (pos.y() - r.top()) / r.height())

    def mouseMoveEvent(self, ev):
        if self.monitor is None or self.on_click is not None:
            return
        if self._edit is not None:
            self.monitor.drag_edit(self._edit, ev.position())
        else:
            self.setCursor(self.monitor.hover_cursor(ev.position()))

    def mouseReleaseEvent(self, ev):
        if self._edit is not None and self.monitor is not None:
            self.monitor.end_edit()
        self._edit = None

    def mouseDoubleClickEvent(self, ev):
        if self.monitor is not None and self.monitor.pen is not None:
            self.monitor.finish_pen()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111214"))
        r = self.target_rect()
        if self.image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawImage(r, self.image)
        else:
            p.fillRect(r, QColor("#000"))
        if self.monitor is not None:
            self.monitor.paint_overlay(p)
        p.end()


class ProgramMonitor(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.view = VideoView()
        self.pool = SourcePool(use_proxy=main.use_proxy)
        self.comp = None
        self.tc = QLabel("00:00:00:00")
        self.dur = QLabel("00:00:00:00")
        mb = lambda icon, tip, slot: monitor_button(self, tip, slot, icon)  # noqa: E731
        self.play_btn = mb(QStyle.SP_MediaPlay, _("Play / Pause") + " (Space)",
                           main.player.toggle)
        from PySide6.QtWidgets import QComboBox
        self.res_combo = QComboBox()
        self.res_combo.setToolTip(_("Playback resolution: lower = smoother playback with heavy effects"))
        for label, f in (("Full", 1.0), ("1/2", 0.5), ("1/4", 0.25)):
            self.res_combo.addItem(_(label), f)
        saved = float(main.settings.value("play_factor", 0.5))
        self.res_combo.setCurrentIndex(max(0, self.res_combo.findData(saved)))
        self.res_combo.currentIndexChanged.connect(self._res_changed)
        self.res_combo.setFocusPolicy(Qt.NoFocus)
        self.play_factor = self.res_combo.currentData()
        self.proxy_btn = QToolButton()
        self.proxy_btn.setText(_("Proxy"))
        self.proxy_btn.setCheckable(True)
        self.proxy_btn.setChecked(main.use_proxy)
        self.proxy_btn.setFocusPolicy(Qt.NoFocus)
        self.proxy_btn.setToolTip(_("Use proxies for preview (light copies of heavy videos). "
                                    "Export always uses the original files."))
        self.proxy_btn.toggled.connect(main.set_use_proxy)
        self.proxy_btn.setStyleSheet("QToolButton { padding:2px 8px; border-radius:4px; } "
                                     "QToolButton:checked { color:#7fd18b; font-weight:bold; "
                                     "background:#23312a; }")
        self.title = QLabel(_("Program"))
        self.bar = ScrubBar(lambda: max(main.project.duration, 1.0),
                            lambda: main.timeline.playhead,
                            lambda t: main.timeline.set_playhead(
                                round(t * main.project.fps) / main.project.fps))
        MonitorFrame(self, self.view, self.bar, self.title, self.tc, [
            mb(QStyle.SP_MediaSkipBackward, _("Go to Start") + " (Home)", main.go_start),
            mb(QStyle.SP_MediaSeekBackward, _("Previous Frame") + " (←)",
               lambda: main.step_frames(-1)),
            self.play_btn,
            mb(QStyle.SP_MediaSeekForward, _("Next Frame") + " (→)",
               lambda: main.step_frames(1)),
            mb(QStyle.SP_MediaSkipForward, _("Go to End") + " (End)", main.go_end),
        ], self.dur, header_right=[self.proxy_btn, self.res_combo])
        main.player.state_changed.connect(self._on_state)
        self.view.monitor = self
        self.sel_mask = None   # monitörde düzenlenen maske kimliği
        self.pen = None        # kalem: {"clip": klip, "points": [[u, v], ...]}
        self._cursor = None
        self.view.setToolTip(_("Drag to move, drag corners to scale"))

    def _res_changed(self, _i):
        self.play_factor = self.res_combo.currentData()
        self.main.settings.setValue("play_factor", self.play_factor)
        self.main.timeline.update()

    def _on_state(self, playing):
        self.play_btn.setIcon(glyph_icon("pause" if playing else "play"))
        if not playing:  # durunca tam çözünürlükte netleştir
            self.refresh(self.main.timeline.playhead)

    def reset(self):
        self.pool.close()
        self.pool = SourcePool(self.main.project.sample_rate, use_proxy=self.main.use_proxy)
        self.comp = None

    # ------------------------------------------------------------ doğrudan düzenleme
    HANDLE = 7

    def target_clip(self):
        """Seçili ve oynatma kafasında görünen en üstteki görsel klip."""
        m = self.main
        if m.player.playing:
            return None
        t = m.timeline.playhead
        proj = m.project
        best = None
        for c in m.selected_clips():
            if proj.track_is_video(c.track) and c.contains(t):
                if best is None or c.track > best.track:
                    best = c
        return best

    def clip_geom(self, clip):
        """Klibin monitördeki dönmüş dikdörtgeni: (merkez, köşeler, ölçek k, animasyonlu klip)."""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QFont, QFontMetricsF, QTransform
        proj = self.main.project
        t = self.main.timeline.playhead
        ac = kf.animated(clip, t)
        r = self.view.target_rect()
        k = r.width() / proj.width
        if ac.kind == "caption":  # altyazı: iz stilindeki konum (x, y)
            from ..render import caption_box
            b = caption_box(proj, ac, r.width(), r.height()).translated(r.topLeft())
            c = b.center()
            return c, [b.topLeft(), b.topRight(), b.bottomRight(), b.bottomLeft()], k, ac
        if ac.kind == "text":
            f = QFont()
            f.setPixelSize(max(1, int(ac.font_size * ac.scale * k)))
            f.setBold(True)
            fm = QFontMetricsF(f)
            lines = ac.text.split("\n") or [""]
            w = max(fm.horizontalAdvance(x) for x in lines) + 8
            h = fm.height() * len(lines) + 4
        else:
            mw, mh = proj.visual_size(ac)  # medya ya da iç içe sekans
            if not mw or not mh:
                return None
            fit = min(proj.width / mw, proj.height / mh)
            w, h = mw * fit * ac.scale * k, mh * fit * ac.scale * k
        c = QPointF(r.center().x() + ac.pos_x * k, r.center().y() + ac.pos_y * k)
        tr = QTransform()
        tr.translate(c.x(), c.y())
        tr.rotate(ac.rotation)
        corners = [tr.map(QPointF(x, y)) for x, y in
                   ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
        return c, corners, k, ac

    def paint_overlay(self, p):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QPen, QPolygonF
        clip = self.target_clip()
        if clip is None:
            return
        g = self.clip_geom(clip)
        if g is None:
            return
        c, corners, _k, _ac = g
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 160), 3))
        p.drawPolygon(QPolygonF(corners))
        p.setPen(QPen(QColor("#4fc3f7"), 1.5))
        p.drawPolygon(QPolygonF(corners))
        if clip.kind == "caption":
            return
        h = self.HANDLE
        p.setBrush(QColor("#ffffff"))
        for pt in corners:
            p.drawRect(QRectF(pt.x() - h / 2, pt.y() - h / 2, h, h))
        p.drawLine(QPointF(c.x() - 6, c.y()), QPointF(c.x() + 6, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 6), QPointF(c.x(), c.y() + 6))
        self.paint_masks(p, clip, g)

    # ------------------------------------------------------------ maskeler
    def mask_xform(self, g):
        """Klip karesindeki (0..1) noktayı monitör koordinatına çeviren dönüşüm."""
        from PySide6.QtGui import QTransform
        _c, corners, _k, _ac = g
        tl, tr, _br, bl = corners
        a, b = tr - tl, bl - tl
        return QTransform(a.x(), a.y(), b.x(), b.y(), tl.x(), tl.y())

    def mask_handles(self, m):
        """(u, v) tutamaçları: kutu şekillerinde 4 köşe, çokgende köşe noktaları."""
        if m.get("shape") == "polygon":
            return [tuple(pt) for pt in m.get("points") or []]
        x0, y0, x1, y1 = m.get("box") or [0.25, 0.25, 0.75, 0.75]
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    def paint_masks(self, p, clip, g):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QPen, QPolygonF
        from ..masks import outline_path
        if clip.kind not in ("media", "nest"):
            return
        xf = self.mask_xform(g)
        for m in self.shown_masks(clip):
            sel = m["id"] == self.sel_mask
            path = xf.map(outline_path(m))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 150), 3))
            p.drawPath(path)
            p.setPen(QPen(QColor("#ffcc33") if sel else QColor(255, 204, 51, 140),
                          1.5 if sel else 1, Qt.SolidLine if m.get("enabled", True)
                          else Qt.DashLine))
            p.drawPath(path)
            if sel:
                p.setBrush(QColor("#ffcc33"))
                for u, v in self.mask_handles(m):
                    q = xf.map(QPointF(u, v))
                    p.drawEllipse(q, 4, 4)
        if self.pen is not None and self.pen["clip"] is clip:
            pts = [xf.map(QPointF(u, v)) for u, v in self.pen["points"]]
            p.setPen(QPen(QColor("#ffcc33"), 1.5, Qt.DashLine))
            if pts:
                tail = pts + ([self._cursor] if self._cursor is not None else [])
                p.drawPolyline(QPolygonF(tail))
            p.setBrush(QColor("#ffcc33"))
            for i, q in enumerate(pts):
                p.drawRect(QRectF(q.x() - 4, q.y() - 4, 8, 8) if i == 0 else
                           QRectF(q.x() - 3, q.y() - 3, 6, 6))

    def shown_masks(self, clip):
        """Oynatma kafasındaki maskeler (maske yolu anahtarlıysa o anki şekil)."""
        if not clip.keyframes:
            return clip.masks
        t = min(max(self.main.timeline.playhead, clip.start), clip.end)
        return kf.animated(clip, t).masks

    def _mask_hit(self, pos, clip, g):
        from PySide6.QtCore import QPointF
        from ..masks import outline_path
        if clip.kind not in ("media", "nest") or not clip.masks:
            return None
        xf = self.mask_xform(g)
        order = sorted(self.shown_masks(clip), key=lambda m: m["id"] != self.sel_mask)  # seçili önce
        for m in order:
            for i, (u, v) in enumerate(self.mask_handles(m)):
                q = xf.map(QPointF(u, v))
                if abs(q.x() - pos.x()) <= 7 and abs(q.y() - pos.y()) <= 7:
                    return ("mask_pt", m, i)
        for m in order:
            if m["id"] == self.sel_mask and xf.map(outline_path(m)).contains(pos):
                return ("mask_move", m, None)
        return None

    def start_pen(self, clip):
        if not self.main._playhead_in(clip):
            self.main.statusBar().showMessage(
                _("Move the playhead over the clip to draw a mask"), 4000)
            return
        self.pen = {"clip": clip, "points": []}
        self.view.setCursor(Qt.CrossCursor)
        self.main.statusBar().showMessage(_("Pen: click to add points, click the first point "
                                            "or double-click to close, Esc to cancel"))
        self.view.update()

    def finish_pen(self):
        pen, self.pen = self.pen, None
        self.view.unsetCursor()
        self.main.statusBar().clearMessage()
        if pen and len(pen["points"]) >= 3:
            self.main.inspector.mask_panel.add_polygon(pen["clip"], pen["points"])
        self.view.update()

    def cancel_pen(self):
        if self.pen is not None:
            self.pen = None
            self.view.unsetCursor()
            self.main.statusBar().clearMessage()
            self.view.update()

    def pick_clip(self, pos):
        """Monitörde tıklanan noktadaki en üstteki görünür klip (Premiere gibi: monitörde
        tıklayınca seçilir, sonra sürüklenir)."""
        from PySide6.QtGui import QPolygonF
        m = self.main
        if m.player.playing:
            return None
        proj, t = m.project, m.timeline.playhead
        vis = [c for c in proj.clips if proj.track_is_video(c.track) and c.contains(t)
               and not proj.tracks[c.track].muted and c.kind in ("media", "text", "caption",
                                                                  "nest")]
        # Altyazı izi en üstte çizilir; sonra büyük iz numarası üstte
        vis.sort(key=lambda c: (c.kind == "caption", c.track), reverse=True)
        for c in vis:
            g = self.clip_geom(c)
            if g is not None and QPolygonF(g[1]).containsPoint(pos, Qt.OddEvenFill):
                return c
        return None

    def _hit(self, pos):
        from PySide6.QtGui import QPolygonF
        clip = self.target_clip()
        if clip is None:
            return None, None, None
        g = self.clip_geom(clip)
        if g is None:
            return None, None, None
        c, corners, k, ac = g
        if clip.kind == "caption":
            if QPolygonF(corners).containsPoint(pos, Qt.OddEvenFill):
                return clip, "caption", g
            return None, None, None
        for pt in corners:
            if abs(pt.x() - pos.x()) <= self.HANDLE + 2 and abs(pt.y() - pos.y()) <= self.HANDLE + 2:
                return clip, "scale", g
        if QPolygonF(corners).containsPoint(pos, Qt.OddEvenFill):
            return clip, "move", g
        return None, None, None

    def hover_cursor(self, pos):
        self._cursor = pos
        if self.pen is not None:
            self.view.update()
            return Qt.CrossCursor
        clip = self.target_clip()
        g = self.clip_geom(clip) if clip is not None else None
        mh = self._mask_hit(pos, clip, g) if g is not None else None
        if mh is not None:
            return Qt.PointingHandCursor if mh[0] == "mask_pt" else Qt.SizeAllCursor
        _c, mode, _g = self._hit(pos)
        if mode is None and self.pick_clip(pos) is not None:
            return Qt.PointingHandCursor  # tıklayınca seçilir
        return {"scale": Qt.SizeFDiagCursor, "move": Qt.SizeAllCursor,
                "caption": Qt.SizeAllCursor}.get(mode, Qt.ArrowCursor)

    def begin_edit(self, pos):
        import copy
        import math
        if self.pen is not None:  # kalem: nokta ekle / kapat
            pen = self.pen
            g = self.clip_geom(pen["clip"])
            if g is None:
                return None
            xf = self.mask_xform(g)
            if len(pen["points"]) >= 3:
                from PySide6.QtCore import QPointF
                q0 = xf.map(QPointF(*pen["points"][0]))
                if abs(q0.x() - pos.x()) <= 8 and abs(q0.y() - pos.y()) <= 8:
                    self.finish_pen()
                    return None
            inv, ok = xf.inverted()
            if ok:
                q = inv.map(pos)
                pen["points"].append([round(q.x(), 4), round(q.y(), 4)])
            self.view.update()
            return None
        tclip = self.target_clip()
        tg = self.clip_geom(tclip) if tclip is not None else None
        mh = self._mask_hit(pos, tclip, tg) if tg is not None else None
        if mh is not None:
            kind, m, idx = mh
            self.sel_mask = m["id"]
            if not self.main._playhead_in(tclip):
                return None
            self.main.before_edit()
            inv, _ok = self.mask_xform(tg).inverted()
            return {"clip": tclip, "mode": kind, "mask": m, "idx": idx,
                    "orig": copy.deepcopy(m), "inv": inv, "uv0": inv.map(pos)}
        clip, mode, g = self._hit(pos)
        picked = self.pick_clip(pos) if mode != "scale" else None
        if clip is None or (picked is not None and picked is not clip):
            # Tıklanan noktadaki en üstteki klibi seç (ör. arka plan seçiliyken yazıya
            # tıklamak yazıyı seçer), hemen sürüklenebilsin
            if picked is None:
                return None
            self.main.timeline.selected = {picked.id}
            self.main._on_selection()
            self.main.timeline.update()
            self.view.update()
            clip, mode, g = self._hit(pos)
            if clip is None:
                return None
        c, _corners, k, ac = g
        if not self.main._playhead_in(clip):
            return None
        self.main.before_edit()
        self.view.setToolTip("")
        if mode == "caption":
            from ..captions import style_of
            st = style_of(self.main.project.tracks[clip.track])
            r = self.view.target_rect()
            return {"clip": clip, "mode": "caption", "p0": pos, "x": float(st["x"]),
                    "y": float(st["y"]), "rw": r.width(), "rh": r.height()}
        return {"clip": clip, "mode": mode, "p0": pos, "c": c, "k": k,
                "pos_x": ac.pos_x, "pos_y": ac.pos_y, "scale": ac.scale,
                "d0": max(math.hypot(pos.x() - c.x(), pos.y() - c.y()), 1.0)}

    def drag_edit(self, e, pos):
        import math
        clip = e["clip"]
        if e["mode"] in ("mask_pt", "mask_move"):
            m, o = e["mask"], e["orig"]  # m: o anki (anahtarlıysa canlandırılmış) maske
            q = e["inv"].map(pos)
            u, v = q.x(), q.y()
            poly = m.get("shape") == "polygon"
            if e["mode"] == "mask_move":
                du, dv = u - e["uv0"].x(), v - e["uv0"].y()
                if poly:
                    val = [[round(x + du, 4), round(y + dv, 4)] for x, y in o["points"]]
                else:
                    x0, y0, x1, y1 = o["box"]
                    val = [round(x0 + du, 4), round(y0 + dv, 4), round(x1 + du, 4),
                           round(y1 + dv, 4)]
            elif poly:
                val = [list(pt) for pt in o["points"]]
                val[e["idx"]] = [round(u, 4), round(v, 4)]
            else:
                x0, y0, x1, y1 = o["box"]
                i = e["idx"]  # 0 TL, 1 TR, 2 BR, 3 BL: karşı köşe sabit
                if i in (0, 3):
                    x0 = u
                else:
                    x1 = u
                if i in (0, 1):
                    y0 = v
                else:
                    y1 = v
                val = [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]
            m["points" if poly else "box"] = val
            # Maske yolu animasyonluysa oynatma kafasında anahtar kare oluşur/güncellenir
            self.main.prop_edit(clip, f"mask.{m['id']}.{'points' if poly else 'box'}", val,
                                push=False)
            return
        if e["mode"] == "caption":  # tüm altyazı izinin konumu (stil)
            x = e["x"] + (pos.x() - e["p0"].x()) / e["rw"]
            y = e["y"] + (pos.y() - e["p0"].y()) / e["rh"]
            if abs(x - 0.5) < 0.015:  # ortaya yapış
                x = 0.5
            st = self.main.project.tracks[clip.track].style
            st["x"] = round(min(max(x, 0.05), 0.95), 4)
            st["y"] = round(min(max(y, 0.08), 1.0), 4)
            self.main.on_edited(refresh_inspector=False)
            self.view.update()
            return
        if e["mode"] == "move":
            dx = (pos.x() - e["p0"].x()) / e["k"]
            dy = (pos.y() - e["p0"].y()) / e["k"]
            self.main.prop_edit(clip, "pos_x", round(e["pos_x"] + dx), push=False)
            self.main.prop_edit(clip, "pos_y", round(e["pos_y"] + dy), push=False)
        else:
            d = math.hypot(pos.x() - e["c"].x(), pos.y() - e["c"].y())
            sc = min(max(e["scale"] * d / e["d0"], 0.05), 5.0)
            self.main.prop_edit(clip, "scale", round(sc, 3), push=False)

    def end_edit(self):
        self.main.on_edited(refresh_inspector=False)

    def start_pick(self, callback, disable_effect=None):
        """Damlalık: monitöre tıklanan noktanın rengini döndürür.

        Renk, seçilen efekt kapalıyken alınır (ör. chroma key'in sildiği yeşil).
        """
        self.main.statusBar().showMessage(_("Eyedropper: click on the Program monitor"))
        self.view.setCursor(Qt.CrossCursor)

        def clicked(nx, ny):
            proj = self.main.project
            if self.comp is None:
                self.comp = Compositor(proj, self.pool)
            w = min(proj.width, PREVIEW_MAX_W) // 2 * 2
            h = max(2, int(w * proj.height / proj.width) // 2 * 2)
            self.comp.disabled_effect = disable_effect
            try:
                img = self.comp.compose(self.main.timeline.playhead, w, h)
            finally:
                self.comp.disabled_effect = None
            x = min(max(int(nx * w), 0), w - 1)
            y = min(max(int(ny * h), 0), h - 1)
            # 5x5 ortalama: gürültüye karşı daha kararlı
            rs = gs = bs = n = 0
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    xx, yy = min(max(x + dx, 0), w - 1), min(max(y + dy, 0), h - 1)
                    c = img.pixelColor(xx, yy)
                    rs, gs, bs, n = rs + c.red(), gs + c.green(), bs + c.blue(), n + 1
            self.main.statusBar().clearMessage()
            callback(QColor(rs // n, gs // n, bs // n).name())
        self.view.on_click = clicked

    def frame_size(self, playing: bool):
        """Duraklatılmışken ekrana göre net görüntü; oynatırken seçilen oynatma çözünürlüğü
        (sabit boyut: render önbelleği pencere boyutundan bağımsız kalsın)."""
        proj = self.main.project
        if playing:
            w = int(min(PREVIEW_MAX_W, proj.width) * self.play_factor)
        else:
            r = self.view.target_rect()
            w = int(min(r.width() * self.devicePixelRatioF(), PREVIEW_MAX_W, proj.width))
        w = max(w // 2 * 2, 64)
        return w, max(2, int(w * proj.height / proj.width) // 2 * 2)

    def refresh(self, t: float):
        proj = self.main.project
        if self.comp is None or self.comp.project is not proj:
            self.comp = Compositor(proj, self.pool)
        self.view.aspect = proj.width / proj.height
        playing = self.main.player.playing
        w, h = self.frame_size(playing)
        fi = int(round(t * proj.fps))
        tq = fi / proj.fps  # kareye hizalı zaman: önbellek anahtarı tutarlı olsun
        try:
            key = (fi, self.main.frame_sig()(tq), w, h)
            img = self.main.cache.get(key)
            if img is None:
                img = self.comp.compose(tq, w, h)
                self.main.cache.put(key, img)
            self.view.image = img
            if playing:
                self.main.prefetch(fi, w, h)
        except Exception as e:  # önizleme hatası programı düşürmesin, ama görünür olsun
            import traceback
            from ..app import log_error
            log_error("".join(traceback.format_exception(e)))
            self.main.statusBar().showMessage(f"{_('Preview error')}: {e}", 8000)
        self.view.update()
        self.tc.setText(timecode(t, proj.fps))
        self.dur.setText(timecode(proj.duration, proj.fps))
        self.bar.update()
        sc = getattr(self.main, "scopes", None)
        if sc is not None:
            sc.request_update()
        name = os.path.splitext(os.path.basename(proj.path))[0] if proj.path else _("Sequence")
        self.title.setText(_("Program") + ": " + name)
