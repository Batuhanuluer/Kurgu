"""Altyazılar paneli: konuşmadan altyazı, altyazı listesi, SRT/VTT, stil."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from .. import captions as cap
from ..i18n import _
from .panels import SliderRow
from .timeline import timecode


class CaptionsPanel(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        gen = QPushButton("🎙 " + _("Transcribe Speech to Captions…"))
        gen.setToolTip(_("Automatic captions with Whisper speech recognition"))
        gen.clicked.connect(lambda: main.transcribe_dialog())
        lay.addWidget(gen)
        row = QHBoxLayout()
        for text, tip, slot in ((_("Add"), _("Add a caption at the playhead"), main.add_caption),
                                (_("Import…"), _("Import SRT / VTT"), main.import_captions),
                                (_("Export…"), _("Export SRT / VTT"), main.export_captions)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([_("Start"), _("End"), _("Text")])
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(True)
        self.table.cellClicked.connect(self._jump)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table, 1)
        rm = QPushButton(_("Delete Selected Captions"))
        rm.clicked.connect(self._delete)
        lay.addWidget(rm)

        style = QGroupBox(_("Caption Style"))
        sf = QFormLayout(style)
        self.size = SliderRow(2, 12, 0.5, 1, lambda v: self._style("size", v / 100), "%")
        self.pos = SliderRow(40, 98, 1, 0, lambda v: self._style("y", v / 100), "%")
        self.box = QCheckBox(_("Background box"))
        self.box.toggled.connect(lambda on: self._style("box", bool(on)))
        self.box_op = SliderRow(0, 100, 5, 0, lambda v: self._style("box_opacity", v / 100), "%")
        self.bold = QCheckBox(_("Bold"))
        self.bold.toggled.connect(lambda on: self._style("bold", bool(on)))
        self.outline = QCheckBox(_("Outline"))
        self.outline.toggled.connect(lambda on: self._style("outline", bool(on)))
        self.color = QPushButton()
        self.color.clicked.connect(lambda: self._pick_color("color"))
        sf.addRow(_("Size"), self.size)
        sf.addRow(_("Position"), self.pos)
        sf.addRow(_("Color"), self.color)
        sf.addRow(self.box)
        sf.addRow(_("Box opacity"), self.box_op)
        self.upper = QCheckBox(_("UPPERCASE"))
        self.upper.toggled.connect(lambda on: self._style("uppercase", bool(on)))
        checks = QHBoxLayout()
        checks.addWidget(self.bold)
        checks.addWidget(self.outline)
        checks.addWidget(self.upper)
        sf.addRow(checks)
        lay.addWidget(style)

        # Kelime vurgusu (Reels / TikTok tarzı)
        hl = QGroupBox(_("Word Highlight"))
        hf = QFormLayout(hl)
        reels = QPushButton("✨ " + _("Reels Style"))
        reels.setToolTip(_("Big centered captions in short phrases; the spoken word is "
                           "highlighted"))
        reels.clicked.connect(lambda: self.apply_reels())
        hf.addRow(reels)
        self.hl = QCheckBox(_("Highlight the spoken word"))
        self.hl.toggled.connect(lambda on: self._style("highlight", bool(on)))
        hf.addRow(self.hl)
        self.hl_color = QPushButton()
        self.hl_color.clicked.connect(lambda: self._pick_color("highlight_color"))
        self.hl_box = QCheckBox(_("Box behind the word"))
        self.hl_box.toggled.connect(lambda on: self._style("highlight_box", bool(on)))
        hrow = QHBoxLayout()
        hrow.addWidget(self.hl_color, 1)
        hrow.addWidget(self.hl_box)
        hf.addRow(_("Color"), hrow)
        srow = QHBoxLayout()
        self.words = QSpinBox()
        self.words.setRange(1, 8)
        self.words.setValue(3)
        self.words.setSuffix(" " + _("words"))
        split = QPushButton(_("Split"))
        split.setToolTip(_("Re-split all captions into short phrases using the word timing"))
        split.clicked.connect(lambda: self.split_phrases(self.words.value()))
        srow.addWidget(self.words, 1)
        srow.addWidget(split)
        hf.addRow(_("Short phrases"), srow)
        lay.addWidget(hl)
        self.info = QLabel()
        self.info.setStyleSheet("color:#80868b;")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.refresh()

    # ------------------------------------------------------------ liste
    def clips(self):
        p = self.main.project
        tis = p.caption_tracks()
        return sorted([c for c in p.clips if c.track in tis and c.kind == "caption"],
                      key=lambda c: c.start)

    def refresh(self):
        self._loading = True
        p = self.main.project
        cs = self.clips()
        self.table.setRowCount(len(cs))
        for i, c in enumerate(cs):
            for j, val in enumerate((timecode(c.start, p.fps), timecode(c.end, p.fps), c.text)):
                it = QTableWidgetItem(val)
                it.setData(Qt.UserRole, c.id)
                if j < 2:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i, j, it)
        self.table.resizeRowsToContents()
        ti = p.caption_track(create=False)
        st = cap.style_of(p.tracks[ti] if ti is not None else None)
        self.size.set_value(st["size"] * 100)
        self.pos.set_value(st["y"] * 100)
        self.box.setChecked(bool(st["box"]))
        self.box_op.set_value(st["box_opacity"] * 100)
        self.bold.setChecked(bool(st["bold"]))
        self.outline.setChecked(bool(st["outline"]))
        self.upper.setChecked(bool(st["uppercase"]))
        self.hl.setChecked(bool(st["highlight"]))
        self.hl_box.setChecked(bool(st["highlight_box"]))
        self.color.setStyleSheet(f"background:{st['color']}; min-height:20px;")
        self.hl_color.setStyleSheet(f"background:{st['highlight_color']}; min-height:20px;")
        self.info.setText(_("{n} captions").format(n=len(cs)) if cs else
                          _("No captions yet. Transcribe speech, import an SRT file or add "
                            "one at the playhead."))
        self._loading = False

    def _jump(self, row, _col):
        it = self.table.item(row, 0)
        c = self.main.project.clip_by_id(it.data(Qt.UserRole)) if it else None
        if c is not None:
            self.main.timeline.selected = {c.id}
            self.main.timeline.set_playhead(c.start + 1e-3)
            self.main.timeline.ensure_visible(c.start)
            self.main.timeline.update()

    def _edited(self, item):
        if self._loading or item.column() != 2:
            return
        c = self.main.project.clip_by_id(item.data(Qt.UserRole))
        if c is None or c.text == item.text():
            return
        self.main.before_edit()
        c.text = item.text()
        c.name = c.text.replace("\n", " ")[:40]
        self.main.on_edited(refresh_inspector=False)

    def _delete(self):
        ids = {self.table.item(r.row(), 0).data(Qt.UserRole)
               for r in self.table.selectionModel().selectedRows()}
        if ids:
            self.main.before_edit()
            self.main.project.clips = [c for c in self.main.project.clips if c.id not in ids]
            self.main.on_edited()

    # ------------------------------------------------------------ stil
    def _style(self, key, value):
        if self._loading:
            return
        p = self.main.project
        ti = p.caption_track(create=False)
        if ti is None:
            return
        self.main.inspector._push_coalesced(("caption-style", key))
        p.tracks[ti].style[key] = value
        self.main.on_edited(refresh_inspector=False)

    def _pick_color(self, key="color"):
        p = self.main.project
        ti = p.caption_track(create=False)
        st = cap.style_of(p.tracks[ti] if ti is not None else None)
        col = QColorDialog.getColor(QColor(st[key]), self, _("Color"))
        if col.isValid():
            self._style(key, col.name())
            self.refresh()

    # ------------------------------------------------------------ Reels tarzı
    def split_phrases(self, max_words: int):
        p = self.main.project
        ti = p.caption_track(create=False)
        if ti is None or not self.clips():
            self.main.statusBar().showMessage(_("No captions yet"), 4000)
            return []
        self.main.before_edit()
        made = cap.rechunk(p, ti, max_words)
        self.main.on_edited()
        self.refresh()
        return made

    def apply_reels(self):
        p = self.main.project
        ti = p.caption_track(create=False)
        if ti is None:
            self.main.statusBar().showMessage(_("No captions yet"), 4000)
            return
        self.main.before_edit()
        p.tracks[ti].style.update(cap.REELS_STYLE)
        if self.clips():
            cap.rechunk(p, ti, self.words.value())
        self.main.on_edited()
        self.refresh()
