"""Kaynak monitörü: yükle, I/O işaretle, Ekle/Üzerine Yaz, sürükleme verisi."""
import os
import subprocess
import sys

from kurgu.ui.timeline import decode_source_drag, encode_source_drag


def test_drag_payload_roundtrip():
    assert decode_source_drag(b"abc") == ("abc", None, None)
    assert decode_source_drag(encode_source_drag("abc", 0.5, None)) == ("abc", 0.5, None)
    assert decode_source_drag(encode_source_drag("abc", 0.5, 1.25)) == ("abc", 0.5, 1.25)


def test_source_monitor_insert_overwrite(tmp_path, red_video, blue_video):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication; app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from kurgu.ui.main_window import MainWindow
from kurgu.ui.player import Player
from PySide6.QtWidgets import QMessageBox
QMessageBox.exec = lambda self: 0
w = MainWindow(None)
w.resize(1200, 800); w.show(); app.processEvents()
red, blue = w.import_paths([{red_video!r}, {blue_video!r}])
src = w.source
w.open_in_source(red)
app.processEvents()
assert src.media is red and abs(src.duration() - red.duration) < 0.05
assert src.view.image is not None
# I/O: 0.4 – 1.2 s
src.seek(0.4); src.mark_in(); src.seek(1.2); src.mark_out()
assert w.source_marks[red.id] == (0.4, 1.2), w.source_marks
# Üzerine yaz: oynatma kafası 0'da
w.timeline.set_playhead(0.0)
src.edit("overwrite")
p = w.project
v = [c for c in p.clips if p.track_is_video(c.track)]
a = [c for c in p.clips if not p.track_is_video(c.track)]
assert len(v) == 1 and len(a) == 1, p.clips
assert abs(v[0].in_point - 0.4) < 1e-6 and abs(v[0].duration - 0.8) < 1e-6
assert abs(w.timeline.playhead - 0.8) < 1e-6
# Ekle: mavinin tamamı 0.4'e -> kırmızı bölünür, sağ parça itilir
w.open_in_source(blue)
w.timeline.set_playhead(0.4)
src.edit("insert")
v = sorted([c for c in p.clips if p.track_is_video(c.track)], key=lambda c: c.start)
assert len(v) == 3, v
assert v[1].media_id == blue.id and abs(v[1].start - 0.4) < 1e-6
assert abs(v[2].start - (0.4 + blue.duration)) < 1e-3
# Odak kaynak monitöründeyken Space onun oynatıcısını kullanır
src.setFocus(); app.processEvents()
assert w.source_active() and w._player() is src.player
w.timeline.setFocus(); app.processEvents()
assert not w.source_active() and w._player() is w.player
# Medya silinince monitör boşalır
w.remove_media([blue.id])
assert src.media is None
w.undo()
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
