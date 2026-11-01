"""Seslendirme kaydı: WAV yazma, ham bayt çevirme, pencere ile zaman çizelgesine koyma."""
import os
import subprocess
import sys
import wave

import numpy as np

from kurgu import recorder as rc


def test_write_wav_and_names(tmp_path):
    t = np.arange(4800) / 48000
    s = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = rc.next_take_path(str(tmp_path / "vo"))
    assert p.endswith("Voiceover 001.wav")
    rc.write_wav(p, s, 48000)
    assert rc.next_take_path(str(tmp_path / "vo")).endswith("Voiceover 002.wav")
    with wave.open(p) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == \
            (1, 2, 48000, 4800)
        a = np.frombuffer(w.readframes(4800), "<i2") / 32767
    assert abs(a.max() - 0.5) < 0.01


def test_raw_conversion_and_partial_samples(qapp):
    r = rc.VoiceRecorder()
    r._fmt, r.channels, r.rate = "Int16", 2, 48000
    r.recording = True
    levels = []
    r.level.connect(lambda a, b: levels.append((a, b)))
    raw = np.array([16384, -8192] * 10, "<i2").tobytes()
    r.feed_raw(raw[:7])   # yarım örnek: bekletilir
    r.feed_raw(raw[7:])
    out = r.stop()
    assert out.shape == (10, 2)
    assert np.allclose(out[:, 0], 0.5) and np.allclose(out[:, 1], -0.25)
    assert levels and abs(levels[-1][0] - 0.5) < 1e-6
    for name, arr in (("Float", np.array([0.25], "<f4")), ("Int32", np.array([2 ** 30], "<i4")),
                      ("UInt8", np.array([192], np.uint8))):
        assert abs(rc.to_float(arr.tobytes(), name, 1)[0, 0] - (0.25 if name == "Float" else 0.5)) < 1e-3


def test_voiceover_dialog_places_clip(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r}); sys.path.insert(0, {root!r} + '/tests')
from PySide6.QtWidgets import QApplication, QMessageBox; app = QApplication([])
QMessageBox.exec = lambda self: 0
import numpy as np
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from conftest import make_video
from kurgu.ui.main_window import MainWindow
v = make_video({str(tmp_path / 'v.mp4')!r}, seconds=3, w=320, h=180)
w = MainWindow(None); w.resize(1400, 900); w.show(); app.processEvents()
m, = w.import_paths([v]); w.place_media(m, 0.0, None)
w.root_project().path = {str(tmp_path / 'proj' / 'p.kurgu')!r}
w.timeline.set_playhead(1.0)
dlg = w.voiceover_dialog(fake=True)
assert dlg.isVisible() and dlg.button.isEnabled()
a1 = w.project.track_indices('audio')[0]
dlg.track.setCurrentIndex(dlg.track.findData(-1))  # yeni ses izi
n_tracks = len(w.project.tracks)
dlg.button.click(); app.processEvents()
assert dlg.rec.recording and w.player.playing and w.player.silent
t = np.arange(48000) / 48000
dlg.rec.feed((0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32))
dlg.button.click(); app.processEvents()
assert not dlg.rec.recording and not w.player.playing and not w.player.silent
assert len(w.project.tracks) == n_tracks + 1
vo = [c for c in w.project.clips if c.track == n_tracks]
assert len(vo) == 1 and abs(vo[0].start - 1.0) < 1e-6 and abs(vo[0].duration - 1.0) < 0.05
import os
assert os.path.exists({str(tmp_path / 'proj' / 'Voiceover' / 'Voiceover 001.wav')!r})
# Ses gerçekten miksajda
from kurgu.media import SourcePool
from kurgu.render import Compositor
mix = Compositor(w.project, SourcePool()).mix(1.5, 4800)
assert np.abs(mix).max() > 0.1
# Tek geri alma adımı: iz ve klip birlikte gider
w.undo()
assert len(w.project.tracks) == n_tracks and not any(c.track == n_tracks for c in w.project.clips)
# Var olan ize ikinci kayıt
dlg.refresh(); dlg.track.setCurrentIndex(dlg.track.findData(a1))
w.timeline.set_playhead(0.5)
dlg.button.click(); dlg.rec.feed(np.zeros(24000, np.float32) + 0.1); dlg.button.click()
assert any(c.track == a1 and abs(c.start - 0.5) < 1e-6 and c.media_id != m.id
           for c in w.project.clips)
# Çok kısa kayıt: hiçbir şey eklenmez
k = len(w.project.clips)
dlg.button.click(); dlg.button.click()
assert len(w.project.clips) == k and 'Nothing' in dlg.info.text()
dlg.close(); w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path), KURGU_LANG="en")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
