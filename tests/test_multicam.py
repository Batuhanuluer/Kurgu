"""Çoklu kamera: sesten eşitleme, gruba yerleştirme, 1-9 ile kesme, render."""
import os
import subprocess
import sys

import numpy as np

from kurgu import multicam as mcm
from kurgu.media import SourcePool, probe
from kurgu.model import Project
from kurgu.render import Compositor


def test_audio_sync_finds_offsets(cameras):
    paths, truth = cameras
    offs, conf = mcm.sync_by_audio(paths)
    for o, t in zip(offs, truth):
        assert abs(o - t) < 0.01, (offs, truth)
    assert min(conf[1:]) > 0.15


def _project(cameras, qapp):
    paths, truth = cameras
    p = Project(160, 90, 25.0)
    items = [p.add_media(probe(x)) for x in paths]
    gid = p.create_multicam("Konser", items, truth, audio=0)
    return p, items, gid


def _color(p, t):
    comp = Compositor(p, SourcePool())
    img = comp.compose(t, 160, 90)
    c = img.pixelColor(80, 45)
    return c.red(), c.green(), c.blue()


def test_place_switch_cut_and_render(cameras, qapp):
    p, items, gid = _project(cameras, qapp)
    dur = p.multicam_duration(p.multicams[gid])
    assert abs(dur - 9.24) < 0.05  # cam2 1.24'te başlar, 8 sn sürer
    made = p.place_multicam(gid, 0.0)
    v = [c for c in made if p.track_is_video(c.track)][0]
    a = [c for c in made if not p.track_is_video(c.track)][0]
    assert v.mc == gid and a.mc == gid and v.link_id == a.link_id
    assert _color(p, 0.5)[0] > 150  # kamera 1 kırmızı
    # 2. saniyede kamera 2'ye kes (tuş "2")
    p.multicam_cut(2.0, 1)
    vids = sorted([c for c in p.clips if p.track_is_video(c.track)], key=lambda c: c.start)
    assert len(vids) == 2 and vids[1].mc_angle == 1 and abs(vids[1].start - 2.0) < 1e-6
    r, g, b = _color(p, 3.0)
    assert g > 150 and r < 100
    # Kamera 2'nin 1.0 s'sinde (grup zamanı 1.0 < 1.24) henüz görüntü yok: siyah
    p.multicam_switch(vids[0], 1)
    assert max(_color(p, 0.5)) < 20
    p.multicam_switch(vids[0], 0)
    # Kesimler yerinde: açı değişince başlangıç/bitiş değişmez
    p.multicam_cut(5.0, 2)
    vids = sorted([c for c in p.clips if p.track_is_video(c.track)], key=lambda c: c.start)
    assert [round(c.start, 3) for c in vids] == [0.0, 2.0, 5.0]
    assert _color(p, 5.5)[2] > 150
    # Kamera 3 6.52'de bitiyor: 7. saniyede siyah
    assert max(_color(p, 7.0)) < 20
    # Ses bölünür ama kayma doğru: grup zamanı = ses kamerasının zamanı (ofset 0)
    auds = [c for c in p.clips if not p.track_is_video(c.track)]
    assert len(auds) == 3 and all(c.mc_angle == 0 for c in auds)
    # Aynı açıya basmak kesim yapmaz
    n = len(p.clips)
    p.multicam_cut(5.5, 2)
    assert len(p.clips) == n


def test_multicam_audio_offset_in_mix(cameras, qapp):
    paths, truth = cameras
    p = Project(160, 90, 25.0)
    items = [p.add_media(probe(x)) for x in paths]
    gid = p.create_multicam("K", items, truth, audio=1)  # ses kamera 2'den
    p.place_multicam(gid, 0.0)
    comp = Compositor(p, SourcePool())
    # Grup zamanı 0.5'te kamera 2 henüz kaydetmiyordu: sessiz; 3.0'da ses var
    assert np.abs(comp.mix(0.5, 2400)).max() < 1e-4
    assert np.abs(comp.mix(1.5, 48000 * 3)).max() > 0.05


def test_multicam_survives_save_load(cameras, qapp, tmp_path):
    p, items, gid = _project(cameras, qapp)
    p.place_multicam(gid, 1.0)
    p.multicam_cut(3.0, 2)
    f = str(tmp_path / "mc.kurgu")
    p.save(f)
    q = Project.load(f)
    assert q.multicams[gid]["angles"][1]["offset"] == 1.24
    vids = sorted([c for c in q.clips if q.track_is_video(c.track)], key=lambda c: c.start)
    assert [c.mc_angle for c in vids] == [0, 2] and q.media_offset(vids[1]) == 0.52


def test_multicam_gui(tmp_path, cameras):
    paths, truth = cameras
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication; app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from PySide6.QtWidgets import QMessageBox, QDialog
QMessageBox.exec = lambda self: 0
QDialog.exec = lambda self: 1          # oluşturma penceresi: varsayılanlarla Tamam
from kurgu.ui.main_window import MainWindow
w = MainWindow(None); w.resize(1500, 950); w.show(); app.processEvents()
items = w.import_paths({paths!r})
gid = w.create_multicam_dialog(items)
p = w.project
assert gid in p.multicams
offs = [a['offset'] for a in p.multicams[gid]['angles']]
assert all(abs(o - t) < 0.01 for o, t in zip(offs, {truth!r})), offs
assert any(c.mc == gid for c in p.clips)                      # oynatma kafasına eklendi
assert any(str(w.bin.item(i).data(0x0100)).startswith('mc:') for i in range(w.bin.count()))
# Çoklu kamera görünümü: ızgara 3 hücre
w.toggle_multicam_view(True); w.timeline.set_playhead(2.0); app.processEvents()
assert w.source.mc_mode and len(w.source.mc_cells) == 3 and w.source.view.image is not None
# Klavye: 2 -> kamera 2
w.multicam_key(1)
v = sorted([c for c in w.project.clips if c.mc and w.project.track_is_video(c.track)], key=lambda c: c.start)
assert [c.mc_angle for c in v] == [0, 1], [c.mc_angle for c in v]
# Izgarada 3. hücreye tıkla (oynatma kafası 4.0)
w.timeline.set_playhead(4.0); app.processEvents()
from PySide6.QtCore import QPointF
r = w.source.view.target_rect()
cx, cy, cw, ch = w.source.mc_cells[2]
pos = QPointF(r.left() + (cx + cw / 2) * r.width(), r.top() + (cy + ch / 2) * r.height())
i = w.source.mc_cell_at(pos); assert i == 2, i
w.multicam_key(i)
v = sorted([c for c in w.project.clips if c.mc and w.project.track_is_video(c.track)], key=lambda c: c.start)
assert [c.mc_angle for c in v] == [0, 1, 2]
w.undo()
v = [c for c in w.project.clips if c.mc and w.project.track_is_video(c.track)]
assert len(v) == 2
w.toggle_multicam_view(False); assert not w.source.mc_mode
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
