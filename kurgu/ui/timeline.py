"""Zaman çizelgesi: çok izli düzenleme, taşıma, kırpma, jilet, mıknatıs, sürükle-bırak."""
from __future__ import annotations

import copy
import threading

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QObject
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QPainterPath, QCursor
from PySide6.QtWidgets import (QHBoxLayout, QMenu, QScrollBar, QToolButton, QVBoxLayout,
                               QWidget)

from .. import keyframes as kf
from ..i18n import _
from ..media import waveform
from ..model import Clip, Project

MEDIA_MIME = "application/x-kurgu-media"
EFFECT_MIME = "application/x-kurgu-effect"
HEADER_W = 128
RULER_H = 30
TRACK_H = 58
GROUP_GAP = 8
EDGE_PX = 7
SNAP_PX = 9
MAGNET_PX = 18  # komşunun üzerine bu kadar binerse kenarına dayan (üzerine yazma)

COLORS = {
    "video": QColor("#3d6fb6"), "image": QColor("#2f8f86"), "audio": QColor("#3f8a4e"),
    "text": QColor("#8a55b5"), "missing": QColor("#8a3a3a"),
}


EDIT_TOOLS = ("ripple", "roll", "slip", "slide")
TOOLS = [  # (araç, kısayol, simge, ad)
    ("select", "V", "➤", "Selection Tool"),
    ("ripple", "B", "⇤", "Ripple Edit Tool"),
    ("roll", "N", "⇹", "Rolling Edit Tool"),
    ("razor", "C", "✂", "Razor Tool"),
    ("slip", "Y", "↔", "Slip Tool"),
    ("slide", "U", "⇆", "Slide Tool"),
]


def encode_source_drag(media_id: str, a: float | None, b: float | None) -> bytes:
    """Kaynak monitöründen sürükleme verisi: 'kimlik;giriş;çıkış' (boş = işaretsiz)."""
    return f"{media_id};{'' if a is None else a};{'' if b is None else b}".encode()


def decode_source_drag(data: bytes):
    """MEDIA_MIME verisi: 'kimlik' ya da 'kimlik;giriş;çıkış'."""
    parts = bytes(data).decode().split(";")
    a = float(parts[1]) if len(parts) > 1 and parts[1] else None
    b = float(parts[2]) if len(parts) > 2 and parts[2] else None
    return parts[0], a, b


def timecode(t: float, fps: float) -> str:
    t = max(t, 0.0)
    f = int(round(t * fps))
    fpsi = max(1, int(round(fps)))
    ff = f % fpsi
    s = f // fpsi
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{ff:02d}"


class _WaveSignal(QObject):
    ready = Signal()


class TimelineWidget(QWidget):
    playhead_changed = Signal(float)
    selection_changed = Signal()
    edited = Signal()  # proje değişti (yeniden çiz + önizlemeyi tazele)

    def __init__(self, main):
        super().__init__()
        self.main = main  # before_edit(), project, thumbnails sağlayan ana pencere
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(RULER_H + TRACK_H * 3)
        self.pps = 60.0  # saniye başına piksel
        self.scroll = 0.0  # soldaki görünür zaman
        self.playhead = 0.0
        self.selected: set[str] = set()
        self.tool = "select"
        self.snapping = True
        self._drag = None
        self._drop_preview = None
        self._fx_target = None
        self.snap_hit = None  # yapışılan zaman (kılavuz çizgi)
        self.razor_hover = None  # jilet önizlemesi: (zaman, iz)
        self.sel_gap = None  # seçili boşluk: (iz, başlangıç, bitiş)
        self.sel_trans = None  # seçili geçiş: (klip kimliği, "in" | "out")
        self.waves: dict[str, object] = {}
        self._wave_sig = _WaveSignal()
        self._wave_sig.ready.connect(self.update)

    @property
    def project(self) -> Project:
        return self.main.project

    # ------------------------------------------------------------ geometri
    def x_of(self, t: float) -> float:
        return HEADER_W + (t - self.scroll) * self.pps

    def t_of(self, x: float) -> float:
        return max(0.0, (x - HEADER_W) / self.pps + self.scroll)

    def rows(self) -> list[tuple[int, float]]:
        """(iz dizini, üst y) listesi, yukarıdan aşağıya."""
        out = []
        y = RULER_H
        prev_kind = None
        for ti in self.project.display_order():
            kind = self.project.tracks[ti].kind
            if prev_kind == "video" and kind == "audio":
                y += GROUP_GAP
            out.append((ti, y))
            y += TRACK_H
            prev_kind = kind
        return out

    def track_at(self, y: float) -> int | None:
        for ti, ty in self.rows():
            if ty <= y < ty + TRACK_H:
                return ti
        return None

    def row_y(self, track: int) -> float:
        for ti, ty in self.rows():
            if ti == track:
                return ty
        return -1000

    def clip_rect(self, c: Clip) -> QRectF:
        y = self.row_y(c.track)
        return QRectF(self.x_of(c.start), y + 2, max(2.0, c.duration * self.pps), TRACK_H - 4)

    def clip_at(self, pos: QPointF) -> Clip | None:
        for c in reversed(self.project.clips):
            if self.clip_rect(c).contains(pos):
                return c
        return None

    def content_height(self) -> int:
        r = self.rows()
        return int(r[-1][1] + TRACK_H + 10) if r else RULER_H

    def frame_snap(self, t: float) -> float:
        fps = self.project.fps
        return round(t * fps) / fps

    def snap(self, t: float, exclude=(), radius_px: float = SNAP_PX) -> float:
        """Yakındaki klip kenarına / işaretçiye / oynatma kafasına yapıştırır.
        Yapıştıysa self.snap_hit o zamanı tutar (sarı kılavuz çizgi çizilir)."""
        self.snap_hit = None
        if not self.snapping:
            return self.frame_snap(t)
        best, best_d = t, radius_px / self.pps
        for p in self.project.snap_points(exclude) + [self.playhead]:
            d = abs(p - t)
            if d < best_d:
                best, best_d = p, d
        if best == t:
            return self.frame_snap(t)
        self.snap_hit = best
        return best

    def magnet_move(self, clip: Clip, t: float, excl, drag: dict | None = None,
                    fine: bool = False) -> float:
        """Taşınan klibin yeni başlangıcı: kenarlara yapışma + çarpışma mıknatısı.

        1) Başı ya da sonu yakındaki bir kenara (klip, işaretçi, oynatma kafası) yapışır.
        2) Hedef izdeki bir komşunun üzerine *biraz* bindiyse (MAGNET_PX'ten az) üzerine yazmak
           yerine onun kenarına dayanır. Daha derin bırakılırsa Premiere gibi üzerine yazar.
        """
        dur = clip.duration
        if not self.snapping or fine:  # Alt/Ctrl basılı: hassas mod, mıknatıs yok
            self.snap_hit = None
            return max(self.frame_snap(t), 0.0)
        s0 = self.snap(t, excl, radius_px=SNAP_PX * 1.3)
        h0 = self.snap_hit
        s1 = self.snap(t + dur, excl, radius_px=SNAP_PX * 1.3) - dur
        h1 = self.snap_hit
        if h0 is not None and (h1 is None or abs(s0 - t) <= abs(s1 - t)):
            t, hit = s0, h0
        elif h1 is not None:
            t, hit = s1, h1
        else:
            t, hit = self.frame_snap(t), None
        limit = MAGNET_PX / self.pps
        for o in self.project.clips:
            if o.track != clip.track or o.id in excl:
                continue
            # Komşunun üzerine binme derinliği. Mıknatıs "yumuşak" çalışır: derinlik payın
            # altındaysa kenara dayanır; payı aşınca klip kenardan başlayarak kesintisiz
            # içeri girer (sıçrama olmaz, geri çekince de akıcı biçimde kenara oturur).
            if o.start < t < o.end:  # soldaki komşunun üzerine bindi
                depth = o.end - t
                t = o.end if depth <= limit else o.end - (depth - limit)
                hit = o.end if depth <= limit else None
            elif o.start < t + dur < o.end:  # sağdakinin üzerine bindi
                depth = t + dur - o.start
                t = o.start - dur if depth <= limit else o.start - dur + (depth - limit)
                hit = o.start if depth <= limit else None
            else:
                continue
            t = self.frame_snap(t) if hit is None else t
            break
        self.snap_hit = hit
        return max(t, 0.0)

    def razor_time(self, pos: QPointF, clip: Clip) -> float:
        """Jilet: kendi kenarları hariç diğer izlerdeki kenarlara yapışarak kesme zamanı."""
        own = {c.id for c in self.project.linked(clip)}
        t = self.snap(self.t_of(pos.x()), own, radius_px=SNAP_PX * 1.6)
        return min(max(t, clip.start), clip.end)

    # ------------------------------------------------------------ yakınlaştırma
    def zoom(self, factor: float, anchor_x: float | None = None):
        if anchor_x is None:
            anchor_x = self.x_of(self.playhead)
        anchor_t = self.t_of(anchor_x)
        self.pps = min(max(self.pps * factor, 2.0), 2000.0)
        self.scroll = max(0.0, anchor_t - (anchor_x - HEADER_W) / self.pps)
        self.main.timeline_panel.sync_scrollbar()
        self.update()

    def zoom_fit(self):
        dur = max(self.project.duration, 5.0)
        self.pps = max(2.0, (self.width() - HEADER_W - 30) / dur)
        self.scroll = 0.0
        self.main.timeline_panel.sync_scrollbar()
        self.update()

    def ensure_visible(self, t: float):
        x = self.x_of(t)
        if x < HEADER_W or x > self.width() - 20:
            self.scroll = max(0.0, t - (self.width() - HEADER_W) * 0.1 / self.pps)
            self.main.timeline_panel.sync_scrollbar()

    def set_playhead(self, t: float, emit=True):
        self.playhead = max(0.0, t)
        self.update()
        if emit:
            self.playhead_changed.emit(self.playhead)

    # ------------------------------------------------------------ dalga formu
    def wave_for(self, media_id: str):
        if media_id in self.waves:
            return self.waves[media_id]
        m = self.project.media.get(media_id)
        if m is None:
            return None
        self.waves[media_id] = None

        def work():
            self.waves[media_id] = waveform(m.path)
            try:
                self._wave_sig.ready.emit()
            except RuntimeError:  # pencere kapandıysa
                pass
        threading.Thread(target=work, daemon=True).start()
        return None

    # ------------------------------------------------------------ çizim
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#1b1d21"))
        proj = self.project
        # İz arka planları
        for ti, y in self.rows():
            tr = proj.tracks[ti]
            bg = QColor("#24272d") if tr.kind == "video" else QColor("#22282a")
            p.fillRect(HEADER_W, int(y), w - HEADER_W, TRACK_H - 1, bg)
        # Klipler
        p.setClipRect(HEADER_W, RULER_H, w - HEADER_W, h - RULER_H)
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() * 0.9))
        p.setFont(font)
        for c in proj.clips:
            self.paint_clip(p, c)
        for c in proj.clips:
            self.paint_transitions(p, c)
        for c in proj.clips:
            self.paint_keys(p, c)
        if self.sel_gap is not None:
            gt, g0, g1 = self.sel_gap
            gy = self.row_y(gt)
            gr = QRectF(self.x_of(g0), gy + 2, (g1 - g0) * self.pps, TRACK_H - 4)
            p.setPen(QPen(QColor("#ffcc33"), 1, Qt.DashLine))
            p.setBrush(QColor(255, 204, 51, 55))
            p.drawRect(gr)
            if gr.width() > 60:
                p.setPen(QColor("#ffe08a"))
                p.drawText(gr, Qt.AlignCenter, _("Gap") + "  ⌫")
        if self._fx_target is not None:
            p.setPen(QPen(QColor("#ffcc33"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self._fx_target)
        if self._drop_preview:
            track, t0, dur = self._drop_preview
            y = self.row_y(track)
            p.setPen(QPen(QColor("#ffcc33"), 2, Qt.DashLine))
            p.setBrush(QColor(255, 204, 51, 40))
            p.drawRect(QRectF(self.x_of(t0), y + 2, dur * self.pps, TRACK_H - 4))
        p.setClipping(False)
        # İz başlıkları
        for ti, y in self.rows():
            self.paint_header(p, ti, y)
        # Cetvel
        self.paint_ruler(p, w)
        # Sürükleme göstergesi: ne kadar kaydırıldı (hassas ayar için)
        d = self._drag
        if d and d.get("mode") == "move" and d.get("pushed"):
            mc = self.project.clip_by_id(d["clip"])
            if mc is not None:
                delta = mc.start - d["start0"]
                sign = "+" if delta >= 0 else "−"
                txt = f"{sign}{timecode(abs(delta), self.project.fps)}"
                txt += "   " + (_("fine (no magnet)") if d.get("fine") else _("Alt: fine"))
                r = self.clip_rect(mc)
                f = QFont(self.font())
                f.setBold(True)
                p.setFont(f)
                tw = p.fontMetrics().horizontalAdvance(txt) + 14
                box = QRectF(max(HEADER_W + 2, r.left()), r.top() - 22, tw, 19)
                if box.top() < RULER_H:
                    box.moveTop(r.bottom() + 3)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(20, 20, 24, 225))
                p.drawRoundedRect(box, 4, 4)
                p.setPen(QColor("#ffcc33") if not d.get("fine") else QColor("#7fd18b"))
                p.drawText(box, Qt.AlignCenter, txt)
                p.setFont(self.font())
        if d and d.get("mode") == "tool" and d.get("pushed") and d.get("info"):
            mc = self.project.clip_by_id(d["clip"])
            if mc is not None:
                label = {"ripple": _("Ripple"), "roll": _("Roll"), "slip": _("Slip"),
                         "slide": _("Slide")}.get(d["op"], "")
                txt = f"{label}  {d['info']}"
                r = self.clip_rect(mc)
                f = QFont(self.font())
                f.setBold(True)
                p.setFont(f)
                tw = p.fontMetrics().horizontalAdvance(txt) + 14
                box = QRectF(max(HEADER_W + 2, r.left()), r.top() - 22, tw, 19)
                if box.top() < RULER_H:
                    box.moveTop(r.bottom() + 3)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(20, 20, 24, 225))
                p.drawRoundedRect(box, 4, 4)
                p.setPen(QColor("#4fc3f7"))
                p.drawText(box, Qt.AlignCenter, txt)
                p.setFont(self.font())
        # Jilet önizlemesi ve yapışma kılavuzu
        if self.tool == "razor" and self.razor_hover is not None:
            rt, rtrack = self.razor_hover
            rx = self.x_of(rt)
            ry = self.row_y(rtrack)
            snapped = self.snap_hit is not None
            p.setPen(QPen(QColor("#ffcc33") if snapped else QColor(255, 255, 255, 200), 2))
            p.drawLine(int(rx), int(ry + 2), int(rx), int(ry + TRACK_H - 2))
        guide = self.snap_hit if (self._drag is not None or self.razor_hover is not None
                                  or self._drop_preview is not None) else None
        if guide is not None:
            gx = self.x_of(guide)
            p.setPen(QPen(QColor("#ffcc33"), 1, Qt.DashLine))
            p.drawLine(int(gx), RULER_H, int(gx), h)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ffcc33"))
            p.drawPolygon(QPolygonF([QPointF(gx - 5, RULER_H), QPointF(gx + 5, RULER_H),
                                     QPointF(gx, RULER_H + 6)]))
            p.drawPolygon(QPolygonF([QPointF(gx - 5, h), QPointF(gx + 5, h),
                                     QPointF(gx, h - 6)]))
        # Oynatma kafası
        x = self.x_of(self.playhead)
        if x >= HEADER_W:
            p.setPen(QPen(QColor("#ff4d4d"), 1))
            p.drawLine(int(x), RULER_H - 8, int(x), h)
            p.setBrush(QColor("#ff4d4d"))
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF([QPointF(x - 6, RULER_H - 14), QPointF(x + 6, RULER_H - 14),
                                     QPointF(x + 6, RULER_H - 6), QPointF(x, RULER_H),
                                     QPointF(x - 6, RULER_H - 6)]))
        p.end()

    def paint_ruler(self, p: QPainter, w: int):
        p.fillRect(0, 0, w, RULER_H, QColor("#2a2d33"))
        p.setPen(QColor("#9aa0a6"))
        p.drawText(QRectF(8, 0, HEADER_W - 16, RULER_H), Qt.AlignVCenter | Qt.AlignLeft,
                   timecode(self.playhead, self.project.fps))
        # Uygun aralık seç
        steps = [1 / self.project.fps, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
        step = next((s for s in steps if s * self.pps >= 70), 1200)
        t = (int(self.scroll / step)) * step
        end = self.t_of(w)
        p.setClipRect(HEADER_W, 0, w - HEADER_W, RULER_H)
        while t <= end:
            x = self.x_of(t)
            p.setPen(QColor("#6b7178"))
            p.drawLine(int(x), RULER_H - 10, int(x), RULER_H)
            mid = self.x_of(t + step / 2)
            p.drawLine(int(mid), RULER_H - 5, int(mid), RULER_H)
            p.setPen(QColor("#b8bec5"))
            label = timecode(t, self.project.fps)
            if step >= 1:
                label = label[:-3]
            p.drawText(int(x) + 3, 12, label)
            t += step
        for m in self.project.markers:
            x = self.x_of(m)
            p.setBrush(QColor("#f5c542"))
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF([QPointF(x - 5, 14), QPointF(x + 5, 14), QPointF(x, 22)]))
        p.setClipping(False)
        # Render çubuğu: kırmızı = efektli, render edilmemiş; yeşil = hazır
        try:
            runs = self.main.render_status()
        except Exception:
            runs = []
        p.setClipRect(HEADER_W, 0, w - HEADER_W, RULER_H)
        for t0, t1, done in runs:
            x0, x1 = self.x_of(t0), self.x_of(t1)
            if x1 < HEADER_W or x0 > w:
                continue
            p.fillRect(QRectF(x0, RULER_H - 5, max(1.0, x1 - x0), 4),
                       QColor("#3fbf5f") if done else QColor("#d64545"))
        p.setClipping(False)
        p.setPen(QColor("#111"))
        p.drawLine(0, RULER_H - 1, w, RULER_H - 1)

    def paint_header(self, p: QPainter, ti: int, y: float):
        tr = self.project.tracks[ti]
        r = QRectF(0, y, HEADER_W - 1, TRACK_H - 1)
        p.fillRect(r, QColor("#2b2f36"))
        p.setPen(QColor("#e8eaed"))
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(10, y, 40, TRACK_H), Qt.AlignVCenter, tr.name)
        p.setFont(self.font())
        for i, (key, on) in enumerate((("M" if tr.kind == "audio" else "H", tr.muted),
                                       ("L", tr.locked))):
            br = self.header_button_rect(y, i)
            p.setPen(QColor("#555b63"))
            p.setBrush(QColor("#d9534f") if on and i == 0 else
                       QColor("#c99a2e") if on else QColor("#3a3f47"))
            p.drawRoundedRect(br, 3, 3)
            p.setPen(QColor("#f1f3f4"))
            p.drawText(br, Qt.AlignCenter, key)

    @staticmethod
    def header_button_rect(y: float, i: int) -> QRectF:
        return QRectF(56 + i * 30, y + (TRACK_H - 22) / 2, 24, 22)

    def paint_clip(self, p: QPainter, c: Clip):
        r = self.clip_rect(c)
        if r.right() < HEADER_W or r.left() > self.width():
            return
        media = self.project.media.get(c.media_id)
        is_audio_track = not self.project.track_is_video(c.track)
        if c.kind == "text":
            col = COLORS["text"]
        elif media is None:
            col = COLORS["missing"]
        elif is_audio_track:
            col = COLORS["audio"]
        else:
            col = COLORS["image" if media.kind == "image" else "video"]
        sel = c.id in self.selected
        p.setPen(QPen(QColor("#ffffff") if sel else col.darker(160), 2 if sel else 1))
        p.setBrush(col.lighter(125) if sel else col)
        p.drawRoundedRect(r, 3, 3)
        inner = r.adjusted(2, 2, -2, -2)
        p.save()
        p.setClipRect(inner.intersected(QRectF(HEADER_W, 0, self.width(), self.height())))
        text_x = inner.left() + 4
        if not is_audio_track and media is not None:
            thumb = self.main.thumbnails.get(media.id)
            if thumb is not None and inner.width() > 30:
                th = inner.height() - 16
                tw = th * thumb.width() / max(1, thumb.height())
                p.drawImage(QRectF(inner.left(), inner.top() + 16, tw, th), thumb)
        if is_audio_track and media is not None:
            wave = self.wave_for(media.id)
            if wave is not None and len(wave):
                self.paint_wave(p, c, wave, inner)
        p.setPen(QColor("#ffffff"))
        label = c.name if c.kind != "text" else "T  " + c.text.replace("\n", " ")
        if abs(c.speed - 1) > 1e-3:
            label += f"  [{c.speed:.2f}x]"
        p.drawText(QRectF(text_x, inner.top(), inner.width() - 6, 15),
                   Qt.AlignLeft | Qt.AlignVCenter, label)
        # Geçiş üçgenleri
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 90))
        if c.fade_in > 0:
            fw = c.fade_in * self.pps
            p.drawPolygon(QPolygonF([inner.bottomLeft(), inner.topLeft(),
                                     QPointF(inner.left() + fw, inner.top())]))
        if c.fade_out > 0:
            fw = c.fade_out * self.pps
            p.drawPolygon(QPolygonF([inner.bottomRight(), inner.topRight(),
                                     QPointF(inner.right() - fw, inner.top())]))
        p.restore()
        badge = self.fx_badge_rect(c)
        if badge is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#f0b429"))
            p.drawRoundedRect(badge, 3, 3)
            p.setPen(QColor("#1b1d21"))
            p.drawText(badge, Qt.AlignCenter, "fx")
        if c.link_id:
            p.setPen(QColor(255, 255, 255, 110))
            p.drawLine(r.bottomLeft() + QPointF(4, -3), r.bottomLeft() + QPointF(14, -3))

    def trans_spans(self, c: Clip):
        """Klibin geçişleri: [(dikdörtgen, kenar, pencere başı, süre, tür)]"""
        proj = self.project
        y = self.row_y(c.track)
        out = []
        info = proj.trans_in_info(c)
        if info is not None:
            out.append(("in", info[1], info[2], c.trans_in))
        o = proj.trans_out_info(c)
        if o is not None:
            out.append(("out", o[0], o[1], c.trans_out))
        return [(QRectF(self.x_of(w0), y + 18, max(8.0, d * self.pps), TRACK_H - 24),
                 edge, w0, d, kind) for edge, w0, d, kind in out]

    def transition_at(self, pos: QPointF):
        for c in reversed(self.project.clips):
            for r, edge, w0, d, kind in self.trans_spans(c):
                if r.adjusted(-2, -2, 2, 2).contains(pos):
                    return c, edge, r
        return None

    def paint_transitions(self, p: QPainter, c: Clip):
        from ..transitions import NAMES
        for r, edge, w0, d, kind in self.trans_spans(c):
            sel = self.sel_trans == (c.id, edge)
            p.setPen(QPen(QColor("#ffcc33") if sel else QColor(255, 255, 255, 200), 2 if sel else 1))
            p.setBrush(QColor(90, 70, 20, 220) if sel else QColor(20, 20, 24, 170))
            p.drawRoundedRect(r, 3, 3)
            p.drawLine(r.bottomLeft(), r.topRight())
            if r.width() > 40:
                p.setPen(QColor("#ffffff"))
                p.drawText(r.adjusted(3, 0, -3, 0), Qt.AlignCenter, _(NAMES.get(kind, kind)))

    # ------------------------------------------------------------ anahtar kareler
    def key_points(self, c: Clip):
        """Klipteki anahtar karelerin (kaynak zamanı, ekran noktası) listesi."""
        if not c.keyframes:
            return []
        r = self.clip_rect(c)
        y = r.bottom() - 7
        out = []
        for s in kf.all_key_times(c):
            t = c.start + (s - c.in_point) / c.speed
            if c.start - 1e-6 <= t <= c.end + 1e-6:
                out.append((s, QPointF(self.x_of(t), y)))
        return out

    def key_at(self, pos: QPointF):
        for c in reversed(self.project.clips):
            for s, pt in self.key_points(c):
                if abs(pt.x() - pos.x()) <= 6 and abs(pt.y() - pos.y()) <= 7:
                    return c, s
        return None

    def paint_keys(self, p: QPainter, c: Clip):
        pts = self.key_points(c)
        if not pts:
            return
        sel = c.id in self.selected
        p.setPen(QPen(QColor("#1b1d21"), 1))
        here = c.source_time(self.playhead)
        for s, pt in pts:
            on = abs(s - here) <= kf.tol(self.project.fps)
            p.setBrush(QColor("#f0b429") if (sel or on) else QColor(235, 235, 235, 220))
            d = 5
            p.drawPolygon(QPolygonF([QPointF(pt.x(), pt.y() - d), QPointF(pt.x() + d, pt.y()),
                                     QPointF(pt.x(), pt.y() + d), QPointF(pt.x() - d, pt.y())]))

    def fx_badge_rect(self, c: Clip) -> QRectF | None:
        r = self.clip_rect(c)
        if not c.effects or r.width() <= 26:
            return None
        return QRectF(r.right() - 22, r.top() + 3, 18, 13)

    def paint_wave(self, p: QPainter, c: Clip, wave, inner: QRectF):
        bps = 50.0
        mid = inner.top() + 16 + (inner.height() - 16) / 2
        amp = (inner.height() - 18) / 2
        x0 = max(inner.left(), HEADER_W)
        x1 = min(inner.right(), self.width())
        path = QPainterPath()
        step = 2
        x = x0
        pts_top, pts_bot = [], []
        while x <= x1:
            t = self.t_of(x)
            src = c.source_time(t)
            i = int(src * bps)
            v = float(wave[i]) if 0 <= i < len(wave) else 0.0
            v = min(1.0, v * c.volume)
            pts_top.append(QPointF(x, mid - v * amp))
            pts_bot.append(QPointF(x, mid + v * amp))
            x += step
        if not pts_top:
            return
        path.moveTo(pts_top[0])
        for q in pts_top[1:]:
            path.lineTo(q)
        for q in reversed(pts_bot):
            path.lineTo(q)
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(200, 255, 210, 130))
        p.drawPath(path)

    # ------------------------------------------------------------ fare
    def mousePressEvent(self, ev):
        pos = ev.position()
        self.setFocus()
        if ev.button() == Qt.RightButton:
            self.context_menu(pos)
            return
        if ev.button() != Qt.LeftButton:
            return
        if pos.y() < RULER_H and pos.x() >= HEADER_W:
            self._drag = {"mode": "scrub"}
            self.set_playhead(self.frame_snap(self.t_of(pos.x())))
            return
        if pos.x() < HEADER_W:
            ti = self.track_at(pos.y())
            if ti is not None:
                y = self.row_y(ti)
                tr = self.project.tracks[ti]
                if self.header_button_rect(y, 0).contains(pos):
                    self.main.before_edit()
                    tr.muted = not tr.muted
                    self.edited.emit()
                elif self.header_button_rect(y, 1).contains(pos):
                    self.main.before_edit()
                    tr.locked = not tr.locked
                    self.edited.emit()
                self.update()
            return
        khit = self.key_at(pos) if self.tool == "select" else None
        if khit is not None:
            kclip, s = khit
            self.sel_trans = None
            self.sel_gap = None
            self.selected = {kclip.id}
            self.selection_changed.emit()
            self.set_playhead(kclip.start + (s - kclip.in_point) / kclip.speed)
            self._drag = {"mode": "key", "clip": kclip.id, "s": s, "pushed": False,
                          "x0": pos.x(), "y0": pos.y()}
            self.update()
            return
        hit = self.transition_at(pos) if self.tool == "select" else None
        if hit is not None:
            tclip, edge, r = hit
            self.sel_trans = (tclip.id, edge)
            self.selected.clear()
            self.selection_changed.emit()
            side = "left" if pos.x() - r.left() <= EDGE_PX else \
                "right" if r.right() - pos.x() <= EDGE_PX else None
            if side:
                self._drag = {"mode": "trans_dur", "clip": tclip.id, "edge": edge,
                              "side": side, "pushed": False, "x0": pos.x(), "y0": pos.y()}
            self.update()
            return
        if self.sel_trans is not None:
            self.sel_trans = None
            self.selection_changed.emit()
        clip = self.clip_at(pos)
        self.sel_gap = None
        if clip is None:
            if not ev.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
                self.selected.clear()
                self.selection_changed.emit()
            ti = self.track_at(pos.y())
            if ti is not None:
                gap = self.project.gap_at(ti, self.t_of(pos.x()))
                if gap is not None:
                    self.sel_gap = (ti, gap[0], gap[1])
            self._drag = {"mode": "scrub"}
            self.set_playhead(self.frame_snap(self.t_of(pos.x())))
            return
        if self.project.tracks[clip.track].locked:
            return
        if self.tool == "razor":
            t = self.razor_time(pos, clip)
            self.main.before_edit()
            ids = [c.id for c in self.project.linked(clip)]
            self.project.split_at(t, ids)
            self.edited.emit()
            self.update()
            return
        if self.tool in EDIT_TOOLS:
            self.selected = {c.id for c in self.project.linked(clip)}
            self.selection_changed.emit()
            mode = self.tool_mode(pos, clip)
            if mode is None:
                self.update()
                return
            if mode[0] == "roll" and mode[2] is None:
                mode = ("trim", mode[1], None)  # komşu yoksa normal kırpma gibi
            self._drag = {"mode": "tool", "op": mode[0], "edge": mode[1],
                          "clip": (mode[2] or clip).id, "x0": pos.x(), "y0": pos.y(),
                          "t0": self.t_of(pos.x()), "pushed": False,
                          "base": copy.deepcopy(self.project.clips),
                          "start0": clip.start, "info": ""}
            self.update()
            return
        group = {c.id for c in self.project.linked(clip)}
        if ev.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            if clip.id in self.selected:
                self.selected -= group
            else:
                self.selected |= group
        elif clip.id not in self.selected:
            self.selected = set(group)
        self.selection_changed.emit()
        badge = self.fx_badge_rect(clip)
        if badge is not None and badge.contains(pos):
            self.main.inspector.focus_effects()
            self.update()
            return
        r = self.clip_rect(clip)
        if pos.x() - r.left() <= EDGE_PX:
            mode = "trim_in"
        elif r.right() - pos.x() <= EDGE_PX:
            mode = "trim_out"
        else:
            mode = "move"
        self._drag = {"mode": mode, "clip": clip.id, "x0": pos.x(), "y0": pos.y(),
                      "start0": clip.start, "pushed": False,
                      "grab": self.t_of(pos.x()) - clip.start,
                      # Zaten seçili bir klibe düz tıklama: sürüklenmezse bırakınca
                      # seçim yalnızca bu klibe (ve hâlâ bağlıysa eşine) iner
                      "narrow": not (ev.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier))
                      and len(self.selected) > len(group)}
        self.update()

    def _begin_drag_edit(self):
        if not self._drag["pushed"]:
            self.main.before_edit()
            self._drag["pushed"] = True
            # before_edit projeyi kopyalamaz, yalnızca geçmişe yazar; klip nesnesi aynı kalır.

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        d = self._drag
        if d is None:
            self.update_cursor(pos)
            return
        if d["mode"] == "scrub":
            self.set_playhead(self.frame_snap(self.t_of(pos.x())))
            return
        clip = self.project.clip_by_id(d["clip"])
        if clip is None:
            return
        if d["mode"] == "key":
            if abs(pos.x() - d["x0"]) < 3 and not d["pushed"]:
                return
            self._begin_drag_edit()
            t = min(max(self.snap(self.t_of(pos.x()), {clip.id}), clip.start), clip.end)
            s_new = clip.source_time(t)
            kf.move_keys_at(clip, d["s"], s_new, self.project.fps)
            d["s"] = s_new
            self.set_playhead(t)
            self.edited.emit()
            return
        if d["mode"] == "trans_dur":
            self._begin_drag_edit()
            t = self.t_of(pos.x())
            proj = self.project
            if d["edge"] == "in":
                centered = proj.prev_adjacent(clip) is not None
                dur = 2 * abs(t - clip.start) if centered else t - clip.start
                kind = clip.trans_in
            else:
                dur = clip.end - t
                kind = clip.trans_out
            dur = max(1.0 / proj.fps, round(dur * proj.fps) / proj.fps)
            for c in proj.linked(clip):
                if d["edge"] == "in" and c.trans_in:
                    c.trans_in_dur = dur
                elif d["edge"] == "out" and c.trans_out:
                    c.trans_out_dur = dur
            self.edited.emit()
            self.update()
            return
        if abs(pos.x() - d["x0"]) < 3 and abs(pos.y() - d["y0"]) < 3 and not d["pushed"]:
            return
        if d["mode"] == "tool":
            self._begin_drag_edit()
            self.tool_drag(d, pos)
            return
        excl = {c.id for c in (self.drag_group(clip) if d["mode"] == "move"
                               else self.project.linked(clip))}
        if d["mode"] == "move":
            self._begin_drag_edit()
            # Önce hedef iz (çarpışma mıknatısı o izdeki komşulara bakar)
            ti = self.track_at(pos.y())
            if ti is not None and ti != clip.track and \
                    self.project.tracks[ti].kind == self.project.tracks[clip.track].kind \
                    and not self.project.tracks[ti].locked:
                d["target_track"] = ti
                clip.track = ti
            t = self.t_of(pos.x()) - d["grab"]
            fine = bool(ev.modifiers() & (Qt.AltModifier | Qt.ControlModifier))
            t = self.magnet_move(clip, t, excl, d, fine)
            d["fine"] = fine
            # Önizleme için yalnızca konumu değiştir; üzerine yazma bırakınca yapılır.
            delta = max(t, 0) - clip.start
            group = self.drag_group(clip)
            if min(c.start for c in group) + delta < 0:
                delta = -min(c.start for c in group)
            for c in group:
                c.start += delta
        elif d["mode"] in ("trim_in", "trim_out"):
            self._begin_drag_edit()
            t = self.snap(self.t_of(pos.x()), excl, radius_px=SNAP_PX * 1.7)
            self.project.trim(clip, "in" if d["mode"] == "trim_in" else "out", t)
            self.edited.emit()
        self.update()

    def leaveEvent(self, ev):
        if self.razor_hover is not None:
            self.razor_hover = None
            self.snap_hit = None
            self.update()
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev):
        d = self._drag
        self._drag = None
        self.snap_hit = None
        if d and d["mode"] == "trans_dur":
            self.selection_changed.emit()
        if d and d.get("narrow") and not d.get("pushed"):
            clip = self.project.clip_by_id(d["clip"])
            if clip is not None:
                self.selected = {c.id for c in self.project.linked(clip)}
                self.selection_changed.emit()
        if d and d["mode"] == "tool":
            d.pop("base", None)
            if d.get("pushed"):
                self.edited.emit()
                self.selection_changed.emit()
        if d and d["mode"] == "move" and d.get("pushed"):
            clip = self.project.clip_by_id(d["clip"])
            if clip is not None:
                # Son konumu kesinleştir: taşınan kliplerin altında kalanlar kırpılır.
                group = self.drag_group(clip)
                ids = {c.id for c in group}
                for c in group:
                    self.project._clear_space(c.track, c.start, c.end, ignore=ids)
            self.edited.emit()
            self.selection_changed.emit()
        self.update()

    # ------------------------------------------------------------ düzenleme araçları
    def tool_mode(self, pos: QPointF, clip: Clip):
        """Araç + tıklanan yer -> (işlem, kenar, roll için soldaki klip).
        Ripple/Roll kenarda çalışır; Slip/Slide klibin her yerinde."""
        r = self.clip_rect(clip)
        near_in = pos.x() - r.left() <= max(EDGE_PX, min(14, r.width() / 3))
        near_out = r.right() - pos.x() <= max(EDGE_PX, min(14, r.width() / 3))
        edge = "in" if near_in and (not near_out or pos.x() - r.left() < r.right() - pos.x()) \
            else "out" if near_out else None
        if self.tool == "slip":
            return ("slip", None, None)
        if self.tool == "slide":
            return ("slide", None, None)
        if edge is None:
            return None
        if self.tool == "ripple":
            return ("ripple", edge, None)
        # roll: kesmenin solundaki klip esas alınır
        if edge == "out":
            return ("roll", "out", clip if self.project.next_adjacent(clip) else None)
        left = self.project.prev_adjacent(clip)
        return ("roll", "in", left)

    def tool_drag(self, d: dict, pos: QPointF):
        proj = self.project
        proj.clips = copy.deepcopy(d["base"])  # her harekette baştan uygula: tutarlı ve geri alınabilir
        clip = proj.clip_by_id(d["clip"])
        if clip is None:
            return
        fps = proj.fps
        excl = {c.id for c in proj.linked(clip)}
        t = self.t_of(pos.x())
        op = d["op"]
        show = None
        if op == "ripple":
            t = self.snap(t, excl, radius_px=SNAP_PX * 1.7)
            moved = proj.ripple_trim(clip, d["edge"], t)
            d["info"] = f"{'+' if moved >= 0 else '−'}{timecode(abs(moved), fps)}"
            show = clip.end if d["edge"] == "out" else clip.start
        elif op == "roll":
            nxt = proj.next_adjacent(clip)
            excl |= {c.id for c in proj.linked(nxt)} if nxt is not None else set()
            t = self.snap(t, excl, radius_px=SNAP_PX * 1.7)
            cut0 = clip.end
            cut = proj.roll(clip, t)
            delta = cut - cut0
            d["info"] = f"{'+' if delta >= 0 else '−'}{timecode(abs(delta), fps)}"
            show = cut
        elif op == "trim":
            t = self.snap(t, excl, radius_px=SNAP_PX * 1.7)
            proj.trim(clip, d["edge"], t)
            show = clip.start if d["edge"] == "in" else clip.end
        elif op == "slip":
            dt = round((t - d["t0"]) * fps) / fps
            got = proj.slip(clip, -dt * clip.speed)
            d["info"] = (f"{_('In')} {timecode(clip.in_point, fps)}   "
                         f"{_('Out')} {timecode(clip.out_point, fps)}   "
                         f"{'+' if got >= 0 else '−'}{timecode(abs(got), fps)}")
            show = clip.start
        elif op == "slide":
            s0 = self.snap(d["start0"] + t - d["t0"], excl, radius_px=SNAP_PX * 1.7)
            got = proj.slide(clip, round((s0 - d["start0"]) * fps) / fps)
            d["info"] = f"{'+' if got >= 0 else '−'}{timecode(abs(got), fps)}"
            show = clip.start
        self.edited.emit()
        if show is not None and not self.main.player.playing:
            # Kesme/kaynak karesini program monitöründe göster (Premiere'deki gibi)
            self.main.monitor.refresh(min(max(show, 0.0), max(proj.duration - 1e-3, 0.0)))
        self.update()

    def drag_group(self, clip: Clip) -> list[Clip]:
        """Sürüklenince birlikte hareket edenler: bağlı klipler + seçili tüm klipler."""
        proj = self.project
        ids = {c.id for c in proj.linked(clip)}
        if clip.id in self.selected:
            for c in proj.clips:
                if c.id in self.selected and not proj.tracks[c.track].locked:
                    ids |= {x.id for x in proj.linked(c)}
        return [c for c in proj.clips if c.id in ids]

    def _key_edit(self, clip, fn):
        self.main.before_edit()
        fn()
        self.edited.emit()
        self.main.inspector.sync_time(self.playhead)

    def keyPressEvent(self, ev):
        # Menü kısayolu bir sebeple tetiklenmezse silme yine çalışsın
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.main.delete_selected(ripple=bool(ev.modifiers() & Qt.ShiftModifier))
            ev.accept()
            return
        super().keyPressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        clip = self.clip_at(ev.position())
        if clip is not None and clip.kind == "text":
            self.main.edit_title(clip)

    def update_cursor(self, pos: QPointF):
        if pos.y() < RULER_H or pos.x() < HEADER_W:
            self.setCursor(Qt.ArrowCursor)
            return
        if self.tool == "razor":
            clip = self.clip_at(pos)
            self.setCursor(Qt.SplitHCursor if clip else Qt.ArrowCursor)
            old = self.razor_hover
            if clip is not None and not self.project.tracks[clip.track].locked:
                self.razor_hover = (self.razor_time(pos, clip), clip.track)
            else:
                self.razor_hover = None
                self.snap_hit = None
            if self.razor_hover != old:
                self.update()
            return
        if self.tool in EDIT_TOOLS:
            c = self.clip_at(pos)
            mode = self.tool_mode(pos, c) if c is not None else None
            if mode is None:
                self.setCursor(Qt.ForbiddenCursor if c is not None else Qt.ArrowCursor)
            elif mode[0] == "roll":
                self.setCursor(Qt.SplitHCursor if mode[2] is not None else Qt.SizeHorCursor)
            elif mode[0] == "slide":
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.SizeHorCursor)
            return
        if self.key_at(pos) is not None:
            self.setCursor(Qt.PointingHandCursor)
            return
        hit = self.transition_at(pos)
        if hit is not None:
            r = hit[2]
            edge = pos.x() - r.left() <= EDGE_PX or r.right() - pos.x() <= EDGE_PX
            self.setCursor(Qt.SizeHorCursor if edge else Qt.PointingHandCursor)
            return
        c = self.clip_at(pos)
        if c is None:
            self.setCursor(Qt.ArrowCursor)
            return
        r = self.clip_rect(c)
        if pos.x() - r.left() <= EDGE_PX or r.right() - pos.x() <= EDGE_PX:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, ev):
        dy = ev.angleDelta().y() or ev.angleDelta().x()
        if ev.modifiers() & Qt.ControlModifier:
            self.zoom(1.15 if dy > 0 else 1 / 1.15, ev.position().x())
        else:
            self.scroll = max(0.0, self.scroll - dy / 120 * 80 / self.pps)
            self.main.timeline_panel.sync_scrollbar()
            self.update()

    def context_menu(self, pos: QPointF):
        menu = QMenu(self)
        khit = self.key_at(pos)
        if khit is not None:
            kclip, s = khit
            fps = self.project.fps
            menu.setTitle(_("Keyframe"))
            for name, it in (("Linear", "linear"), ("Ease In/Out", "ease"), ("Hold", "hold")):
                a = menu.addAction(_(name))
                a.triggered.connect(lambda _c=False, it=it: self._key_edit(
                    kclip, lambda: kf.set_interp_at(kclip, s, it, fps)))
            menu.addSeparator()
            menu.addAction(_("Delete Keyframe"), lambda: self._key_edit(
                kclip, lambda: kf.remove_keys_at(kclip, s, fps)))
            menu.exec(QCursor.pos())
            self.update()
            return
        hit = self.transition_at(pos)
        if hit is not None:
            self.sel_trans = (hit[0].id, hit[1])
            self.selected.clear()
            self.selection_changed.emit()
            menu.addAction(_("Delete Transition"), self.main.delete_selected)
            menu.exec(QCursor.pos())
            self.update()
            return
        clip = self.clip_at(pos)
        if clip is not None:
            if clip.id not in self.selected:
                self.selected = {c.id for c in self.project.linked(clip)}
                self.selection_changed.emit()
            menu.addAction(_("Split at Playhead"), self.main.split_at_playhead)
            menu.addAction(_("Duplicate"), self.main.duplicate_selected)
            vis = next((c for c in self.project.linked(clip)
                        if self.project.track_is_video(c.track) and c.kind == "media"), None)
            if vis is not None:
                menu.addSeparator()
                menu.addAction(_("Scale to Fill Frame"), lambda: self.main.scale_clip(vis, "fill"))
                menu.addAction(_("Scale to Fit Frame"), lambda: self.main.scale_clip(vis, "fit"))
                menu.addAction(_("Match Project to This Clip"),
                               lambda: self.main.match_sequence_to_clip(vis))
                menu.addSeparator()
                menu.addAction("🎨 " + _("Use as Color Reference"),
                               lambda: self.main.set_color_reference_clip(vis))
                ref = self.main.color_ref
                if ref is not None:
                    menu.addAction("🎨 " + _("Apply Color Palette") + f"  ({ref['ref_name']})",
                                   self.main.apply_color_reference)
                menu.addSeparator()
            vclips = [c for c in self.project.linked(clip) if c.effects]
            if vclips:
                fxm = menu.addMenu(_("Effects"))
                from ..effects import EFFECTS
                vc = vclips[0]
                for i, e in enumerate(vc.effects):
                    d = EFFECTS.get(e["type"])
                    name = _(d.name) if d else e["type"]
                    fxm.addAction(_("Remove") + ": " + name,
                                  lambda c=vc, i=i: self.main.remove_effect(c, i))
                fxm.addSeparator()
                fxm.addAction(_("Remove All Effects"),
                              lambda c=vc: self.main.remove_effect(c, None))
            if clip.link_id:
                menu.addAction(_("Unlink"), self.main.unlink_selected)
            elif len(self.selected) >= 2:
                menu.addAction(_("Link"), self.main.link_selected)
            menu.addSeparator()
            menu.addAction(_("Delete"), self.main.delete_selected)
            menu.addAction(_("Ripple Delete"), lambda: self.main.delete_selected(ripple=True))
        else:
            ti = self.track_at(pos.y())
            gap = self.project.gap_at(ti, self.t_of(pos.x())) if ti is not None else None
            if gap is not None:
                self.sel_gap = (ti, gap[0], gap[1])
                self.update()
                menu.addAction(_("Ripple Delete Gap"), self.main.delete_selected)
                menu.addSeparator()
            menu.addAction(_("Add Title"), self.main.add_title)
            menu.addAction(_("Add Marker"), self.main.add_marker)
        menu.exec(QCursor.pos())
        self.update()

    # ------------------------------------------------------------ sürükle-bırak
    def dragEnterEvent(self, ev):
        md = ev.mimeData()
        if md.hasFormat(MEDIA_MIME) or md.hasUrls() or md.hasFormat(EFFECT_MIME):
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        md = ev.mimeData()
        if md.hasFormat(EFFECT_MIME):
            clip = self.clip_at(ev.position())
            if clip is None:
                self._fx_target = None
                self.update()
                ev.ignore()
                return
            r = self.clip_rect(clip)
            if bytes(md.data(EFFECT_MIME)).decode().startswith("transition:"):
                # Kenar vurgusu: yakın olan kenar
                left = ev.position().x() - r.left() < r.right() - ev.position().x()
                w = min(r.width() / 2, 40)
                r = QRectF(r.left(), r.top(), w, r.height()) if left else \
                    QRectF(r.right() - w, r.top(), w, r.height())
            self._fx_target = r
            self.update()
            ev.acceptProposedAction()
            return
        if md.hasFormat(MEDIA_MIME):
            mid, a, b = decode_source_drag(md.data(MEDIA_MIME))
            m = self.project.media.get(mid)
            ti = self.track_at(ev.position().y())
            if m is not None and ti is not None:
                t0 = self.snap(self.t_of(ev.position().x()), radius_px=18)
                full = m.duration if m.kind != "image" else 5.0
                dur = (b if b is not None else full) - (a or 0.0)
                self._drop_preview = (ti, t0, dur)
                self.update()
        ev.acceptProposedAction()

    def dragLeaveEvent(self, ev):
        self._drop_preview = None
        self._fx_target = None
        self.update()

    def dropEvent(self, ev):
        self._drop_preview = None
        self._fx_target = None
        md = ev.mimeData()
        if md.hasFormat(EFFECT_MIME):
            clip = self.clip_at(ev.position())
            if clip is not None:
                r = self.clip_rect(clip)
                edge = "in" if ev.position().x() - r.left() < r.right() - ev.position().x() \
                    else "out"
                self.main.apply_effect_key(bytes(md.data(EFFECT_MIME)).decode(), clip, edge)
            ev.acceptProposedAction()
            self.update()
            return
        t0 = self.snap(self.t_of(ev.position().x()), radius_px=18)
        ti = self.track_at(ev.position().y())
        if md.hasUrls() and not md.hasFormat(MEDIA_MIME):
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            items = self.main.import_paths(paths)
            for m in items:
                self.main.place_media(m, t0, ti)
                t0 += m.duration if m.kind != "image" else 5.0
        elif md.hasFormat(MEDIA_MIME):
            mid, a, b = decode_source_drag(md.data(MEDIA_MIME))
            m = self.project.media.get(mid)
            if m is not None:
                self.main.place_media(m, t0, ti, a, b)
        ev.acceptProposedAction()
        self.update()


class TimelinePanel(QWidget):
    """Zaman çizelgesi + yatay kaydırma çubuğu."""

    def __init__(self, main):
        super().__init__()
        self.timeline = TimelineWidget(main)
        self.bar = QScrollBar(Qt.Horizontal)
        self.bar.valueChanged.connect(self._on_bar)
        tools = QVBoxLayout()
        tools.setContentsMargins(2, 2, 2, 2)
        tools.setSpacing(2)
        self.tool_buttons = {}
        for key, sc, icon, name in TOOLS:
            b = QToolButton()
            b.setText(icon)
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setFixedSize(30, 28)
            b.setToolTip(f"{_(name)} ({sc})")
            b.setStyleSheet("QToolButton { font-size: 15px; } "
                            "QToolButton:checked { background:#3d5a80; border-radius:4px; }")
            b.clicked.connect(lambda _c=False, k=key: main.set_tool(k))
            tools.addWidget(b)
            self.tool_buttons[key] = b
        tools.addStretch(1)
        self.tool_buttons["select"].setChecked(True)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.timeline, 1)
        col.addWidget(self.bar)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(tools)
        lay.addLayout(col, 1)
        self._syncing = False

    def sync_scrollbar(self):
        tl = self.timeline
        visible = max(1.0, (tl.width() - HEADER_W) / tl.pps)
        total = max(tl.project.duration + visible, tl.scroll + visible)
        self._syncing = True
        self.bar.setRange(0, int(max(0, total - visible) * 100))
        self.bar.setPageStep(int(visible * 100))
        self.bar.setValue(int(tl.scroll * 100))
        self._syncing = False

    def _on_bar(self, v):
        if not self._syncing:
            self.timeline.scroll = v / 100
            self.timeline.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.sync_scrollbar()
