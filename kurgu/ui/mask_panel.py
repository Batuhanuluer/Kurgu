"""Denetçi: Opaklık ve Maske bölümü (karıştırma modu, iz matı, maskeler)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout,
                               QWidget)

from .. import masks as mk
from ..i18n import _
from .panels import SliderRow

SHAPES = [("ellipse", "Ellipse", "◯"), ("rect", "Rectangle", "▭"), ("polygon", "Pen", "✒")]
MODES = [("add", "Add"), ("subtract", "Subtract"), ("intersect", "Intersect")]


class MaskRow(QFrame):
    def __init__(self, panel, clip, mask, index):
        super().__init__()
        self.panel, self.clip, self.mask = panel, clip, mask
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("QFrame { border:1px solid #3a3f47; border-radius:5px; }"
                           "QLabel, QCheckBox { border:none; }")
        form = QFormLayout(self)
        head = QHBoxLayout()
        name = dict((k, n) for k, n, _i in SHAPES).get(mask.get("shape"), "Mask")
        en = QCheckBox(_("Mask") + f" {index + 1} · " + _(name))
        en.setChecked(mask.get("enabled", True))
        en.toggled.connect(lambda v: self._set("enabled", bool(v)))
        head.addWidget(en, 1)
        sel = QToolButton()
        sel.setText("◎")
        sel.setToolTip(_("Edit this mask on the Program monitor"))
        sel.clicked.connect(lambda: panel.select_mask(mask["id"]))
        head.addWidget(sel)
        rm = QToolButton()
        rm.setText("✕")
        rm.setToolTip(_("Delete Mask"))
        rm.clicked.connect(lambda: panel.remove_mask(mask["id"]))
        head.addWidget(rm)
        form.addRow(head)
        for key, label, lo, hi, step, dec, suf in (
                ("feather", "Feather", 0, 500, 1, 0, " px"),
                ("expansion", "Expansion", -300, 300, 1, 0, " px"),
                ("opacity", "Mask Opacity", 0, 1, 0.05, 2, "")):
            sl = SliderRow(lo, hi, step, dec, lambda v, k=key: self._set(k, float(v)), suf)
            sl.set_value(float(mask.get(key, 0.0 if key != "opacity" else 1.0)))
            form.addRow(_(label), sl)
        row = QHBoxLayout()
        inv = QCheckBox(_("Inverted"))
        inv.setChecked(bool(mask.get("invert")))
        inv.toggled.connect(lambda v: self._set("invert", bool(v)))
        mode = QComboBox()
        for k, n in MODES:
            mode.addItem(_(n), k)
        mode.setCurrentIndex(max(0, mode.findData(mask.get("mode", "add"))))
        mode.currentIndexChanged.connect(lambda _i: self._set("mode", mode.currentData()))
        row.addWidget(inv)
        row.addWidget(mode, 1)
        form.addRow(row)

    def _set(self, key, value):
        self.panel.edit(("mask", self.mask["id"], key))
        self.mask[key] = value
        self.panel.changed()


class MaskPanel(QGroupBox):
    def __init__(self, inspector):
        super().__init__(_("Opacity & Masks"))
        self.ins = inspector
        self.main = inspector.main
        self.clip = None
        self._loading = False
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.blend = QComboBox()
        for k, n in mk.BLEND_MODES:
            self.blend.addItem(_(n), k)
        self.blend.currentIndexChanged.connect(self._blend)
        form.addRow(_("Blend Mode"), self.blend)
        mrow = QHBoxLayout()
        self.matte = QComboBox()
        self.matte.currentIndexChanged.connect(self._matte)
        self.matte_inv = QCheckBox(_("Reverse"))
        self.matte_inv.toggled.connect(self._matte)
        mrow.addWidget(self.matte, 1)
        mrow.addWidget(self.matte_inv)
        form.addRow(_("Track Matte"), mrow)
        lay.addLayout(form)
        btns = QHBoxLayout()
        for key, name, icon in SHAPES:
            b = QPushButton(f"{icon} {_(name)}")
            b.setToolTip(_("Add mask") + (": " + _("click points on the Program monitor, "
                                                   "click the first point to close")
                                          if key == "polygon" else ""))
            b.clicked.connect(lambda _c=False, k=key: self.add_mask(k))
            btns.addWidget(b)
        lay.addLayout(btns)
        self.list = QVBoxLayout()
        lay.addLayout(self.list)
        self.hint = QLabel(_("Masks hide parts of the clip. Drag the outline and its points "
                             "on the Program monitor."))
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#80868b;")
        lay.addWidget(self.hint)

    # ------------------------------------------------------------
    def set_clip(self, clip):
        self.clip = clip
        visual = clip is not None and clip.kind == "media" and \
            self.main.project.track_is_video(clip.track)
        self.setVisible(visual)
        if not visual:
            return
        self._loading = True
        self.blend.setCurrentIndex(max(0, self.blend.findData(clip.blend or "normal")))
        p = self.main.project
        self.matte.clear()
        self.matte.addItem(_("None"), "")
        for ti in reversed(p.track_indices("video")):
            if ti == clip.track or p.tracks[ti].captions:
                continue
            nm = p.tracks[ti].name
            self.matte.addItem(f"{nm} · " + _("Alpha"), f"{ti}:alpha")
            self.matte.addItem(f"{nm} · " + _("Luma"), f"{ti}:luma")
        mt = clip.matte or {}
        if "track" in mt:
            i = self.matte.findData(f"{mt['track']}:{mt.get('type', 'alpha')}")
            self.matte.setCurrentIndex(max(0, i))
        self.matte_inv.setChecked(bool(mt.get("invert")))
        self._loading = False
        self._rebuild()

    def _rebuild(self):
        while self.list.count():
            w = self.list.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)  # hemen kaldır: eski satır yenisinin üstünde kalmasın
                w.deleteLater()
        for i, m in enumerate(self.clip.masks if self.clip else []):
            self.list.addWidget(MaskRow(self, self.clip, m, i))

    def edit(self, key):
        self.ins._push_coalesced((self.clip.id, key))

    def changed(self):
        self.main.on_edited(refresh_inspector=False)
        self.main.monitor.view.update()

    def _blend(self, _i):
        if self._loading or self.clip is None:
            return
        self.edit("blend")
        self.clip.blend = self.blend.currentData()
        self.changed()

    def _matte(self, *_a):
        if self._loading or self.clip is None:
            return
        self.main.before_edit()
        d = self.matte.currentData()
        if d:
            ti, kind = d.split(":")
            d = (int(ti), kind)
        self.clip.matte = {} if not d else {"track": d[0], "type": d[1],
                                            "invert": self.matte_inv.isChecked()}
        self.changed()
        if d and not self.main.project.tracks[d[0]].muted:
            self.main.statusBar().showMessage(
                _("Tip: hide the matte track (H) so only its shape is used."), 6000)

    def add_mask(self, shape):
        if self.clip is None:
            return
        if shape == "polygon":
            self.main.monitor.start_pen(self.clip)
            return
        self.main.before_edit()
        m = mk.new_mask(shape)
        self.clip.masks.append(m)
        self.main.monitor.sel_mask = m["id"]
        self._rebuild()
        self.changed()

    def add_polygon(self, clip, points):
        self.main.before_edit()
        m = mk.new_mask("polygon")
        m["points"] = [list(p) for p in points]
        clip.masks.append(m)
        self.main.monitor.sel_mask = m["id"]
        if clip is self.clip:
            self._rebuild()
        self.changed()

    def remove_mask(self, mid):
        self.main.before_edit()
        self.clip.masks = [m for m in self.clip.masks if m["id"] != mid]
        self._rebuild()
        self.changed()

    def select_mask(self, mid):
        self.main.monitor.sel_mask = mid
        self.main.monitor.view.update()
