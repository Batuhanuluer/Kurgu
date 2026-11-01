"""Program monitörü ve oynatma motoru (görüntü + ses senkronu)."""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QIODevice, QObject, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget, QStyle)

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
            return
        r = self.target_rect()
        pos = ev.position()
        if r.contains(pos):
            cb, self.on_click = self.on_click, None
            self.unsetCursor()
            cb((pos.x() - r.left()) / r.width(), (pos.y() - r.top()) / r.height())

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111214"))
        r = self.target_rect()
        if self.image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawImage(r, self.image)
        else:
            p.fillRect(r, QColor("#000"))
        p.end()


class ProgramMonitor(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.view = VideoView()
        self.pool = SourcePool()
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
        controls.addWidget(self.dur)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.view, 1)
        lay.addLayout(controls)
        main.player.state_changed.connect(self._on_state)

    def _on_state(self, playing):
        icon = QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay
        self.play_btn.setIcon(self.style().standardIcon(icon))

    def reset(self):
        self.pool.close()
        self.pool = SourcePool(self.main.project.sample_rate)
        self.comp = None

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

    def refresh(self, t: float):
        proj = self.main.project
        if self.comp is None or self.comp.project is not proj:
            self.comp = Compositor(proj, self.pool)
        self.view.aspect = proj.width / proj.height
        r = self.view.target_rect()
        ratio = self.devicePixelRatioF()
        w = int(min(r.width() * ratio, PREVIEW_MAX_W, proj.width)) // 2 * 2
        w = max(w, 64)
        h = max(2, int(w * proj.height / proj.width) // 2 * 2)
        try:
            self.view.image = self.comp.compose(t, w, h)
        except Exception as e:  # önizleme hatası programı düşürmesin, ama görünür olsun
            import traceback
            from ..app import log_error
            log_error("".join(traceback.format_exception(e)))
            self.main.statusBar().showMessage(f"{_('Preview error')}: {e}", 8000)
        self.view.update()
        self.tc.setText(timecode(t, proj.fps))
        self.dur.setText(timecode(proj.duration, proj.fps))
