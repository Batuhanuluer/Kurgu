"""Kaynak monitörü (Premiere "Source Monitor").

Bir medyayı zaman çizelgesine koymadan izle, I/O ile kullanılacak parçayı işaretle,
sonra Ekle (,) ya da Üzerine Yaz (.) ile oynatma kafasına koy — veya görüntüyü
zaman çizelgesine sürükle. İşaretler medya başına hatırlanır.
"""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QStyle, QToolButton,
                               QVBoxLayout, QWidget)

from ..i18n import _
from ..media import SourcePool
from ..model import Project
from ..render import Compositor
from .player import PREVIEW_MAX_W, Player, VideoView
from .timeline import MEDIA_MIME, encode_source_drag, timecode


class _Clock(QObject):
    """Player'ın beklediği zaman çizelgesi arayüzü (playhead + set_playhead)."""

    moved = Signal(float)

    def __init__(self):
        super().__init__()
        self.playhead = 0.0

    def set_playhead(self, t, emit=True):
        self.playhead = max(0.0, t)
        if emit:
            self.moved.emit(self.playhead)


class _SourceView(VideoView):
    """Görüntüden zaman çizelgesine sürükle-bırak (işaretli aralıkla)."""

    def __init__(self, mon):
        super().__init__()
        self.mon = mon
        self._press = None

    def mousePressEvent(self, ev):
        self._press = ev.position()
        self.mon.setFocus()

    def mouseMoveEvent(self, ev):
        if self._press is None or self.mon.media is None:
            return
        if (ev.position() - self._press).manhattanLength() < 8:
            return
        self._press = None
        md = QMimeData()
        a, b = self.mon.marks()
        md.setData(MEDIA_MIME, encode_source_drag(self.mon.media.id, a, b))
        drag = QDrag(self)
        drag.setMimeData(md)
        if self.image is not None:
            from PySide6.QtGui import QPixmap
            drag.setPixmap(QPixmap.fromImage(self.image).scaledToWidth(160))
        drag.exec(Qt.CopyAction)

    def mouseReleaseEvent(self, ev):
        self._press = None

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self.mon.media is None:
            p = QPainter(self)
            p.setPen(QColor("#80868b"))
            p.drawText(self.rect().adjusted(20, 20, -20, -20),
                       Qt.AlignCenter | Qt.TextWordWrap, self.mon.hint)
            p.end()


class _Bar(QWidget):
    """Kaynak zaman çubuğu: oynatma kafası ve I/O aralığı; tıkla/sürükle ile gez."""

    def __init__(self, mon):
        super().__init__()
        self.mon = mon
        self.setFixedHeight(22)
        self.setMouseTracking(True)

    def _t(self, x):
        d = self.mon.duration()
        return min(max((x - 6) / max(self.width() - 12, 1) * d, 0.0), d)

    def mousePressEvent(self, ev):
        self.mon.setFocus()
        self.mon.seek(self._t(ev.position().x()))

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.LeftButton:
            self.mon.seek(self._t(ev.position().x()))

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#1b1d21"))
        d = max(self.mon.duration(), 1e-6)
        x_of = lambda t: 6 + (w - 12) * t / d  # noqa: E731
        p.fillRect(QRectF(6, h / 2 - 2, w - 12, 4), QColor("#3a3f47"))
        a, b = self.mon.marks()
        if a is not None or b is not None:
            xa, xb = x_of(a or 0.0), x_of(b if b is not None else d)
            p.fillRect(QRectF(xa, 3, max(xb - xa, 1), h - 6), QColor(79, 140, 255, 90))
            p.setPen(QPen(QColor("#4f8cff"), 2))
            if a is not None:
                p.drawLine(QPointF(xa, 2), QPointF(xa, h - 2))
            if b is not None:
                p.drawLine(QPointF(xb, 2), QPointF(xb, h - 2))
        x = x_of(self.mon.clock.playhead)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#4fc3f7"))
        p.drawPolygon(QPolygonF([QPointF(x - 5, 2), QPointF(x + 5, 2), QPointF(x, 9)]))
        p.setPen(QPen(QColor("#4fc3f7"), 1))
        p.drawLine(QPointF(x, 8), QPointF(x, h - 2))
        p.end()


class SourceMonitor(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setFocusPolicy(Qt.StrongFocus)
        self.media = None
        self.project = Project()  # Player için: içinde yalnızca kaynak medya olan geçici proje
        self.clock = _Clock()
        self.timeline = self.clock  # Player arayüzü
        self.player = Player(self)
        self.pool = SourcePool(use_proxy=main.use_proxy)
        self.comp = None
        self.hint = _("Double-click a clip in the Project panel to open it here")
        self.view = _SourceView(self)
        self.title = QLabel(_("Source: (none)"))
        self.title.setStyleSheet("color:#9aa0a6; padding:2px 4px;")
        self.title.setTextFormat(Qt.PlainText)
        self.bar = _Bar(self)
        self.tc = QLabel("00:00:00:00")
        self.tc.setStyleSheet("color:#4fc3f7; font-family: monospace; font-size: 14px;")
        self.inout = QLabel("")
        self.inout.setStyleSheet("color:#9aa0a6; font-family: monospace;")
        st = self.style()

        def btn(text, tip, slot, icon=None):
            b = QToolButton()
            if icon is not None:
                b.setIcon(st.standardIcon(icon))
            else:
                b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            return b
        self.play_btn = btn("", _("Play / Pause") + " (Space)", self.player.toggle,
                            QStyle.SP_MediaPlay)
        info = QHBoxLayout()
        info.addWidget(self.tc)
        info.addStretch(1)
        info.addWidget(self.inout)
        row = QHBoxLayout()
        row.setSpacing(2)
        row.addStretch(1)
        row.addWidget(btn("{", _("Mark In") + " (I)", self.mark_in))
        row.addWidget(btn("}", _("Mark Out") + " (O)", self.mark_out))
        row.addSpacing(6)
        row.addWidget(btn("", _("Previous Frame"), lambda: self.step(-1),
                          QStyle.SP_MediaSeekBackward))
        row.addWidget(self.play_btn)
        row.addWidget(btn("", _("Next Frame"), lambda: self.step(1), QStyle.SP_MediaSeekForward))
        row.addSpacing(10)
        ins = btn(_("Insert"), _("Insert at playhead, push later clips") + " (,)",
                  lambda: self.edit("insert"))
        ovr = btn(_("Overwrite"), _("Overwrite at playhead") + " (.)",
                  lambda: self.edit("overwrite"))
        for b in (ins, ovr):
            b.setStyleSheet("QToolButton { padding:2px 8px; border:1px solid #3a3f47; "
                            "border-radius:4px; }")
            b.setMinimumWidth(b.sizeHint().width())
        row.addWidget(ins)
        row.addWidget(ovr)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(3)
        lay.addWidget(self.title)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.bar)
        lay.addLayout(info)
        lay.addLayout(row)
        self.player.position.connect(self._show)
        self.player.state_changed.connect(self._on_state)
        self.clock.moved.connect(self._show)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ------------------------------------------------------------ medya
    def load(self, media):
        self.player.pause()
        self.media = media
        p = Project(max(2, media.width or 1920) // 2 * 2, max(2, media.height or 1080) // 2 * 2,
                    media.fps or self.main.project.fps, self.main.project.sample_rate)
        p.media = {media.id: media}
        p.clips = []
        p.place_media(media, 0.0, p.track_indices("video")[0], p.track_indices("audio")[0])
        self.project = p
        self.pool.close()
        self.pool = SourcePool(p.sample_rate, use_proxy=self.main.use_proxy)
        self.comp = Compositor(p, self.pool)
        self.view.aspect = p.width / p.height
        self.title.setText(_("Source") + ": " + media.name)
        a, _b = self.marks()
        self.clock.set_playhead(a or 0.0)
        self.setFocus()

    def unload_if(self, media_ids):
        if self.media is not None and self.media.id in media_ids:
            self.player.pause()
            self.media = None
            self.project = Project()
            self.view.image = None
            self.title.setText(_("Source: (none)"))
            self.view.update()
            self.bar.update()

    def unload(self):
        if self.media is not None:
            self.unload_if({self.media.id})

    def duration(self) -> float:
        return self.project.duration if self.media is not None else 0.0

    def marks(self):
        if self.media is None:
            return None, None
        return self.main.source_marks.get(self.media.id, (None, None))

    def _set_marks(self, a, b):
        if a is not None and b is not None and b <= a:
            b = None
        self.main.source_marks[self.media.id] = (a, b)
        self._show(self.clock.playhead)

    def mark_in(self):
        if self.media is None:
            return
        _a, b = self.marks()
        t = round(self.clock.playhead * self.project.fps) / self.project.fps
        self._set_marks(t, b if (b is None or b > t) else None)

    def mark_out(self):
        if self.media is None:
            return
        a, _b = self.marks()
        t = round(self.clock.playhead * self.project.fps) / self.project.fps
        t = max(t, 1.0 / self.project.fps)
        self._set_marks(a if (a is None or a < t) else None, t)

    def clear_marks(self):
        if self.media is not None:
            self._set_marks(None, None)

    def seek(self, t):
        if self.player.playing:
            self.player.pause()
        self.clock.set_playhead(min(max(t, 0.0), self.duration()))

    def step(self, n):
        if self.media is not None:
            self.seek(self.clock.playhead + n / self.project.fps)

    def edit(self, mode):
        if self.media is None:
            self.main.statusBar().showMessage(self.hint, 4000)
            return
        a, b = self.marks()
        self.main.source_edit(self.media, a, b, mode)

    # ------------------------------------------------------------ çizim
    def _on_state(self, playing):
        icon = QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay
        self.play_btn.setIcon(self.style().standardIcon(icon))

    def _show(self, t):
        if self.media is None or self.comp is None:
            return
        r = self.view.target_rect()
        w = int(min(r.width() * self.devicePixelRatioF(), PREVIEW_MAX_W, self.project.width))
        w = max(w // 2 * 2, 64)
        h = max(2, int(w * self.project.height / self.project.width) // 2 * 2)
        fi = round(t * self.project.fps)
        try:
            self.view.image = self.comp.compose(fi / self.project.fps, w, h)
        except Exception as e:
            self.main.statusBar().showMessage(f"{_('Preview error')}: {e}", 6000)
        self.view.update()
        self.bar.update()
        fps = self.project.fps
        self.tc.setText(timecode(t, fps))
        a, b = self.marks()
        if a is not None or b is not None:
            aa, bb = a or 0.0, b if b is not None else self.duration()
            self.inout.setText(f"I {timecode(aa, fps)}  O {timecode(bb, fps)}  "
                               f"Δ {timecode(bb - aa, fps)}")
        else:
            self.inout.setText(timecode(self.duration(), fps))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.media is not None:
            self._show(self.clock.playhead)
