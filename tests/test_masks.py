"""Maskeler, karıştırma modları, iz matı."""
import os
import subprocess
import sys

import numpy as np
import pytest

from kurgu import masks as mk
from kurgu.media import SourcePool, probe
from kurgu.model import Project
from kurgu.render import Compositor


@pytest.fixture(scope="module")
def two_layers(tmp_path_factory):
    sys.path.insert(0, os.path.dirname(__file__))
    from conftest import make_video
    d = tmp_path_factory.mktemp("mk")
    return (make_video(d / "r.mp4", seconds=1, w=320, h=180, color=(220, 30, 30)),
            make_video(d / "b.mp4", seconds=1, w=320, h=180, color=(30, 30, 220)))


def _setup(two_layers):
    red, blue = two_layers
    p = Project(320, 180, 25.0)
    p.place_media(p.add_media(probe(blue)), 0.0)
    v2 = p.track_indices("video")[1]
    top = [c for c in p.place_media(p.add_media(probe(red)), 0.0, v2)
           if p.track_is_video(c.track)][0]
    return p, top


def px(img, x, y):
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def test_mask_alpha_math(qapp):
    m = mk.new_mask("rect")
    m["feather"] = 0
    a = mk.mask_alpha([m], 100, 100)
    assert a[50, 50] > 0.99 and a[5, 5] < 0.01
    m["invert"] = True
    assert mk.mask_alpha([m], 100, 100)[50, 50] < 0.01
    m["invert"] = False
    m["expansion"] = 20
    assert mk.mask_alpha([m], 100, 100)[50, 10] > 0.99  # genişledi
    m["expansion"], m["feather"] = 0, 30
    edge = mk.mask_alpha([m], 100, 100)[50, 25]
    assert 0.2 < edge < 0.8  # yumuşak kenar
    a1, a2 = mk.new_mask("rect"), mk.new_mask("ellipse")
    a1["feather"] = a2["feather"] = 0
    a2["mode"] = "subtract"
    both = mk.mask_alpha([a1, a2], 100, 100)
    assert both[50, 50] < 0.01 and both[27, 27] > 0.99  # köşe kalır, ortası oyulur
    poly = mk.new_mask("polygon")
    poly["points"], poly["feather"] = [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]], 0
    t = mk.mask_alpha([poly], 100, 100)
    assert t[30, 50] > 0.99 and t[80, 10] < 0.01


def test_masked_clip_renders(qapp, two_layers):
    p, top = _setup(two_layers)
    top.masks = [mk.new_mask("ellipse")]
    top.masks[0]["feather"] = 0
    comp = Compositor(p, SourcePool())
    img = comp.compose(0.5, 320, 180)
    assert px(img, 160, 90)[0] > 180 and px(img, 5, 170)[2] > 180  # içi kırmızı, dışı mavi
    deep = Compositor(p, SourcePool(), deep=True).compose(0.5, 320, 180)
    assert px(deep, 160, 90)[0] > 180 and px(deep, 5, 170)[2] > 180


@pytest.mark.parametrize("mode,check", [
    ("multiply", lambda c: max(c) < 60), ("screen", lambda c: c[0] > 200 and c[2] > 200),
    ("difference", lambda c: c[0] > 150 and c[2] > 150), ("add", lambda c: c[0] > 200),
])
def test_blend_modes(qapp, two_layers, mode, check):
    p, top = _setup(two_layers)
    top.blend = mode
    assert check(px(Compositor(p, SourcePool()).compose(0.5, 320, 180), 160, 90))


def test_track_matte(qapp, two_layers):
    p, top = _setup(two_layers)
    p.add_track("video")
    v3 = p.track_indices("video")[2]
    t = p.add_text("■", 0.0, v3)
    t.font_size = 160
    p.tracks[v3].muted = True
    top.matte = {"track": v3, "type": "alpha"}
    img = Compositor(p, SourcePool()).compose(0.5, 320, 180)
    assert px(img, 160, 90)[0] > 180 and px(img, 5, 170)[2] > 180
    top.matte["invert"] = True
    img = Compositor(p, SourcePool()).compose(0.5, 320, 180)
    assert px(img, 160, 90)[2] > 180 and px(img, 5, 170)[0] > 180
    # Önbellek imzası mat izi değişince değişir
    from kurgu.rendercache import FrameSig
    s1 = FrameSig(p)(0.5)
    t.font_size = 40
    assert FrameSig(p)(0.5) != s1


def test_masks_survive_save(qapp, two_layers, tmp_path):
    p, top = _setup(two_layers)
    top.masks = [mk.new_mask("polygon")]
    top.blend = "screen"
    top.matte = {"track": 0, "type": "luma", "invert": True}
    f = str(tmp_path / "m.kurgu")
    p.save(f)
    q = Project.load(f)
    c = q.clip_by_id(top.id)
    assert c.masks[0]["points"] == top.masks[0]["points"] and c.blend == "screen"
    assert c.matte == {"track": 0, "type": "luma", "invert": True}


def test_mask_gui(tmp_path, two_layers):
    red, blue = two_layers
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication; app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from PySide6.QtWidgets import QMessageBox
QMessageBox.exec = lambda self: 0
from PySide6.QtCore import QPointF
from kurgu.ui.main_window import MainWindow
w = MainWindow(None); w.resize(1500, 950); w.show(); app.processEvents()
b, r = w.import_paths([{blue!r}, {red!r}])
w.place_media(b, 0.0, None)
v2 = w.project.track_indices('video')[1]
w.place_media(r, 0.0, v2)
top = [c for c in w.project.clips if c.track == v2][0]
w.timeline.selected = {{top.id}}; w._on_selection(); w.timeline.set_playhead(0.5); app.processEvents()
mp = w.inspector.mask_panel
assert mp.isVisibleTo(w.inspector)
mp.add_mask('ellipse')
assert len(top.masks) == 1 and w.monitor.sel_mask == top.masks[0]['id']
mon = w.monitor
g = mon.clip_geom(top); xf = mon.mask_xform(g)
# Sağ alt köşe tutamacını sürükle: kutu büyür
h = xf.map(QPointF(0.75, 0.75)); dst = xf.map(QPointF(0.9, 0.95))
e = mon.begin_edit(h); assert e is not None and e['mode'] == 'mask_pt'
mon.drag_edit(e, dst); mon.end_edit()
assert abs(top.masks[0]['box'][2] - 0.9) < 0.01 and abs(top.masks[0]['box'][3] - 0.95) < 0.01
# İçinden sürükle: taşı
c0 = xf.map(QPointF(0.5, 0.5)); e = mon.begin_edit(c0)
assert e['mode'] == 'mask_move'
mon.drag_edit(e, xf.map(QPointF(0.4, 0.5))); mon.end_edit()
assert abs(top.masks[0]['box'][0] - 0.15) < 0.01
w.undo(); top = w.project.clip_by_id(top.id)
assert abs(top.masks[0]['box'][0] - 0.25) < 0.01
# Kalem: üç nokta + ilk noktaya tıkla
w.timeline.selected = {{top.id}}; w._on_selection()
w.inspector.mask_panel.add_mask('polygon'); assert mon.pen is not None
for uv in ((0.2, 0.2), (0.8, 0.2), (0.5, 0.8), (0.2, 0.2)):
    mon.begin_edit(xf.map(QPointF(*uv)))
top = w.project.clip_by_id(top.id)
assert mon.pen is None and top.masks[-1]['shape'] == 'polygon' and len(top.masks[-1]['points']) == 3
mp = w.inspector.mask_panel
mp.blend.setCurrentIndex(mp.blend.findData('screen')); assert top.blend == 'screen'
mp.matte.setCurrentIndex(mp.matte.findData('0:luma')); assert top.matte['type'] == 'luma'
w.monitor.view.repaint()
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


# ------------------------------------------------------------ maske yolu ve tekerlek animasyonu
def test_keyframes_interpolate_lists():
    from kurgu import keyframes as kf
    ks = [[0.0, [0.0, 0.0, 0.5, 0.5], "linear"], [2.0, [0.5, 0.5, 1.0, 1.0], "linear"]]
    assert kf.eval_keys(ks, 1.0) == [0.25, 0.25, 0.75, 0.75]
    pts = [[0.0, [[0, 0], [1, 0], [1, 1]], "linear"], [1.0, [[1, 1], [2, 1], [2, 2]], "ease"]]
    assert kf.eval_keys(pts, 0.5) == [[0.5, 0.5], [1.5, 0.5], [1.5, 1.5]]
    # Nokta sayısı değişirse önceki şekil tutulur
    bad = [[0.0, [[0, 0]], "linear"], [1.0, [[1, 1], [2, 2]], "linear"]]
    assert kf.eval_keys(bad, 0.5) == [[0, 0]]
    v = kf.eval_keys(ks, 5.0)
    v[0] = 99  # dönen değer anahtarı değiştirmemeli
    assert ks[-1][1][0] == 0.5


def test_mask_path_animation_renders(qapp, tmp_path):
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from conftest import make_video
    from kurgu import keyframes as kf
    from kurgu.masks import new_mask
    from kurgu.media import SourcePool, probe
    from kurgu.model import Project
    from kurgu.render import Compositor
    v = make_video(tmp_path / "w.mp4", seconds=2, w=320, h=180, color=(240, 240, 240))
    p = Project(320, 180, 25.0)
    c = [x for x in p.place_media(p.add_media(probe(v)), 0.0) if p.track_is_video(x.track)][0]
    m = new_mask("rect")
    m["feather"] = 0.0
    m["box"] = [0.0, 0.0, 0.3, 1.0]  # sol şerit
    c.masks.append(m)
    prop = f"mask.{m['id']}.box"
    kf.set_key(c, prop, c.source_time(0.0), [0.0, 0.0, 0.3, 1.0], p.fps)
    kf.set_key(c, prop, c.source_time(1.6), [0.7, 0.0, 1.0, 1.0], p.fps)  # sağ şerit
    comp = Compositor(p, SourcePool())

    def lum(img, x):
        return img.pixelColor(x, 90).red()
    a = comp.compose(0.0, 320, 180)
    assert lum(a, 30) > 200 and lum(a, 290) < 30
    b = comp.compose(1.6, 320, 180)
    assert lum(b, 30) < 30 and lum(b, 290) > 200
    mid = comp.compose(0.8, 320, 180)
    assert lum(mid, 160) > 200 and lum(mid, 30) < 30  # ortada
    # Statik değer ve kronometre kapatma: o anki şekil kalır
    kf.toggle_animation(c, prop, c.source_time(0.8), p.fps)
    assert not c.keyframes and c.masks[0]["box"] == pytest.approx([0.35, 0, 0.65, 1.0])


def test_color_wheel_animation(qapp):
    from kurgu import effects as fx
    from kurgu import keyframes as kf
    from kurgu.model import Clip
    c = Clip(id="c", media_id="m", track=0, start=0.0, in_point=0.0, out_point=2.0)
    e = fx.new_effect("lumetri")
    c.effects.append(e)
    prop = kf.fx_prop(e, "gain")
    kf.set_key(c, prop, 0.0, [0.0, 0.0], 25)
    kf.set_key(c, prop, 2.0, [0.4, -0.2], 25)
    a = kf.animated(c, 1.0)
    g = [x for x in a.effects if x["type"] == "lumetri"][0]["params"]["gain"]
    assert g == pytest.approx([0.2, -0.1])
    assert e["params"]["gain"] == [0.0, 0.0]  # orijinal değişmez


def test_mask_and_wheel_keyframes_gui(tmp_path, two_layers):
    red, blue = two_layers
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication, QMessageBox; app = QApplication([])
QMessageBox.exec = lambda self: 0
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from PySide6.QtCore import QPointF
from kurgu import keyframes as kf
from kurgu.ui.main_window import MainWindow
w = MainWindow(None); w.resize(1500, 950); w.show(); app.processEvents()
b, = w.import_paths([{blue!r}])
w.place_media(b, 0.0, None)
top = [c for c in w.project.clips if w.project.track_is_video(c.track)][0]
w.timeline.selected = {{top.id}}; w._on_selection(); w.timeline.set_playhead(0.1); app.processEvents()
mp = w.inspector.mask_panel
mp.add_mask('rect')
mid = top.masks[0]['id']
row = mp.rows[0]
path = row.anim_rows[0]
path.watch.click(); app.processEvents()           # kronometre: ilk anahtar
assert kf.is_animated(top, f'mask.{{mid}}.box'), top.keyframes
assert path.key.text() == '◆'
w.timeline.set_playhead(0.8); app.processEvents()
assert mp.rows[0].anim_rows[0].key.text() == '◇'
mon = w.monitor
g = mon.clip_geom(top); xf = mon.mask_xform(g)
e = mon.begin_edit(xf.map(QPointF(0.5, 0.5))); assert e['mode'] == 'mask_move'
mon.drag_edit(e, xf.map(QPointF(0.7, 0.5))); mon.end_edit()
ks = top.keyframes[f'mask.{{mid}}.box']
assert len(ks) == 2 and abs(ks[0][1][0] - 0.25) < 1e-6 and abs(ks[1][1][0] - 0.45) < 0.01
assert abs(top.masks[0]['box'][0] - 0.25) < 1e-6   # statik değer bozulmadı
# Ortada canlandırılmış şekil monitörde de görünür
w.timeline.set_playhead(0.45); app.processEvents()
sh = mon.shown_masks(top)[0]['box'][0]
assert 0.3 < sh < 0.4, sh
mon.view.repaint()
# Yumuşatma anahtarı
fe = mp.rows[0].anim_rows[1]
fe.watch.click(); app.processEvents()
assert kf.is_animated(top, f'mask.{{mid}}.feather')
# Renk tekerleği anahtarları
w.color_dock.show() if hasattr(w, 'color_dock') else None
cp = w.color_panel; cp.set_clip(top)
w.timeline.set_playhead(0.1); app.processEvents()
cp.wheel_anim['gain'].watch.click(); app.processEvents()
e = cp.effect(); prop = kf.fx_prop(e, 'gain')
assert kf.is_animated(top, prop) and cp.wheel_anim['gain'].watch.isChecked()
w.timeline.set_playhead(0.8); app.processEvents()
cp._push('gain', force=True); cp._set_wheel('gain', [0.5, 0.0])
assert len(top.keyframes[prop]) == 2 and top.keyframes[prop][1][1] == [0.5, 0.0]
w.timeline.set_playhead(0.45); cp.refresh()
x, y = cp.wheels['gain'].value() if hasattr(cp.wheels['gain'], 'value') else (None, None)
pn = cp.params_now()['gain']
assert 0.2 < pn[0] < 0.3, pn
# Kronometreyi kapat: anahtarlar silinir, o anki değer kalır
cp.wheel_anim['gain'].watch.click(); app.processEvents()
assert not kf.is_animated(top, prop) and 0.2 < e['params']['gain'][0] < 0.3
# Maskeyi sil: anahtarları da gider
mp.remove_mask(mid)
assert not any(k.startswith('mask.') for k in top.keyframes)
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
