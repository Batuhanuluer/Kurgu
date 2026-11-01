"""Program monitöründe tıklayarak seçme ve sürükleme: başlık ve altyazı konumu."""
import os
import subprocess
import sys


def test_click_select_and_drag_text_and_caption(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r}); sys.path.insert(0, {root!r} + '/tests')
from PySide6.QtWidgets import QApplication, QMessageBox; app = QApplication([])
QMessageBox.exec = lambda self: 0
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from PySide6.QtCore import QPointF
from conftest import make_video
from kurgu.ui.main_window import MainWindow
v = make_video({str(tmp_path / 'v.mp4')!r}, seconds=3, w=320, h=180)
w = MainWindow(None); w.resize(1400, 900); w.show(); app.processEvents()
m, = w.import_paths([v]); w.place_media(m, 0.0, None)
bg = [c for c in w.project.clips if w.project.track_is_video(c.track)][0]
w.project.add_text('Başlık', 0.0)
w.project.add_captions([(0.0, 2.5, 'Merhaba dünya')])
w.on_edited()
title = [c for c in w.project.clips if c.kind == 'text'][0]
cap = [c for c in w.project.clips if c.kind == 'caption'][0]
w.timeline.selected = {{bg.id}}; w._on_selection()   # arka plan seçili
w.timeline.set_playhead(1.0); app.processEvents()
mon = w.monitor
# 1) Yazıya tıkla: seçilir ve sürüklenir (seçili arka plan değil)
g = mon.clip_geom(title); c = g[0]
e = mon.begin_edit(c)
assert e is not None and e['clip'] is title and e['mode'] == 'move', e
assert w.timeline.selected == {{title.id}}
mon.drag_edit(e, QPointF(c.x() + 60, c.y() + 30)); mon.end_edit()
k = g[2]
assert abs(title.pos_x - round(60 / k)) <= 1 and abs(title.pos_y - round(30 / k)) <= 1, (title.pos_x, title.pos_y)
assert abs(bg.pos_x) < 1e-6
w.undo(); title = [c for c in w.project.clips if c.kind == 'text'][0]
assert abs(title.pos_x) < 1e-6
# 2) Altyazıya tıkla ve yukarı sürükle: iz stilinin konumu değişir
cap = [c for c in w.project.clips if c.kind == 'caption'][0]
g = mon.clip_geom(cap); c = g[0]
assert mon.pick_clip(c) is cap
e = mon.begin_edit(c)
assert e is not None and e['mode'] == 'caption'
r = mon.view.target_rect()
mon.drag_edit(e, QPointF(c.x() + 3, c.y() - r.height() * 0.3)); mon.end_edit()
st = w.project.tracks[cap.track].style
assert abs(st['y'] - 0.6) < 0.02 and st['x'] == 0.5, st   # ortaya yapıştı
assert w.captions_panel.pos.slider.value() if hasattr(w.captions_panel.pos, 'slider') else True
mon.view.repaint()
w.undo()
assert 'y' not in w.project.tracks[cap.track].style or abs(w.project.tracks[cap.track].style['y'] - 0.9) < 1e-6
# 3) Boş yere tıklamak hiçbir şey yapmaz
assert mon.begin_edit(QPointF(2, 2)) is None
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
