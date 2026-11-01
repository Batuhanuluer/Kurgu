"""Seslendirme kaydı penceresi: oynatma kafasından itibaren video oynarken mikrofonu
kaydeder, durdurunca WAV'ı seçilen ses izine yerleştirir."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout)

from .. import recorder as rc
from ..i18n import _
from .mixer import Meter
from .timeline import timecode

NEW_TRACK = -1


class VoiceoverDialog(QDialog):
    def __init__(self, main, fake: bool = False):
        super().__init__(main)
        self.main = main
        self.fake = fake  # test: cihaz yerine feed() ile veri
        self.setWindowTitle(_("Record Voiceover"))
        self.setModal(False)
        self.rec = rc.VoiceRecorder(self)
        self.rec.level.connect(self._level)
        self.rec.failed.connect(self._failed)
        self.t0 = 0.0
        self.count = 0
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.device = QComboBox()
        from . import player as pl
        # Ses altyapısı yoksa (ya da testte) cihaz taranmaz
        self.devices = rc.input_devices() if (pl.HAVE_AUDIO and not fake) else []
        for d in self.devices:
            self.device.addItem(d.description())
        if not self.devices:
            self.device.addItem(_("No microphone found"))
        form.addRow(_("Input"), self.device)
        self.track = QComboBox()
        form.addRow(_("Track"), self.track)
        self.mute = QCheckBox(_("Mute timeline audio while recording"))
        self.mute.setChecked(True)
        self.mute.setToolTip(_("Prevents the speakers from being recorded again (echo)"))
        form.addRow(self.mute)
        self.countdown = QCheckBox(_("3-second countdown"))
        self.countdown.setChecked(True)
        form.addRow(self.countdown)
        lay.addLayout(form)
        mid = QHBoxLayout()
        self.meter = Meter()
        self.meter.setMinimumHeight(90)
        mid.addWidget(self.meter)
        self.clock = QLabel("00:00")
        self.clock.setAlignment(Qt.AlignCenter)
        self.clock.setStyleSheet("font-size:28px; font-family:monospace;")
        mid.addWidget(self.clock, 1)
        lay.addLayout(mid)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#9aa0a6;")
        lay.addWidget(self.info)
        self.button = QPushButton()
        self.button.setMinimumHeight(36)
        self.button.clicked.connect(lambda: self.toggle())
        lay.addWidget(self.button)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.cd_timer = QTimer(self)
        self.cd_timer.setInterval(1000)
        self.cd_timer.timeout.connect(self._countdown_step)
        self.refresh()

    # ------------------------------------------------------------
    def refresh(self):
        p = self.main.project
        cur = self.track.currentData()
        self.track.clear()
        for ti in p.track_indices("audio"):
            tr = p.tracks[ti]
            if not tr.locked:
                self.track.addItem(tr.name, ti)
        self.track.addItem(_("New audio track"), NEW_TRACK)
        i = self.track.findData(cur)
        self.track.setCurrentIndex(max(0, i))
        busy = self.rec.recording or self.cd_timer.isActive()
        self.button.setText("■ " + _("Stop") if busy else "● " + _("Record"))
        self.button.setStyleSheet("background:#c62828; color:white; font-weight:bold;"
                                  if busy else "")
        self.button.setEnabled(bool(self.devices) or self.fake)
        if not busy:
            self.info.setText(_("Recording starts at the playhead ({tc}) and the video plays "
                                "while you speak. Use headphones for the best result.").format(
                tc=timecode(self.main.timeline.playhead, p.fps)))

    def toggle(self):
        if self.cd_timer.isActive():  # geri sayımı iptal
            self.cd_timer.stop()
            self.clock.setText("00:00")
            self.refresh()
        elif self.rec.recording:
            self.finish()
        else:
            self.begin()

    def begin(self):
        if self.countdown.isChecked() and not self.fake:
            self.count = 3
            self.clock.setText("3")
            self.cd_timer.start()
            self.refresh()
            return
        self._go()

    def _countdown_step(self):
        self.count -= 1
        if self.count > 0:
            self.clock.setText(str(self.count))
            return
        self.cd_timer.stop()
        self._go()

    def _go(self):
        self.main.player.pause()
        self.t0 = self.main.timeline.playhead
        dev = self.devices[self.device.currentIndex()] if self.devices else None
        if not self.rec.start(dev, fake=self.fake):
            self.refresh()
            return
        self.main.player.silent = self.mute.isChecked()
        self.main.player.play()
        self.timer.start(100)
        self.refresh()
        self.info.setText(_("Recording… press Stop when you are done."))

    def _tick(self):
        s = int(self.rec.seconds) if self.fake else int(max(self.rec.seconds, 0))
        self.clock.setText(f"{s // 60:02d}:{s % 60:02d}")

    def finish(self):
        self.timer.stop()
        samples = self.rec.stop()
        self.main.player.pause()
        self.main.player.silent = False
        self.meter.set_levels(0.0, 0.0)
        self.refresh()
        if len(samples) < self.rec.rate * 0.2:
            self.info.setText(_("Nothing was recorded. Check the microphone and try again."))
            return None
        path = rc.next_take_path(self.main.voiceover_folder())
        rc.write_wav(path, samples, self.rec.rate)
        made = self.main.add_voiceover(path, self.t0, self.track.currentData())
        self.refresh()
        self.info.setText(_("Saved: {name}").format(name=path.replace("\\", "/").rsplit("/", 1)[-1]))
        return made

    def _level(self, l, r):
        self.meter.set_levels(l, r)

    def _failed(self, why):
        self.info.setText(_("Could not open the microphone. Check that it is connected and "
                            "that Kurgu is allowed to use it."))

    def closeEvent(self, e):
        if self.cd_timer.isActive():
            self.cd_timer.stop()
        if self.rec.recording:
            self.finish()
        super().closeEvent(e)
