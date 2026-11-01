"""Zaman çizelgesi araçları (Ripple B, Roll N, Slip Y, Slide U) fare ile."""
import os
import subprocess
import sys


def test_timeline_tools_with_mouse(tmp_path, red_video, blue_video):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication; app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
QMessageBox.exec = lambda self: 0
from kurgu.ui.main_window import MainWindow
w = MainWindow(None)
w.resize(1400, 900); w.show(); app.processEvents()
red, blue = w.import_paths([{red_video!r}, {blue_video!r}])
p = w.project
fps = p.fps = 25.0
# kırmızı 0.5–1.5 (0–1 s), mavi 1.0–2.0 kaynağı; arka arkaya
p.place_media(red, 0.0, in_point=0.5, out_point=1.5)
p.place_media(blue, 1.0, in_point=0.0, out_point=1.0)
p.place_media(red, 2.0, in_point=0.0, out_point=1.0)
w.on_edited()
tl = w.timeline
tl.pps = 200.0; tl.scroll = 0.0
V1 = p.track_indices("video")[0]
y = int(tl.row_y(V1) + 20)
def vclips():
    p = w.project
    return sorted([c for c in p.clips if c.track == V1], key=lambda c: c.start)
def drag(x0, x1):
    QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, QPoint(int(x0), y))
    QTest.mouseMove(tl, QPoint(int((x0 + x1) / 2), y))
    QTest.mouseMove(tl, QPoint(int(x1), y))
    QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(int(x1), y))
    app.processEvents()
tl.snapping = False
# --- Ripple (B): kırmızının sonunu 0.2 s geri çek -> sonrakiler 0.2 s sola kayar
w.set_tool("ripple")
a, b, c = vclips()
drag(tl.x_of(a.end) - 2, tl.x_of(a.end - 0.2) - 2)
a, b, c = vclips()
assert abs(a.end - 0.8) < 0.02, a
assert abs(b.start - 0.8) < 0.02 and abs(c.start - 1.8) < 0.02, (b, c)
w.undo(); a, b, c = vclips()
assert abs(a.end - 1.0) < 1e-6 and abs(c.start - 2.0) < 1e-6
# --- Roll (N): mavinin başında kaynak payı yok (in=0) -> sola gidemez
w.set_tool("roll")
drag(tl.x_of(1.0) - 3, tl.x_of(0.8) - 3)
a, b, c = vclips()
assert abs(a.end - 1.0) < 1e-6 and abs(b.start - 1.0) < 1e-6, (a, b)
# sağa 0.2 s: kırmızı uzar, mavi kısalır, toplam süre aynı
drag(tl.x_of(1.0) - 3, tl.x_of(1.2) - 3)
a, b, c = vclips()
assert abs(a.end - 1.2) < 0.02 and abs(b.start - 1.2) < 0.02, (a, b)
assert abs(b.end - 2.0) < 1e-6 and abs(b.in_point - 0.2) < 0.02 and abs(c.start - 2.0) < 1e-6
w.undo()
a, b, c = vclips()
assert abs(b.start - 1.0) < 1e-6
# --- Slip (Y): kırmızı (in 0.5) -> 0.2 s sola sürükle = kaynak ileri (+0.2)
w.set_tool("slip")
a, b, c = vclips()
drag(tl.x_of(0.5), tl.x_of(0.3))
a, b, c = vclips()
assert abs(a.start) < 1e-6 and abs(a.in_point - 0.7) < 0.02 and abs(a.duration - 1.0) < 1e-6, a
w.undo()
# --- Slide (U): mavi 0.2 s sağa -> kırmızı uzar, son kırmızı kısalır
w.set_tool("slide")
a, b, c = vclips()
drag(tl.x_of(1.5), tl.x_of(1.7))
a, b, c = vclips()
assert abs(b.start - 1.2) < 0.02 and abs(b.duration - 1.0) < 1e-6, b
assert abs(a.end - 1.2) < 0.02 and abs(c.start - 2.2) < 0.02 and abs(c.end - 3.0) < 1e-6, (a, c)
# Bağlı sesler de aynı şekilde
p = w.project
auds = sorted([x for x in p.clips if not p.track_is_video(x.track)], key=lambda x: x.start)
assert abs(auds[1].start - 1.2) < 0.02, auds
w.undo()
# Araç düğmeleri ve menü eşzamanlı
w.set_tool("razor")
assert w.timeline_panel.tool_buttons["razor"].isChecked()
assert not w.timeline_panel.tool_buttons["slide"].isChecked()
assert w.tool_actions["razor"].isChecked()
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
