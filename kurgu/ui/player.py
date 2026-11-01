"""Program monitörü ve oynatma motoru (görüntü + ses senkronu)."""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QIODevice, QObject, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget, QStyle)

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
        if HAVE_AUDIO and rate == 1.0:
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
        self.tc.setStyleSheet("color:#4fc3f7; font-family: monospace; font-size: 15px;")
        self.dur = QLabel("00:00:00:00")
        self.dur.setStyleSheet("color:#9aa0a6; font-family: monospace;")
        st = self.style()

        def btn(icon, tip, slot):
            b = QToolButton()
            b.setIcon(st.standardIcon(icon))
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            return b
        self.play_btn = btn(QStyle.SP_MediaPlay, _("Play / Pause") + " (Space)",
                            main.player.toggle)
        controls = QHBoxLayout()
        controls.addWidget(self.tc)
        controls.addStretch(1)
        controls.addWidget(btn(QStyle.SP_MediaSkipBackward, _("Go to Start") + " (Home)",
                               main.go_start))
        controls.addWidget(btn(QStyle.SP_MediaSeekBackward, _("Previous Frame") + " (←)",
                               lambda: main.step_frames(-1)))
        controls.addWidget(self.play_btn)
        controls.addWidget(btn(QStyle.SP_MediaSeekForward, _("Next Frame") + " (→)",
                               lambda: main.step_frames(1)))
        controls.addWidget(btn(QStyle.SP_MediaSkipForward, _("Go to End") + " (End)",
                               main.go_end))
        controls.addStretch(1)
        from PySide6.QtWidgets import QComboBox
        self.res_combo = QComboBox()
        self.res_combo.setToolTip(_("Playback resolution: lower = smoother playback with heavy effects"))
        for label, f in (("Full", 1.0), ("1/2", 0.5), ("1/4", 0.25)):
            self.res_combo.addItem(_("Playback") + ": " + _(label), f)
        saved = float(main.settings.value("play_factor", 0.5))
        self.res_combo.setCurrentIndex(max(0, self.res_combo.findData(saved)))
        self.res_combo.currentIndexChanged.connect(self._res_changed)
        self.play_factor = self.res_combo.currentData()
        self.proxy_btn = QToolButton()
        self.proxy_btn.setText(_("Proxy"))
        self.proxy_btn.setCheckable(True)
        self.proxy_btn.setChecked(main.use_proxy)
        self.proxy_btn.setToolTip(_("Use proxies for preview (light copies of heavy videos). "
                                    "Export always uses the original files."))
        self.proxy_btn.toggled.connect(main.set_use_proxy)
        self.proxy_btn.setStyleSheet("QToolButton:checked { color:#7fd18b; font-weight:bold; }")
        controls.addWidget(self.proxy_btn)
        controls.addWidget(self.res_combo)
        controls.addWidget(self.dur)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.view, 1)
        lay.addLayout(controls)
        main.player.state_changed.connect(self._on_state)
        self.view.monitor = self
        self.view.setToolTip(_("Drag to move, drag corners to scale"))

    def _res_changed(self, _i):
        self.play_factor = self.res_combo.currentData()
        self.main.settings.setValue("play_factor", self.play_factor)
        self.main.timeline.update()

    def _on_state(self, playing):
        icon = QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay
        self.play_btn.setIcon(self.style().standardIcon(icon))
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
        if ac.kind == "text":
            f = QFont()
            f.setPixelSize(max(1, int(ac.font_size * ac.scale * k)))
            f.setBold(True)
            fm = QFontMetricsF(f)
            lines = ac.text.split("\n") or [""]
            w = max(fm.horizontalAdvance(x) for x in lines) + 8
            h = fm.height() * len(lines) + 4
        else:
            m = proj.media.get(ac.media_id)
            if m is None or not m.width:
                return None
            fit = min(proj.width / m.width, proj.height / m.height)
            w, h = m.width * fit * ac.scale * k, m.height * fit * ac.scale * k
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
        h = self.HANDLE
        p.setBrush(QColor("#ffffff"))
        for pt in corners:
            p.drawRect(QRectF(pt.x() - h / 2, pt.y() - h / 2, h, h))
        p.drawLine(QPointF(c.x() - 6, c.y()), QPointF(c.x() + 6, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 6), QPointF(c.x(), c.y() + 6))

    def _hit(self, pos):
        from PySide6.QtGui import QPolygonF
        clip = self.target_clip()
        if clip is None:
            return None, None, None
        g = self.clip_geom(clip)
        if g is None:
            return None, None, None
        c, corners, k, ac = g
        for pt in corners:
            if abs(pt.x() - pos.x()) <= self.HANDLE + 2 and abs(pt.y() - pos.y()) <= self.HANDLE + 2:
                return clip, "scale", g
        if QPolygonF(corners).containsPoint(pos, Qt.OddEvenFill):
            return clip, "move", g
        return None, None, None

    def hover_cursor(self, pos):
        _c, mode, _g = self._hit(pos)
        return {"scale": Qt.SizeFDiagCursor, "move": Qt.SizeAllCursor}.get(mode, Qt.ArrowCursor)

    def begin_edit(self, pos):
        import math
        clip, mode, g = self._hit(pos)
        if clip is None:
            return None
        c, _corners, k, ac = g
        if not self.main._playhead_in(clip):
            return None
        self.main.before_edit()
        self.view.setToolTip("")
        return {"clip": clip, "mode": mode, "p0": pos, "c": c, "k": k,
                "pos_x": ac.pos_x, "pos_y": ac.pos_y, "scale": ac.scale,
                "d0": max(math.hypot(pos.x() - c.x(), pos.y() - c.y()), 1.0)}

    def drag_edit(self, e, pos):
        import math
        clip = e["clip"]
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
