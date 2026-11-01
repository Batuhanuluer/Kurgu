"""Kaynak monitörü (Premiere "Source Monitor").

Bir medyayı zaman çizelgesine koymadan izle, I/O ile kullanılacak parçayı işaretle,
sonra Ekle (,) ya da Üzerine Yaz (.) ile oynatma kafasına koy — veya görüntüyü
zaman çizelgesine sürükle. İşaretler medya başına hatırlanır.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QMimeData, QObject, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy, QStyle, QWidget

from ..i18n import _
from ..media import SourcePool
from ..model import Project
from ..render import Compositor
from .player import (PREVIEW_MAX_W, MonitorFrame, Player, ScrubBar, VideoView,
                     glyph_icon, monitor_button)
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
        if self.mon.mc_mode:  # çoklu kamera ızgarası: tıklanan kameraya kes
            i = self.mon.mc_cell_at(ev.position())
            if i is not None:
                self.mon.main.multicam_key(i)
            return
        self._press = ev.position()
        self.mon.setFocus()

    def mouseMoveEvent(self, ev):
        if self._press is None or self.mon.media is None or self.mon.mc_mode:
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
        if self.mon.media is None and not self.mon.mc_mode:
            p = QPainter(self)
            p.setPen(QColor("#80868b"))
            p.drawText(self.rect().adjusted(20, 20, -20, -20),
                       Qt.AlignCenter | Qt.TextWordWrap, self.mon.hint)
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
        self.bar = ScrubBar(self.duration, lambda: self.clock.playhead, self.seek, self.marks,
                            on_press=self.setFocus)
        self.tc = QLabel("00:00:00:00")
        self.inout = QLabel("00:00:00:00")
        mb = lambda tip, slot, icon=None, text=None: monitor_button(  # noqa: E731
            self, tip, slot, icon, text)
        self.play_btn = mb(_("Play / Pause") + " (Space)", self.player.toggle,
                           QStyle.SP_MediaPlay)
        MonitorFrame(self, self.view, self.bar, self.title, self.tc, [
            mb(_("Mark In") + " (I)", self.mark_in, text="{"),
            mb(_("Mark Out") + " (O)", self.mark_out, text="}"),
            None,
            mb(_("Previous Frame"), lambda: self.step(-1), QStyle.SP_MediaSeekBackward),
            self.play_btn,
            mb(_("Next Frame"), lambda: self.step(1), QStyle.SP_MediaSeekForward),
            None,
            mb(_("Insert at playhead, push later clips") + " (,)",
               lambda: self.edit("insert"), text=_("Insert")),
            mb(_("Overwrite at playhead") + " (.)",
               lambda: self.edit("overwrite"), text=_("Overwrite")),
        ], self.inout)
        self.player.position.connect(self._show)
        self.player.state_changed.connect(self._on_state)
        self.clock.moved.connect(self._show)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Çoklu kamera görünümü (Premiere'deki multicam monitörü)
        self.mc_mode = False
        self.mc_pool = None
        self.mc_cells: list = []
        self._mc_flash = None
        from PySide6.QtCore import QTimer
        self._mc_flash_timer = QTimer(self)
        self._mc_flash_timer.setSingleShot(True)
        self._mc_flash_timer.timeout.connect(self._mc_unflash)
        main.player.position.connect(self._mc_follow)
        main.timeline.playhead_changed.connect(self._mc_follow)

    # ------------------------------------------------------------ çoklu kamera
    def set_multicam_mode(self, on: bool):
        self.mc_mode = on
        self.player.pause()
        if on:
            if self.mc_pool is None:
                self.mc_pool = SourcePool(use_proxy=self.main.use_proxy)
            self.mc_render(self.main.timeline.playhead)
        else:
            if self.mc_pool is not None:
                self.mc_pool.close()
                self.mc_pool = None
            self.mc_cells = []
            if self.media is not None:
                self.load(self.media)
            else:
                self.view.image = None
                self.title.setText(_("Source: (none)"))
                self.view.update()

    def _mc_follow(self, t):
        if self.mc_mode:
            self.mc_render(t)

    def mc_flash(self, angle):
        if self.mc_mode:
            self._mc_flash = angle
            self._mc_flash_timer.start(350)
            self.mc_render(self.main.player.now() if self.main.player.playing
                           else self.main.timeline.playhead)

    def _mc_unflash(self):
        self._mc_flash = None
        if self.mc_mode:
            self.mc_render(self.main.timeline.playhead)

    def mc_cell_at(self, pos):
        r = self.view.target_rect()
        if not r.contains(pos):
            return None
        nx, ny = (pos.x() - r.left()) / r.width(), (pos.y() - r.top()) / r.height()
        for i, (cx, cy, cw, ch) in enumerate(self.mc_cells):
            if cx <= nx < cx + cw and cy <= ny < cy + ch:
                return i
        return None

    def mc_render(self, t):
        """Oynatma kafasındaki çoklu kamera grubunun tüm kameralarını ızgarada göster."""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QFont, QImage, QPen
        import math as _m
        from ..media import display_size, oriented_array
        proj = self.main.project
        c = proj.multicam_clip_at(t)
        if c is None:
            c = next((x for x in proj.clips if x.mc and proj.track_is_video(x.track)), None)
            tau = None
        else:
            tau = c.source_time(t)
        g = proj.multicams.get(c.mc) if c is not None else None
        aspect = proj.width / proj.height
        self.view.aspect = aspect
        W = int(min(self.view.target_rect().width() * self.devicePixelRatioF(), 1280))
        W = max(W // 2 * 2, 320)
        H = max(2, int(W / aspect) // 2 * 2)
        img = QImage(W, H, QImage.Format_RGB32)
        img.fill(QColor("#08090a"))
        p = QPainter(img)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.mc_cells = []
        if g is None:
            p.setPen(QColor("#80868b"))
            p.drawText(img.rect(), Qt.AlignCenter | Qt.TextWordWrap,
                       _("No multi-camera clip on the timeline. Select cameras in the Project "
                         "panel → right-click → Create Multi-Camera Source Sequence."))
            p.end()
            self.title.setText(_("Multi-Camera"))
            self.view.image = img
            self.view.update()
            return
        self.title.setText(_("Multi-Camera") + ": " + g["name"])
        n = len(g["angles"])
        cols = max(1, _m.ceil(_m.sqrt(n)))
        rows = _m.ceil(n / cols)
        cw, ch = W / cols, H / rows
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(max(12, int(ch / 9)))
        p.setFont(f)
        for i, a in enumerate(g["angles"]):
            col, row = i % cols, i // cols
            cell = QRectF(col * cw, row * ch, cw, ch)
            self.mc_cells.append((col / cols, row / rows, 1 / cols, 1 / rows))
            m = proj.media.get(a["media_id"])
            inner = cell.adjusted(2, 2, -2, -2)
            if m is not None and tau is not None and self.mc_pool is not None:
                st = tau - a.get("offset", 0.0)
                src = self.mc_pool.video_source(m) if 0 <= st <= m.duration else None
                fr = src.frame_at(st) if src is not None else None
                if fr is not None:
                    rot = m.rotation if not getattr(src, "is_proxy", False) else 0
                    fw, fh = display_size(fr, rot)
                    s = min(inner.width() / fw, inner.height() / fh)
                    tw, th = max(2, int(fw * s) // 2 * 2), max(2, int(fh * s) // 2 * 2)
                    arr = np.ascontiguousarray(oriented_array(fr, tw, th, "rgb24", rot))
                    q = QImage(arr.data, tw, th, arr.strides[0], QImage.Format_RGB888)
                    p.drawImage(QRectF(inner.center().x() - tw / 2,
                                       inner.center().y() - th / 2, tw, th), q)
            active = c.mc_angle == i
            if self._mc_flash == i:
                p.setPen(QPen(QColor("#ff3b3b"), 5))
            elif active:
                p.setPen(QPen(QColor("#ffcc33"), 4))
            else:
                p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(cell.adjusted(2, 2, -2, -2))
            p.setPen(QColor("#ffcc33") if active else QColor("#e8eaed"))
            label = f"{i + 1}  " + (m.name if m else "?")
            p.drawText(inner.adjusted(8, 4, -8, -4), Qt.AlignLeft | Qt.AlignTop, label)
        p.end()
        self.view.image = img
        self.view.update()

    # ------------------------------------------------------------ medya
    def load(self, media):
        if self.mc_mode:
            self.mc_mode = False
            if hasattr(self.main, "mc_view_action"):
                self.main.mc_view_action.setChecked(False)
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
            self.tc.setText("00:00:00:00")
            self.inout.setText("00:00:00:00")
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
        self.play_btn.setIcon(glyph_icon("pause" if playing else "play"))

    def _show(self, t):
        if self.media is None or self.comp is None or self.mc_mode:
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
            self.inout.setText("{ } " + timecode(bb - aa, fps))
            self.inout.setToolTip(f"{_('In')} {timecode(aa, fps)}   {_('Out')} {timecode(bb, fps)}")
        else:
            self.inout.setText(timecode(self.duration(), fps))
            self.inout.setToolTip("")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.mc_mode:
            self.mc_render(self.main.timeline.playhead)
        elif self.media is not None:
            self._show(self.clock.playhead)
