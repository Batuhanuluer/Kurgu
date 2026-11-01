"""Medya kutusu (proje paneli) ve denetçi (klip özellikleri)."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QMimeData, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPen, QPixmap, QPainter
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QFileDialog, QMessageBox, QComboBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QLayout, QToolButton,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
                               QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox,
                               QVBoxLayout, QWidget, QAbstractItemView)

from .. import effects as fx
from .. import keyframes as kf
from .. import transitions
from ..i18n import _
from ..safety import plain_tip
from ..model import Clip
from .timeline import MEDIA_MIME, timecode


MEDIA_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".m2ts", ".mpg", ".mpeg",
             ".wmv", ".flv", ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".png",
             ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


def _expand_dirs(paths):
    """Bırakılan klasörlerin içindeki medya dosyalarını da al."""
    import os
    out = []
    for p in paths:
        if os.path.isdir(p):
            for base, _d, files in os.walk(p):
                out += [os.path.join(base, f) for f in sorted(files)
                        if os.path.splitext(f)[1].lower() in MEDIA_EXT]
        else:
            out.append(p)
    return out


class MediaBin(QListWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(144, 81))
        self.setGridSize(QSize(160, 120))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWordWrap(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(self._open_source)
        # Dışarıdan (Gezgin/Finder) bırakılan dosyalar: liste görünümü bu olayları kendi
        # içinde tükettiği için doğrudan görüntü alanında (viewport) yakalanır.
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)

    def rebuild(self):
        self.clear()
        for m in self.main.project.media.values():
            it = QListWidgetItem(self.icon_for(m), self.label_for(m))
            it.setData(Qt.UserRole, m.id)
            tip = m.path
            if m.kind == "video":
                tip += f"\n{m.width}×{m.height} · {m.codec} · {m.bit_depth}-bit"
                if m.proxy:
                    tip += "\n" + _("Proxy") + ": " + m.proxy
            it.setToolTip(plain_tip(tip))
            self.addItem(it)
        for gid, g in self.main.project.multicams.items():
            it = QListWidgetItem(self.mc_icon(g), f"🎥 {g['name']}\n"
                                 f"{len(g['angles'])} " + _("cameras"))
            it.setData(Qt.UserRole, "mc:" + gid)
            it.setToolTip(plain_tip(_("Multi-camera source") + ": " + g["name"]))
            self.addItem(it)

    def mc_icon(self, g) -> QIcon:
        """Çoklu kamera simgesi: ilk dört kameranın küçük resimleri 2×2."""
        pm = QPixmap(144, 81)
        pm.fill(QColor("#2b2f36"))
        p = QPainter(pm)
        for i, a in enumerate(g["angles"][:4]):
            th = self.main.thumbnails.get(a["media_id"])
            r = QRect((i % 2) * 72, (i // 2) * 40, 71, 40)
            if th is not None:
                p.drawImage(r, th.scaled(71, 40, Qt.KeepAspectRatioByExpanding,
                                         Qt.SmoothTransformation))
            p.setPen(QColor("#ffcc33"))
            p.drawText(r.adjusted(3, 2, 0, 0), Qt.AlignLeft | Qt.AlignTop, str(i + 1))
        p.setPen(QPen(QColor("#ffcc33"), 2))
        p.drawRect(pm.rect().adjusted(1, 1, -1, -1))
        p.end()
        return QIcon(pm)

    def label_for(self, m) -> str:
        if m.kind == "image":
            return m.name
        extra = ""
        prog = self.main.proxy_progress.get(m.id)
        if prog is not None:
            extra = f"  ⏳P {int(prog * 100)}%"
        elif m.proxy:
            extra = "  ✓P"
        return f"{m.name}\n{timecode(m.duration, 30)[:-3]}{extra}"

    def update_labels(self):
        for i in range(self.count()):
            it = self.item(i)
            m = self.main.project.media.get(it.data(Qt.UserRole))
            if m is not None:
                it.setText(self.label_for(m))

    def icon_for(self, m) -> QIcon:
        pm = QPixmap(144, 81)
        pm.fill(QColor("#2b2f36"))
        p = QPainter(pm)
        thumb = self.main.thumbnails.get(m.id)
        if thumb is not None:
            s = thumb.scaled(144, 81, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawImage((144 - s.width()) // 2, (81 - s.height()) // 2, s)
        else:
            p.setPen(QColor("#7fd18b"))
            f = p.font()
            f.setPixelSize(34)
            p.setFont(f)
            p.drawText(pm.rect(), Qt.AlignCenter, "♪" if m.kind == "audio" else "?")
        p.end()
        return QIcon(pm)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.viewport() and ev.type() in (QEvent.DragEnter, QEvent.DragMove,
                                                     QEvent.Drop):
            md = ev.mimeData()
            if md.hasUrls() and not md.hasFormat(MEDIA_MIME):
                ev.setDropAction(Qt.CopyAction)
                ev.accept()
                if ev.type() == QEvent.Drop:
                    paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
                    self.main.import_paths(_expand_dirs(paths))
                return True
        return super().eventFilter(obj, ev)

    def mimeData(self, items):
        md = QMimeData()
        if items:
            md.setData(MEDIA_MIME, items[0].data(Qt.UserRole).encode())
        return md

    def mimeTypes(self):
        return [MEDIA_MIME, "text/uri-list"]

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            super().dragMoveEvent(ev)

    def dropEvent(self, ev):
        if ev.mimeData().hasUrls():
            self.main.import_paths(_expand_dirs([u.toLocalFile() for u in ev.mimeData().urls()
                                                 if u.isLocalFile()]))
            ev.acceptProposedAction()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self.count() == 0:
            p = QPainter(self.viewport())
            p.setPen(QColor("#80868b"))
            p.drawText(self.viewport().rect().adjusted(12, 12, -12, -12),
                       Qt.AlignCenter | Qt.TextWordWrap, _("Drop media here or use File → Import"))
            p.end()

    def _open_source(self, it):
        key = it.data(Qt.UserRole)
        if isinstance(key, str) and key.startswith("mc:"):
            self.main.place_multicam(key[3:], self.main.timeline.playhead)
            return
        m = self.main.project.media.get(key)
        if m is not None:
            self.main.open_in_source(m)

    def _add_at_playhead(self, items):
        t = self.main.timeline.playhead
        for it in items:
            key = it.data(Qt.UserRole)
            if isinstance(key, str) and key.startswith("mc:"):
                g = self.main.project.multicams.get(key[3:])
                if g:
                    self.main.place_multicam(key[3:], t)
                    t += self.main.project.multicam_duration(g)
                continue
            m = self.main.project.media.get(key)
            if m:
                self.main.place_media(m, t, None)
                t += m.duration if m.kind != "image" else 5.0

    def _menu(self, pos):
        items = self.selectedItems()
        menu = QMenu(self)
        mcs = [i.data(Qt.UserRole)[3:] for i in items
               if str(i.data(Qt.UserRole)).startswith("mc:")]
        if mcs:
            menu.addAction(_("Add to Timeline"), lambda: self._add_at_playhead(items))
            menu.addAction(_("Rename…"), lambda: self.main.rename_multicam(mcs[0]))
            menu.addAction(_("Remove from Project"), lambda: self.main.remove_multicams(mcs))
            menu.exec(self.mapToGlobal(pos))
            return
        vids_mc = [self.main.project.media.get(i.data(Qt.UserRole)) for i in items]
        vids_mc = [v for v in vids_mc if v is not None and v.kind in ("video", "audio")]
        if items:
            menu.addAction(_("Open in Source Monitor"), lambda: self._open_source(items[0]))
            menu.addAction(_("Add to Timeline"), lambda: self._add_at_playhead(items))
            m0 = self.main.project.media.get(items[0].data(Qt.UserRole))
            vids = [self.main.project.media.get(i.data(Qt.UserRole)) for i in items]
            vids = [v for v in vids if v is not None and v.kind == "video"]
            if vids:
                menu.addAction(_("Create Proxy"), lambda: self.main.create_proxies(vids))
                if any(v.proxy for v in vids):
                    menu.addAction(_("Delete Proxy"), lambda: self.main.delete_proxies(vids))
            if m0 is not None and m0.kind != "audio":
                menu.addAction("🎨 " + _("Use as Color Reference"),
                               lambda: self.main.set_color_reference_media(m0))
            if len(vids_mc) >= 2:
                menu.addAction("🎥 " + _("Create Multi-Camera Source Sequence…"),
                               lambda: self.main.create_multicam_dialog(vids_mc))
            menu.addAction(_("Remove from Project"),
                           lambda: self.main.remove_media([i.data(Qt.UserRole) for i in items]))
            menu.addSeparator()
        menu.addAction(_("Import Media…"), self.main.import_dialog)
        menu.exec(self.mapToGlobal(pos))


class SliderRow(QWidget):
    """Kaydırıcı + sayı kutusu; değer değişince geri çağırır."""

    def __init__(self, lo, hi, step, decimals, on_change, suffix=""):
        super().__init__()
        self.scale = 10 ** decimals
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(lo * self.scale), int(hi * self.scale))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(step)
        self.spin.setSuffix(suffix)
        self.spin.setMinimumWidth(78)
        self.on_change = on_change
        self._lock = False
        self.slider.valueChanged.connect(lambda v: self._set(v / self.scale, "slider"))
        self.spin.valueChanged.connect(lambda v: self._set(v, "spin"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        # Kaydırma alanı içerikleri ezmesin: sığmazsa kaydırma çubuğu çıksın
        self.setMinimumHeight(28)
        self.spin.setMinimumHeight(24)

    def _set(self, v, src):
        if self._lock:
            return
        self._lock = True
        if src == "slider":
            self.spin.setValue(v)
        else:
            self.slider.setValue(int(round(v * self.scale)))
        self._lock = False
        self.on_change(v)

    def set_value(self, v):
        self._lock = True
        self.spin.setValue(v)
        self.slider.setValue(int(round(v * self.scale)))
        self._lock = False


class AnimRow(QWidget):
    """Animasyonlanabilen bir değer: kaydırıcı + ⏱ kronometre + ‹ ◆ › anahtar düğmeleri."""

    def __init__(self, inspector, prop_fn, slider: "SliderRow"):
        super().__init__()
        self.ins = inspector
        self.prop_fn = prop_fn  # () -> özellik adı (efekt kimliği sonradan oluşabilir)
        self.slider = slider
        self.setMinimumHeight(28)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.watch = self._btn("⏱", _("Toggle animation (stopwatch)"), checkable=True)
        self.watch.clicked.connect(lambda: self.ins.main.toggle_anim(self.ins.clip, self.prop))
        lay.addWidget(self.watch)
        lay.addWidget(slider, 1)
        self.prev = self._btn("‹", _("Previous keyframe"))
        self.prev.clicked.connect(lambda: self.ins.main.jump_key(self.ins.clip, self.prop, -1))
        self.key = self._btn("◇", _("Add / remove keyframe"))
        self.key.clicked.connect(lambda: self.ins.main.toggle_key_here(self.ins.clip, self.prop))
        self.next = self._btn("›", _("Next keyframe"))
        self.next.clicked.connect(lambda: self.ins.main.jump_key(self.ins.clip, self.prop, 1))
        for b in (self.prev, self.key, self.next):
            lay.addWidget(b)

    @property
    def prop(self) -> str:
        return self.prop_fn()

    @staticmethod
    def _btn(text, tip, checkable=False):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setFixedSize(22, 22)
        b.setAutoRaise(True)
        return b

    def set_value(self, v):
        self.slider.set_value(v)

    def sync(self, clip, t: float, fps: float):
        prop = self.prop
        anim = kf.is_animated(clip, prop)
        self.watch.setChecked(anim)
        self.watch.setStyleSheet("color:#4fc3f7;" if anim else "")
        for b in (self.prev, self.key, self.next):
            b.setVisible(anim)
        if anim:
            here = kf.key_index(kf.keys(clip, prop), clip.source_time(t), fps) is not None
            self.key.setText("◆" if here else "◇")
            self.key.setStyleSheet("color:#f0b429;" if here else "")
        v = kf.value_at(clip, prop, t)
        if v is not None:
            self.slider.set_value(v)


class EffectBox(QGroupBox):
    """Denetçide tek bir efektin kontrolleri (Premiere'deki Efekt Denetimleri gibi)."""

    def __init__(self, inspector, clip: Clip, effect: dict, index: int):
        d = fx.EFFECTS.get(effect["type"])
        super().__init__("fx  " + (_(d.name) if d else effect["type"]))
        self.ins, self.clip, self.effect, self.index = inspector, clip, effect, index
        self.anim_rows: list = []
        form = QFormLayout(self)
        head = QHBoxLayout()
        en = QCheckBox(_("Enabled"))
        en.setChecked(effect.get("enabled", True))
        en.toggled.connect(self._toggle)
        head.addWidget(en)
        head.addStretch(1)
        for text, tip, slot in (("▲", _("Move Up"), lambda: self._move(-1)),
                                ("▼", _("Move Down"), lambda: self._move(1)),
                                ("✕", _("Remove Effect"), self._remove)):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            head.addWidget(b)
        form.addRow(head)
        if d is None:
            return
        for key, label, kind, default, lo, hi, step, dec in d.params:
            val = effect["params"].get(key, default)
            if kind == "float":
                sl = SliderRow(lo, hi, step, dec, lambda v, k=key: self._set(k, v))
                sl.set_value(val)
                row = AnimRow(inspector, lambda k=key: kf.fx_prop(self.effect, k), sl)
                self.anim_rows.append(row)
                form.addRow(_(label), row)
            elif kind == "bool":
                cb = QCheckBox()
                cb.setChecked(bool(val))
                cb.toggled.connect(lambda v, k=key: self._set(k, bool(v)))
                form.addRow(_(label), cb)
            elif kind == "color":
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                btn = QPushButton()
                btn.setFixedHeight(24)
                self._paint_swatch(btn, val)
                btn.clicked.connect(lambda _c=False, k=key, b=btn: self._pick_color(k, b))
                drop = QToolButton()
                drop.setText("💧")
                drop.setToolTip(_("Eyedropper: click on the Program monitor"))
                drop.clicked.connect(lambda _c=False, k=key, b=btn: self._eyedropper(k, b))
                rl.addWidget(btn, 1)
                rl.addWidget(drop)
                form.addRow(_(label), row)
            elif kind == "file":
                btn = QPushButton(os.path.basename(val) if val else _("Choose .cube file…"))
                btn.setToolTip(plain_tip(val))
                btn.clicked.connect(lambda _c=False, k=key, b=btn: self._pick_file(k, b))
                form.addRow(_(label), btn)
        if effect["type"] == "color_match":
            self._build_color_match(form)
        if effect["type"] == "lumetri":
            b = QPushButton(_("Open Lumetri Color panel (curves, wheels)"))
            b.clicked.connect(lambda: self.ins.main.show_dock(self.ins.main.color_dock))
            form.addRow(b)

    def _build_color_match(self, form):
        p = self.effect["params"]
        name = p.get("ref_name")
        info = QLabel(_("Reference") + ": " + (name or _("none yet")))
        info.setTextFormat(Qt.PlainText)
        info.setWordWrap(True)
        info.setStyleSheet("color:#9ec1ff;" if name else "color:#e0a050;")
        form.addRow(info)
        pal = p.get("ref_palette") or []
        if pal:
            strip = QWidget()
            sl = QHBoxLayout(strip)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(3)
            for col in pal:
                sw = QLabel()
                sw.setFixedHeight(22)
                sw.setToolTip(col)
                sw.setStyleSheet(f"background:{col}; border-radius:3px;")
                sl.addWidget(sw, 1)
            form.addRow(strip)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        pick = QPushButton(_("Reference…"))
        pick.setToolTip(_("Pick an image or video from the project as the color reference"))
        pick.clicked.connect(lambda: self.ins.main.pick_color_reference_menu(self.clip, pick))
        again = QPushButton(_("Re-analyze"))
        again.setToolTip(_("Measure the clip again (e.g. after changing a LUT before this effect)"))
        again.clicked.connect(lambda: self.ins.main.reanalyze_color_match(self.clip))
        rl.addWidget(pick, 1)
        rl.addWidget(again, 1)
        form.addRow(row)
        if not name:
            hint = QLabel(_("Tip: right-click an image in the Project panel → Use as Color "
                            "Reference, then right-click clips → Apply Color Palette."))
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#80868b;")
            form.addRow(hint)

    def _pick_file(self, key, btn):
        m = self.ins.main
        start = m.settings.value("lut_dir", os.path.expanduser("~"))
        path, _f = QFileDialog.getOpenFileName(self, _("Choose .cube file…"), start,
                                               "LUT (*.cube *.CUBE)")
        if not path:
            return
        from .. import effects as _fx
        try:
            _fx.load_cube(path)
        except Exception as e:
            QMessageBox.warning(self, "LUT", str(e))
            return
        m.settings.setValue("lut_dir", os.path.dirname(path))
        m.before_edit()
        self.effect["params"][key] = path
        btn.setText(os.path.basename(path))
        btn.setToolTip(plain_tip(path))
        # Sonraki renk eşleştirme, LUT'lu görüntüye göre yeniden ölçülsün
        m.reanalyze_color_match(self.clip, push=False, quiet=True)
        m.on_edited(refresh_inspector=False)

    @staticmethod
    def _paint_swatch(btn, color):
        btn.setText(color)
        txt = "#000" if QColor(color).lightness() > 128 else "#fff"
        btn.setStyleSheet(f"background:{color}; color:{txt}; border-radius:4px;")

    def _set(self, key, value):
        self.ins.main.prop_edit(self.clip, kf.fx_prop(self.effect, key), value)

    def _toggle(self, on):
        self.ins.main.before_edit()
        self.effect["enabled"] = on
        self.ins.main.on_edited(refresh_inspector=False)

    def _move(self, delta):
        lst = self.clip.effects
        j = self.index + delta
        if 0 <= j < len(lst):
            self.ins.main.before_edit()
            lst[self.index], lst[j] = lst[j], lst[self.index]
            self.ins.main.on_edited()

    def _remove(self):
        self.ins.main.before_edit()
        kf.drop_effect_keys(self.clip, self.effect)
        self.clip.effects.pop(self.index)
        self.ins.main.on_edited()

    def _pick_color(self, key, btn):
        c = QColorDialog.getColor(QColor(self.effect["params"].get(key, "#00ff00")), self)
        if c.isValid():
            self.ins.main.before_edit()
            self.effect["params"][key] = c.name()
            self._paint_swatch(btn, c.name())
            self.ins.main.on_edited(refresh_inspector=False)

    def _eyedropper(self, key, btn):
        def picked(color: str):
            self.ins.main.before_edit()
            self.effect["params"][key] = color
            self._paint_swatch(btn, color)
            self.ins.main.on_edited(refresh_inspector=False)
        self.ins.main.monitor.start_pick(picked, disable_effect=self.effect)


class Inspector(QScrollArea):
    FIELDS = [
        # grup, alan, etiket, min, max, adım, ondalık, sonek, varsayılan, sadece
        ("Motion", "scale", "Scale", 0.05, 5.0, 0.05, 2, "x", 1.0, "visual"),
        ("Motion", "pos_x", "Position X", -4000, 4000, 1, 0, " px", 0.0, "visual"),
        ("Motion", "pos_y", "Position Y", -4000, 4000, 1, 0, " px", 0.0, "visual"),
        ("Motion", "rotation", "Rotation", -360, 360, 1, 1, "°", 0.0, "visual"),
        ("Motion", "opacity", "Opacity", 0, 1, 0.05, 2, "", 1.0, "visual"),
        ("Motion", "speed", "Speed", 0.1, 16, 0.05, 2, "x", 1.0, "media"),
        ("Color", "brightness", "Brightness", -1, 1, 0.02, 2, "", 0.0, "visualmedia"),
        ("Color", "contrast", "Contrast", 0, 3, 0.05, 2, "", 1.0, "visualmedia"),
        ("Color", "saturation", "Saturation", 0, 3, 0.05, 2, "", 1.0, "visualmedia"),
        ("Audio", "volume", "Volume", 0, 4, 0.05, 2, "", 1.0, "audio"),
        ("Fades", "fade_in", "Fade In", 0, 10, 0.1, 2, " s", 0.0, "all"),
        ("Fades", "fade_out", "Fade Out", 0, 10, 0.1, 2, " s", 0.0, "all"),
    ]

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setWidgetResizable(True)
        self.clip: Clip | None = None
        self._last_edit = (None, 0.0)
        root = QWidget()
        self.lay = QVBoxLayout(root)
        # İçerik büyüdükçe kaydırma alanı da büyüsün (aksi halde satırlar ezilir)
        self.lay.setSizeConstraint(QLayout.SetMinimumSize)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.title = QLabel(_("No clip selected"))
        self.title.setTextFormat(Qt.PlainText)  # klip adı dosyadan gelir: HTML yorumlanmasın
        self.title.setStyleSheet("font-weight:bold; font-size:13px; padding:4px;")
        self.title.setWordWrap(True)
        self.lay.addWidget(self.title)
        self.rows: dict = {}
        self.base_anim_rows: list[AnimRow] = []
        self.groups: dict[str, QGroupBox] = {}
        self.row_labels: dict[str, QLabel] = {}
        forms: dict[str, QFormLayout] = {}
        # Metin grubu
        self.text_group = QGroupBox(_("Text"))
        tf = QFormLayout(self.text_group)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setFixedHeight(64)
        self.text_edit.textChanged.connect(self._text_changed)
        tf.addRow(self.text_edit)
        self.font_spin = QSpinBox()
        self.font_spin.setRange(8, 600)
        self.font_spin.valueChanged.connect(lambda v: self._apply("font_size", int(v)))
        tf.addRow(_("Font Size"), self.font_spin)
        self.color_btn = QPushButton(_("Text Color…"))
        self.color_btn.clicked.connect(self._pick_color)
        tf.addRow(self.color_btn)
        self.lay.addWidget(self.text_group)
        for grp, field, label, lo, hi, step, dec, suf, default, _only in self.FIELDS:
            if grp not in self.groups:
                box = QGroupBox(_(grp))
                forms[grp] = QFormLayout(box)
                self.groups[grp] = box
                self.lay.addWidget(box)
            row = SliderRow(lo, hi, step, dec, lambda v, f=field: self._apply(f, v), suf)
            if field in kf.CLIP_PROPS:
                row = AnimRow(self, lambda f=field: f, row)
                self.base_anim_rows.append(row)
            lbl = QLabel(_(label))
            self.row_labels[field] = lbl
            forms[grp].addRow(lbl, row)
            self.rows[field] = row
        # Geçişler
        self.trans_group = QGroupBox(_("Transitions"))
        trf = QFormLayout(self.trans_group)
        self.trans_widgets = {}
        for edge, label in (("in", "Start"), ("out", "End")):
            combo = QComboBox()
            combo.addItem(_("None"), "")
            for kid, name, _cat in transitions.TRANSITIONS:
                combo.addItem(_(name), kid)
            dur = QDoubleSpinBox()
            dur.setRange(0.04, 10.0)
            dur.setSingleStep(0.1)
            dur.setDecimals(2)
            dur.setSuffix(" s")
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.addWidget(combo, 1)
            rl.addWidget(dur)
            combo.currentIndexChanged.connect(lambda _i, e=edge: self._trans_changed(e))
            dur.valueChanged.connect(lambda _v, e=edge: self._trans_changed(e))
            self.trans_widgets[edge] = (combo, dur)
            trf.addRow(_(label), row)
        self.lay.addWidget(self.trans_group)
        # Efektler
        self.fx_group = QGroupBox(_("Video Effects"))
        self.fx_lay = QVBoxLayout(self.fx_group)
        self.fx_add = QComboBox()
        self.fx_add.addItem("＋ " + _("Add effect…"), "")
        for cat in fx.CATEGORIES:
            for d in fx.EFFECTS.values():
                if d.category == cat:
                    self.fx_add.addItem(f"{_(d.name)}   ·  {_(cat)}", d.id)
        self.fx_add.activated.connect(self._add_effect)
        self.fx_lay.addWidget(self.fx_add)
        self.fx_empty = QLabel(_("No effects yet. Drag one from the Effects panel or pick above."))
        self.fx_empty.setWordWrap(True)
        self.fx_empty.setStyleSheet("color:#80868b; padding:4px;")
        self.fx_lay.addWidget(self.fx_empty)
        self.fx_clear = QPushButton(_("Remove All Effects"))
        self.fx_clear.clicked.connect(lambda: self.clip and self.main.remove_effect(self.clip, None))
        self.fx_lay.addWidget(self.fx_clear)
        self.fx_boxes: list[EffectBox] = []
        self.lay.insertWidget(1, self.fx_group)  # Efekt denetimleri en üstte
        # Seçili geçiş düzenleyici (zaman çizelgesinde geçişe tıklanınca)
        self.tsel_group = QGroupBox(_("Transition"))
        tsf = QFormLayout(self.tsel_group)
        self.tsel_kind = QComboBox()
        for kid, name, _cat in transitions.TRANSITIONS:
            self.tsel_kind.addItem(_(name), kid)
        self.tsel_dur = QDoubleSpinBox()
        self.tsel_dur.setRange(0.04, 10.0)
        self.tsel_dur.setSingleStep(0.1)
        self.tsel_dur.setDecimals(2)
        self.tsel_dur.setSuffix(" s")
        self.tsel_kind.currentIndexChanged.connect(lambda _i: self._tsel_changed())
        self.tsel_dur.valueChanged.connect(lambda _v: self._tsel_changed())
        tsf.addRow(_("Type"), self.tsel_kind)
        tsf.addRow(_("Duration"), self.tsel_dur)
        hint = QLabel(_("Tip: drag the transition's edges on the timeline to change its length."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#80868b;")
        tsf.addRow(hint)
        del_btn = QPushButton(_("Delete Transition"))
        del_btn.clicked.connect(lambda: self.main.delete_selected())
        tsf.addRow(del_btn)
        self.lay.insertWidget(1, self.tsel_group)
        self.tsel_group.setVisible(False)
        self.tsel = None
        from .mask_panel import MaskPanel
        self.mask_panel = MaskPanel(self)
        self.lay.insertWidget(self.lay.indexOf(self.groups["Motion"]) + 1, self.mask_panel)
        self.mask_panel.setVisible(False)
        from .mixer import AudioFxPanel
        self.afx_panel = AudioFxPanel(self)
        self.lay.insertWidget(self.lay.indexOf(self.groups["Audio"]) + 1, self.afx_panel)
        self.afx_panel.setVisible(False)
        self.reset_btn = QPushButton(_("Reset"))
        self.reset_btn.clicked.connect(self._reset)
        self.lay.addWidget(self.reset_btn)
        self.lay.addStretch(1)
        self.setWidget(root)
        self.set_clip(None)

    def set_clip(self, clip: Clip | None):
        self.clip = clip
        self.tsel = None
        self.tsel_group.setVisible(False)
        enabled = clip is not None
        for box in self.groups.values():
            box.setVisible(enabled)
        self.reset_btn.setVisible(enabled)
        self.trans_group.setVisible(enabled)
        self.text_group.setVisible(enabled and clip.kind == "text")
        for b in self.fx_boxes:
            b.setParent(None)
            b.deleteLater()
        self.fx_boxes = []
        self.mask_panel.set_clip(clip)
        self.afx_panel.set_clip(clip)
        if not enabled:
            self.fx_group.setVisible(False)
            self.title.setText(_("No clip selected"))
            return
        is_audio = not self.main.project.track_is_video(clip.track)
        self.fx_group.setVisible(not is_audio and clip.kind == "media")
        self.title.setText(f"{clip.name}\n{_('Duration')}: "
                           f"{timecode(clip.duration, self.main.project.fps)}")
        group_has = {g: False for g in self.groups}
        for grp, field, *rest in self.FIELDS:
            only = rest[-1]
            show = (only == "all" or (only == "audio" and is_audio)
                    or (only.startswith("visual") and not is_audio
                        and not (only == "visualmedia" and clip.kind == "text"))
                    or (only == "media" and clip.kind == "media"))
            self.rows[field].setVisible(show)
            self.row_labels[field].setVisible(show)
            self.rows[field].set_value(getattr(clip, field))
            group_has[grp] |= show
        for grp, box in self.groups.items():
            box.setVisible(group_has[grp])
        for edge in ("in", "out"):
            combo, dur = self.trans_widgets[edge]
            combo.blockSignals(True)
            dur.blockSignals(True)
            kind = clip.trans_in if edge == "in" else clip.trans_out
            if edge == "out" and not kind:
                nxt = self.main.project.next_adjacent(clip)
                if nxt is not None and nxt.trans_in:
                    kind = nxt.trans_in
            i = combo.findData(kind)
            combo.setCurrentIndex(max(i, 0))
            dur.setValue(clip.trans_in_dur if edge == "in" else clip.trans_out_dur)
            combo.blockSignals(False)
            dur.blockSignals(False)
        if self.fx_group.isVisible():
            for i, e in enumerate(clip.effects):
                box = EffectBox(self, clip, e, i)
                self.fx_lay.insertWidget(self.fx_lay.count() - 1, box)
                self.fx_boxes.append(box)
            self.fx_empty.setVisible(not clip.effects)
            self.fx_clear.setVisible(bool(clip.effects))
            self.fx_group.setTitle(_("Video Effects") + (f" ({len(clip.effects)})"
                                                         if clip.effects else ""))
        self.sync_time(self.main.timeline.playhead)
        if clip.kind == "text":
            self.text_edit.blockSignals(True)
            self.text_edit.setPlainText(clip.text)
            self.text_edit.blockSignals(False)
            self.font_spin.blockSignals(True)
            self.font_spin.setValue(clip.font_size)
            self.font_spin.blockSignals(False)

    def set_transition(self, clip: Clip, edge: str):
        """Zaman çizelgesinde seçilen geçişi düzenle."""
        self.set_clip(None)
        self.tsel = (clip, edge)
        kind = clip.trans_in if edge == "in" else clip.trans_out
        dur = clip.trans_in_dur if edge == "in" else clip.trans_out_dur
        self.title.setText(_("Transition") + ": " + _(transitions.NAMES.get(kind, kind))
                           + "\n" + clip.name)
        for w in (self.tsel_kind, self.tsel_dur):
            w.blockSignals(True)
        self.tsel_kind.setCurrentIndex(max(0, self.tsel_kind.findData(kind)))
        self.tsel_dur.setValue(dur)
        for w in (self.tsel_kind, self.tsel_dur):
            w.blockSignals(False)
        self.tsel_group.setVisible(True)

    def _tsel_changed(self):
        if self.tsel is None:
            return
        clip, edge = self.tsel
        self._push_coalesced((clip.id, "tsel", edge))
        kind, dur = self.tsel_kind.currentData(), self.tsel_dur.value()
        for c in self.main.project.linked(clip):
            if edge == "in" and c.trans_in:
                c.trans_in, c.trans_in_dur = kind, dur
            elif edge == "out" and c.trans_out:
                c.trans_out, c.trans_out_dur = kind, dur
        self.title.setText(_("Transition") + ": " + _(transitions.NAMES.get(kind, kind))
                           + "\n" + clip.name)
        self.main.on_edited(refresh_inspector=False)

    def focus_effects(self, index: int | None = None):
        """Efekt bölümüne kaydır; index verilirse o efekti vurgula."""
        from PySide6.QtCore import QTimer
        target = self.fx_group
        if index is not None and 0 <= index < len(self.fx_boxes):
            target = self.fx_boxes[index]
            target.setStyleSheet("QGroupBox { border:1px solid #f0b429; }")
        dock = self.parentWidget()
        if dock is not None:
            dock.raise_()
        QTimer.singleShot(0, lambda: self.ensureWidgetVisible(target, 0, 40))

    def _push_coalesced(self, key):
        last_key, last_t = self._last_edit
        now = time.monotonic()
        if key != last_key or now - last_t > 1.5:
            self.main.before_edit()
        self._last_edit = (key, now)

    def sync_time(self, t: float):
        """Oynatma kafası değişince animasyonlu değerleri ve ◆ durumlarını güncelle."""
        clip = self.clip
        if clip is None:
            return
        fps = self.main.project.fps
        for row in self.base_anim_rows:
            if not row.isHidden():
                row.sync(clip, t, fps)
        for box in self.fx_boxes:
            for row in box.anim_rows:
                row.sync(clip, t, fps)

    def _apply(self, field, value):
        if self.clip is None:
            return
        if field in kf.CLIP_PROPS:
            self.main.prop_edit(self.clip, field, value)
            return
        self._push_coalesced((self.clip.id, field))
        if field == "speed":
            self.main.project.set_speed(self.clip, value)
        else:
            setattr(self.clip, field, value)
            if field in ("fade_in", "fade_out"):  # bağlı ses klibine de uygula
                for c in self.main.project.linked(self.clip):
                    setattr(c, field, value)
        self.main.on_edited(refresh_inspector=False)

    def _trans_changed(self, edge):
        if self.clip is None:
            return
        combo, dur = self.trans_widgets[edge]
        self._push_coalesced((self.clip.id, "trans", edge))
        self.main.project.apply_transition(self.clip, edge, combo.currentData(), dur.value())
        self.main.on_edited(refresh_inspector=False)

    def _add_effect(self, index):
        eid = self.fx_add.itemData(index)
        self.fx_add.setCurrentIndex(0)
        if eid and self.clip is not None:
            self.main.apply_effect_key("effect:" + eid, self.clip)

    def _text_changed(self):
        if self.clip is None or self.clip.kind != "text":
            return
        self._push_coalesced((self.clip.id, "text"))
        self.clip.text = self.text_edit.toPlainText()
        self.clip.name = self.clip.text.split("\n")[0][:24] or "Text"
        self.main.on_edited(refresh_inspector=False)

    def _pick_color(self):
        if self.clip is None:
            return
        c = QColorDialog.getColor(QColor(self.clip.color), self, _("Text Color…"))
        if c.isValid():
            self.main.before_edit()
            self.clip.color = c.name()
            self.main.on_edited(refresh_inspector=False)

    def _reset(self):
        if self.clip is None:
            return
        self.main.before_edit()
        for grp, field, *rest in self.FIELDS:
            if field == "speed":
                continue
            setattr(self.clip, field, rest[-2])
        self.clip.effects.clear()
        self.main.on_edited()
