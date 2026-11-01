"""Ses mikseri (Premiere "Ses İzi Mikseri"): iz başına seviye göstergesi, fader, pan,
sessiz/solo; ana çıkış faderi. Ayrıca klip ses efektleri kutusu (Denetçi'de)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QToolButton, QVBoxLayout, QWidget)

from .. import audio as au
from ..i18n import _
from .panels import SliderRow

DB_MIN, DB_MAX = -60.0, 6.0


class Meter(QWidget):
    """İki kanallı tepe göstergesi (dB ölçekli, tepe tutmalı)."""

    def __init__(self):
        super().__init__()
        self.setFixedWidth(18)
        self.setMinimumHeight(120)
        self.vals = [0.0, 0.0]
        self.peaks = [0.0, 0.0]
        self.clip = False

    def set_levels(self, l, r):
        for i, v in enumerate((l, r)):
            self.vals[i] = max(v, self.vals[i] * 0.82)  # yumuşak düşüş
            self.peaks[i] = max(v, self.peaks[i] - 0.01)
        if max(l, r) >= 0.999:
            self.clip = True
        self.update()

    @staticmethod
    def frac(v):
        db = au.lin2db(v)
        return min(max((db - DB_MIN) / (DB_MAX - DB_MIN), 0.0), 1.0)

    def paintEvent(self, ev):
        p = QPainter(self)
        h = self.height() - 6
        p.fillRect(self.rect(), QColor("#15171a"))
        g = QLinearGradient(0, self.height(), 0, 0)
        g.setColorAt(0.0, QColor("#2fae5a"))
        g.setColorAt(0.75, QColor("#c9c23a"))
        g.setColorAt(0.92, QColor("#e04040"))
        for i in range(2):
            x = 2 + i * 8
            f = self.frac(self.vals[i])
            p.fillRect(QRectF(x, 6 + h * (1 - f), 6, h * f), g)
            pk = self.frac(self.peaks[i])
            p.fillRect(QRectF(x, 6 + h * (1 - pk), 6, 2), QColor("#ffffff"))
        p.fillRect(QRectF(2, 0, 14, 4), QColor("#ff3030") if self.clip else QColor("#3a3f47"))
        p.setPen(QPen(QColor(255, 255, 255, 40)))
        for db in (0, -6, -12, -24, -48):
            y = 6 + h * (1 - (db - DB_MIN) / (DB_MAX - DB_MIN))
            p.drawLine(0, int(y), 18, int(y))
        p.end()

    def mousePressEvent(self, ev):
        self.clip = False  # kırpılma uyarısını sıfırla
        self.update()


class Strip(QWidget):
    def __init__(self, panel, track_index, master=False):
        super().__init__()
        self.panel, self.ti, self.master = panel, track_index, master
        self.setMinimumWidth(78)
        self.setMaximumWidth(96)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(4)
        self.name = QLabel()
        self.name.setAlignment(Qt.AlignCenter)
        self.name.setStyleSheet("font-weight:bold;")
        lay.addWidget(self.name)
        self.pan = QSlider(Qt.Horizontal)
        self.pan.setRange(-100, 100)
        self.pan.setToolTip(_("Pan (double-click: center)"))
        self.pan.valueChanged.connect(self._pan)
        self.pan.mouseDoubleClickEvent = lambda e: self.pan.setValue(0)
        if not master:
            lay.addWidget(self.pan)
        else:  # ana çıkışta pan yok: göstergeler diğer şeritlerle aynı hizada kalsın
            lay.addSpacing(self.pan.sizeHint().height() + lay.spacing())
        mid = QHBoxLayout()
        self.meter = Meter()
        self.fader = QSlider(Qt.Vertical)
        self.fader.setRange(int(DB_MIN * 10), int(DB_MAX * 10))
        self.fader.setToolTip(_("Volume (double-click: 0 dB)"))
        self.fader.valueChanged.connect(self._fader)
        self.fader.sliderPressed.connect(lambda: self.panel.push(("fader", self.ti)))
        self.fader.mouseDoubleClickEvent = lambda e: self.fader.setValue(0)
        mid.addWidget(self.meter)
        mid.addWidget(self.fader)
        lay.addLayout(mid, 1)
        self.db = QLabel("0.0 dB")
        self.db.setAlignment(Qt.AlignCenter)
        self.db.setStyleSheet("font-family: monospace; color:#9aa0a6;")
        lay.addWidget(self.db)
        btns = QHBoxLayout()
        self.mute = QToolButton()
        self.mute.setText("M")
        self.mute.setCheckable(True)
        self.mute.setToolTip(_("Mute"))
        self.mute.toggled.connect(self._mute)
        self.solo = QToolButton()
        self.solo.setText("S")
        self.solo.setCheckable(True)
        self.solo.setToolTip(_("Solo"))
        self.solo.toggled.connect(self._solo)
        for b, col in ((self.mute, "#d9534f"), (self.solo, "#c99a2e")):
            b.setStyleSheet(f"QToolButton:checked {{ background:{col}; color:white; }}")
        if not master:
            btns.addWidget(self.mute)
            btns.addWidget(self.solo)
        lay.addLayout(btns)
        self._loading = False

    def track(self):
        return None if self.master else self.panel.main.project.tracks[self.ti]

    def load(self):
        self._loading = True
        p = self.panel.main.project
        if self.master:
            self.name.setText(_("Master"))
            v = p.master_db
        else:
            tr = self.track()
            self.name.setText(tr.name)
            v = tr.volume_db
            self.pan.setValue(int(round(tr.pan * 100)))
            self.mute.setChecked(tr.muted)
            self.solo.setChecked(tr.solo)
        self.fader.setValue(int(round(v * 10)))
        self.db.setText(f"{v:+.1f} dB")
        self._loading = False

    def _fader(self, v):
        db = v / 10
        self.db.setText(f"{db:+.1f} dB")
        if self._loading:
            return
        self.panel.push(("fader", self.ti))
        if self.master:
            self.panel.main.project.master_db = db
        else:
            self.track().volume_db = db
        self.panel.changed()

    def _pan(self, v):
        if self._loading:
            return
        self.panel.push(("pan", self.ti))
        self.track().pan = v / 100
        self.panel.changed()

    def _mute(self, on):
        if self._loading:
            return
        self.panel.main.before_edit()
        self.track().muted = bool(on)
        self.panel.changed()

    def _solo(self, on):
        if self._loading:
            return
        self.panel.main.before_edit()
        self.track().solo = bool(on)
        self.panel.changed()


class MixerPanel(QScrollArea):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setWidgetResizable(True)
        self.body = QWidget()
        self.row = QHBoxLayout(self.body)
        self.row.setContentsMargins(4, 4, 4, 4)
        self.setWidget(self.body)
        self.strips: list[Strip] = []
        self._sig = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(40)
        self.rebuild()

    def rebuild(self):
        p = self.main.project
        sig = tuple((i, t.name) for i, t in enumerate(p.tracks) if t.kind == "audio")
        if sig != self._sig:
            self._sig = sig
            for s in self.strips:
                s.setParent(None)
                s.deleteLater()
            self.strips = []
            while self.row.count():
                self.row.takeAt(0)
            for ti in p.track_indices("audio"):
                s = Strip(self, ti)
                self.strips.append(s)
                self.row.addWidget(s)
            self.row.addStretch(1)
            m = Strip(self, -1, master=True)
            self.strips.append(m)
            self.row.addWidget(m)
        for s in self.strips:
            s.load()

    def push(self, key):
        self.main.inspector._push_coalesced(("mixer",) + key)

    def changed(self):
        self.main.on_edited(refresh_inspector=False)
        self.main.timeline.update()  # oynatırken de anında duyulur (aynı proje nesnesi)

    def _poll(self):
        if not self.isVisible():
            return
        dev = getattr(self.main.player, "device", None)
        lv = getattr(getattr(dev, "comp", None), "levels", None) if dev else None
        playing = self.main.player.playing and lv
        for s in self.strips:
            key = "master" if s.master else s.ti
            l, r = (lv.get(key, (0.0, 0.0)) if playing else (0.0, 0.0))
            s.meter.set_levels(l, r)


# ------------------------------------------------------------ klip ses efektleri (Denetçi)
class AudioFxPanel(QGroupBox):
    def __init__(self, inspector):
        super().__init__(_("Audio Effects"))
        self.ins = inspector
        self.main = inspector.main
        self.clip = None
        lay = QVBoxLayout(self)
        self.add = QComboBox()
        self.add.addItem("＋ " + _("Add audio effect…"), "")
        for k, d in au.AUDIO_FX.items():
            self.add.addItem(_(d.name), k)
        self.add.activated.connect(self._add)
        lay.addWidget(self.add)
        self.box = QVBoxLayout()
        lay.addLayout(self.box)
        norm = QPushButton("📢 " + _("Normalize Loudness…"))
        norm.setToolTip(_("Match the loudness of the selected clips to a target "
                          "(YouTube -14 LUFS, podcast -16 LUFS…)"))
        norm.clicked.connect(self.main.normalize_dialog)
        lay.addWidget(norm)

    def set_clip(self, clip):
        self.clip = clip
        audio = clip is not None and not self.main.project.track_is_video(clip.track)
        self.setVisible(audio)
        if audio:
            self._rebuild()

    def _rebuild(self):
        while self.box.count():
            w = self.box.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for fxd in self.clip.audio_fx:
            self.box.addWidget(self._fx_box(fxd))

    def _fx_box(self, fxd):
        d = au.AUDIO_FX.get(fxd["type"])
        g = QGroupBox(_(d.name) if d else fxd["type"])
        form = QFormLayout(g)
        head = QHBoxLayout()
        en = QCheckBox(_("Enabled"))
        en.setChecked(fxd.get("enabled", True))
        en.toggled.connect(lambda v: self._set(fxd, "enabled", bool(v), top=True))
        rm = QToolButton()
        rm.setText("✕")
        rm.setToolTip(_("Remove Effect"))
        rm.clicked.connect(lambda: self._remove(fxd))
        head.addWidget(en)
        head.addStretch(1)
        head.addWidget(rm)
        form.addRow(head)
        if d:
            for key, label, lo, hi, default, unit in d.params:
                dec = 0 if (hi - lo) >= 20 else 1
                step = 1 if dec == 0 else 0.5
                sl = SliderRow(lo, hi, step, dec, lambda v, k=key: self._set(fxd, k, float(v)),
                               f" {unit}" if unit else "")
                sl.set_value(float(fxd["params"].get(key, default)))
                form.addRow(_(label), sl)
        g.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        return g

    def _add(self, i):
        k = self.add.itemData(i)
        self.add.setCurrentIndex(0)
        if not k or self.clip is None:
            return
        self.main.before_edit()
        self.clip.audio_fx.append(au.new_audio_fx(k))
        self._rebuild()
        self.main.on_edited(refresh_inspector=False)

    def _remove(self, fxd):
        self.main.before_edit()
        self.clip.audio_fx = [e for e in self.clip.audio_fx if e is not fxd]
        self._rebuild()
        self.main.on_edited(refresh_inspector=False)

    def _set(self, fxd, key, value, top=False):
        self.ins._push_coalesced((self.clip.id, "afx", fxd.get("id"), key))
        if top:
            fxd[key] = value
        else:
            fxd["params"][key] = value
        self.main.on_edited(refresh_inspector=False)

