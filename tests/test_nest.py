"""İç içe sekanslar: oluşturma, çizim (saydam), ses, düzenleme, kaydetme, arayüz."""
import os
import subprocess
import sys

import numpy as np
import pytest

from kurgu.media import SourcePool, probe
from kurgu.model import Project
from kurgu.render import Compositor


@pytest.fixture(scope="module")
def vids(tmp_path_factory):
    sys.path.insert(0, os.path.dirname(__file__))
    from conftest import make_video
    d = tmp_path_factory.mktemp("n")
    return (make_video(d / "r.mp4", seconds=2, w=320, h=180, color=(220, 30, 30)),
            make_video(d / "b.mp4", seconds=2, w=320, h=180, color=(30, 30, 220)))


def px(img, x, y):
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def _proj(vids):
    red, blue = vids
    p = Project(320, 180, 25.0)
    a = p.place_media(p.add_media(probe(red)), 0.0)
    b = p.place_media(p.add_media(probe(blue)), 2.0)
    return p, a, b


def test_nest_replaces_clips_and_renders(qapp, vids):
    p, a, b = _proj(vids)
    made = p.nest_clips([a[0].id, b[0].id], "İç 1")
    assert {c.kind for c in p.clips} == {"nest"} and len(made) == 2
    v = [c for c in made if p.track_is_video(c.track)][0]
    assert abs(v.duration - 4.0) < 1e-6 and v.link_id == made[1].link_id
    comp = Compositor(p, SourcePool())
    assert px(comp.compose(1.0, 320, 180), 160, 120)[0] > 180
    assert px(comp.compose(3.0, 320, 180), 160, 120)[2] > 180
    assert np.abs(comp.mix(1.0, 4800)).max() > 0.1  # iç sekansın sesi
    # Kırpma ve taşıma: iç içe klip normal klip gibi
    p.trim(v, "in", 1.0)
    assert abs(v.in_point - 1.0) < 1e-6
    assert px(Compositor(p, SourcePool()).compose(2.5, 320, 180), 160, 120)[2] > 180


def test_nest_is_transparent_over_lower_tracks(qapp, vids):
    red, blue = vids
    p = Project(320, 180, 25.0)
    p.place_media(p.add_media(probe(blue)), 0.0)  # V1 mavi
    v2 = p.track_indices("video")[1]
    top = [c for c in p.place_media(p.add_media(probe(red)), 0.0, v2)
           if p.track_is_video(c.track)][0]
    top.scale = 0.5  # küçük kırmızı kutu
    nest = p.nest_clips([top.id], "Kutu")
    nv = [c for c in nest if p.track_is_video(c.track)][0]
    assert nv.track == v2
    img = Compositor(p, SourcePool()).compose(1.0, 320, 180)
    assert px(img, 160, 90)[0] > 180 and px(img, 10, 170)[2] > 180  # boş yerde alttaki görünür


def test_edit_inside_store_and_save(qapp, vids, tmp_path):
    p, a, b = _proj(vids)
    v = [c for c in p.nest_clips([a[0].id, b[0].id], "İç") if p.track_is_video(c.track)][0]
    child = p.nest_project(v.nest)
    assert child.media is p.media and len(child.clips) == 4
    blue_clips = [c for c in child.clips if c.start >= 2.0]
    child.clips = [c for c in child.clips if c not in blue_clips]
    h0 = p.nests[v.nest]["hash"]
    p.store_nest(v.nest, child)
    assert p.nests[v.nest]["hash"] != h0
    assert max(px(Compositor(p, SourcePool()).compose(3.0, 320, 180), 160, 120)) < 20
    f = str(tmp_path / "n.kurgu")
    p.save(f)
    q = Project.load(f)
    assert q.nests[v.nest]["name"] == "İç" and q.nest_duration(v.nest) == pytest.approx(2.0)
    # Çözme: klipler ana sekansa geri gelir
    qv = [c for c in q.clips if c.kind == "nest" and q.track_is_video(c.track)][0]
    back = q.unnest(qv)
    assert len(back) == 2 and not any(c.kind == "nest" for c in q.clips)


def test_nest_in_nest(qapp, vids):
    p, a, b = _proj(vids)
    inner = p.nest_clips([a[0].id], "İç")
    outer = p.nest_clips([inner[0].id, b[0].id], "Dış")
    assert len(p.nests) == 2
    comp = Compositor(p, SourcePool())
    assert px(comp.compose(1.0, 320, 180), 160, 120)[0] > 180
    assert px(comp.compose(3.0, 320, 180), 160, 120)[2] > 180
    _ = outer


def test_nest_gui(tmp_path, vids):
    red, blue = vids
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication, QMessageBox; app = QApplication([])
QMessageBox.exec = lambda self: 0
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from kurgu.ui.main_window import MainWindow
w = MainWindow(None); w.resize(1400, 900); w.show(); app.processEvents()
r, b = w.import_paths([{red!r}, {blue!r}])
w.place_media(r, 0.0, None); w.place_media(b, 2.0, None)
w.timeline.selected = {{c.id for c in w.project.clips}}
made = w.nest_selected("Sahne 1")
assert len(made) == 2 and all(c.kind == 'nest' for c in w.project.clips)
nv = [c for c in made if w.project.track_is_video(c.track)][0]
w.enter_nest(nv)
assert w.nest_stack and len(w.project.clips) == 4
assert w.timeline_panel.crumbs.isVisibleTo(w.timeline_panel)
assert 'Sahne 1' in w.timeline_panel.path.text()
# İçeride düzenle: mavi klipleri sil
w.timeline.selected = {{c.id for c in w.project.clips if c.start >= 2.0}}
w.delete_selected()
assert len(w.project.clips) == 2
# İçerideyken kaydet: ana proje kaydedilir
w.project_path = None
root = w.root_project(); root.path = {str(tmp_path / 'p.kurgu')!r}
assert w.save()
from kurgu.model import Project
q = Project.load({str(tmp_path / 'p.kurgu')!r})
assert q.nest_duration(nv.nest) == 2.0 and all(c.kind == 'nest' for c in q.clips)
w.exit_nest()
assert not w.nest_stack and not w.timeline_panel.crumbs.isVisibleTo(w.timeline_panel)
assert abs(w.project.nest_duration(nv.nest) - 2.0) < 1e-6
w.undo()  # dışarıda tek adım: içerideki silme geri gelir
assert abs(w.project.nest_duration(nv.nest) - 4.0) < 1e-6
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
