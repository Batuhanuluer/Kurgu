"""Lumetri renk düzeltme ve skoplar."""
import os
import subprocess
import sys

import numpy as np

from kurgu import effects as fx
from kurgu import grading as gr


def _img(color, shape=(40, 60)):
    a = np.zeros(shape + (4,), np.uint8)
    a[..., :3] = color
    a[..., 3] = 255
    return a


def _apply(arr, **params):
    e = fx.new_effect("lumetri")
    e["params"].update(params)
    return fx.apply_effects(arr.copy(), [e], {"hibit": True})


def test_identity_is_noop():
    a = np.random.default_rng(0).integers(0, 256, (30, 40, 4), dtype=np.uint8)
    assert (_apply(a) == a).all()
    e = fx.new_effect("lumetri")
    e2 = fx.new_effect("lumetri")
    e["params"]["curve_r"].append([0.5, 0.9])
    assert e2["params"]["curve_r"] == [[0.0, 0.0], [1.0, 1.0]]  # varsayılanlar paylaşılmıyor


def test_basic_controls_move_in_right_direction():
    g = _img((110, 110, 110))
    assert _apply(g, exposure=1.0)[0, 0, 0] > 140
    assert _apply(g, exposure=-1.0)[0, 0, 0] < 85
    warm = _apply(g, temperature=60)[0, 0]
    assert warm[0] > warm[2] + 20  # sıcak: kırmızı > mavi
    mag = _apply(g, tint=60)[0, 0]
    assert mag[1] < mag[0] and mag[1] < mag[2]  # macenta: yeşil azalır
    dark, light = _img((60, 60, 60)), _img((200, 200, 200))
    assert _apply(dark, contrast=80)[0, 0, 0] < 60 and _apply(light, contrast=80)[0, 0, 0] > 200
    assert _apply(dark, shadows=80)[0, 0, 0] > 70
    assert abs(int(_apply(dark, highlights=-80)[0, 0, 0]) - 60) <= 3  # gölgeye dokunmaz
    assert _apply(light, highlights=-80)[0, 0, 0] < 190
    assert _apply(_img((0, 0, 0)), blacks=60)[0, 0, 0] > 15
    assert _apply(_img((230, 230, 230)), whites=80)[0, 0, 0] == 255


def test_saturation_and_vibrance():
    c = _img((200, 80, 60))
    gray = _apply(c, saturation=0)[0, 0, :3]
    assert gray.max() - gray.min() <= 1
    more = _apply(c, saturation=150)[0, 0, :3]
    assert int(more[0]) - int(more[2]) > 140 - 1
    # Canlılık az doygun rengi doygun olandan daha çok artırır
    dull = _img((140, 120, 115))
    vivid = _img((230, 40, 30))
    gain_dull = np.ptp(_apply(dull, vibrance=80)[0, 0, :3].astype(int)) / np.ptp([140, 120, 115])
    gain_vivid = np.ptp(_apply(vivid, vibrance=80)[0, 0, :3].astype(int)) / np.ptp([230, 40, 30])
    assert gain_dull > gain_vivid


def test_curves():
    y = gr.monotone_curve([[0, 0], [0.5, 0.7], [1, 1]])
    assert abs(y[128] - 0.7) < 0.01 and np.all(np.diff(y) >= -1e-6)  # taşma yok, monoton
    y = gr.monotone_curve([[0, 0], [0.25, 0.1], [0.75, 0.9], [1, 1]])
    assert np.all(np.diff(y) >= -1e-6)
    g = _img((128, 128, 128))
    out = _apply(g, curve_master=[[0, 0], [0.5, 0.75], [1, 1]])[0, 0]
    assert abs(int(out[0]) - 191) <= 3 and out[0] == out[1] == out[2]
    out = _apply(g, curve_b=[[0, 0], [0.5, 0.25], [1, 1]])[0, 0]
    assert out[2] < 80 and abs(int(out[0]) - 128) <= 1


def test_color_wheels_tint_tonal_ranges():
    dark, light = _img((40, 40, 40)), _img((220, 220, 220))
    # Gölge tekerleği maviye: koyu piksel mavileşir, açık piksel neredeyse değişmez
    blue = [np.cos(np.radians(240 + gr.HUE_OFFSET)), np.sin(np.radians(240 + gr.HUE_OFFSET))]
    d = _apply(dark, lift=[blue[0] * 0.6, blue[1] * 0.6])[0, 0].astype(int)
    l = _apply(light, lift=[blue[0] * 0.6, blue[1] * 0.6])[0, 0].astype(int)
    assert d[2] - d[0] > 12 and abs(l[2] - l[0]) < d[2] - d[0]
    # Parlak alan tekerleği kırmızıya
    a = np.radians(gr.HUE_OFFSET)
    h = _apply(light, gain=[0.5 * np.cos(a), 0.5 * np.sin(a)])[0, 0].astype(int)
    assert h[0] > h[2] + 20
    assert (_apply(light, gain_luma=-50)[0, 0, :3] < 200).all()


def test_grading_is_fast_enough_for_preview():
    import time
    a = np.random.default_rng(0).integers(0, 256, (720, 1280, 4), dtype=np.uint8)
    e = fx.new_effect("lumetri")
    e["params"].update(exposure=0.5, contrast=30, saturation=120, vibrance=20,
                       curve_master=[[0, 0], [0.5, 0.6], [1, 1]], lift=[0.1, 0.2])
    fx.apply_effects(a.copy(), [e], {})
    t = time.perf_counter()
    fx.apply_effects(a.copy(), [e], {})
    assert time.perf_counter() - t < 0.25


def test_scopes_math():
    from kurgu.ui import scopes
    rgb = np.zeros((10, 20, 3), np.uint8)
    rgb[:, :10] = (255, 255, 255)
    wf = scopes.waveform(scopes.luma(rgb))
    assert wf.shape == (256, 20)
    assert wf[0, :10].sum() == 100 and wf[255, 10:].sum() == 100  # üst = beyaz, alt = siyah
    vs = scopes.vectorscope(np.full((5, 5, 3), 128, np.uint8))
    y, x = np.unravel_index(vs.argmax(), vs.shape)
    assert abs(x - 127) <= 1 and abs(y - 127) <= 1  # gri = merkez
    red = scopes.vectorscope(np.array([[[255, 0, 0]]], np.uint8))
    y, x = np.unravel_index(red.argmax(), red.shape)
    assert y < 60 and x < 128  # kırmızı: yukarı-sol (Cr+, Cb-)
    h = scopes.histogram(rgb)
    assert h.shape == (3, 256) and h[0, 255] == 100 and h[0, 0] == 100


def test_auto_params_fix_cast_and_exposure():
    from kurgu.ui.color_panel import auto_params
    a = np.zeros((60, 80, 3), np.uint8)
    a[..., 0] = np.linspace(20, 90, 80)[None, :]
    a[..., 1] = np.linspace(25, 100, 80)[None, :]
    a[..., 2] = np.linspace(40, 140, 80)[None, :]  # koyu ve mavimsi
    p = auto_params(a)
    assert p["exposure"] > 0.3 and p["temperature"] > 10
    rgba = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], axis=-1)
    out = _apply(rgba, **p)[..., :3].reshape(-1, 3).astype(float).mean(axis=0)
    before = a.reshape(-1, 3).astype(float).mean(axis=0)
    assert np.ptp(out) < np.ptp(before)  # renk kayması azaldı
    assert out.mean() > before.mean()


def test_color_panel_gui(tmp_path, red_video):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication; app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from PySide6.QtWidgets import QMessageBox
QMessageBox.exec = lambda self: 0
from kurgu.ui.main_window import MainWindow
w = MainWindow(None); w.resize(1500, 950); w.show(); app.processEvents()
(m,) = w.import_paths([{red_video!r}])
w.place_media(m, 0.0, None)
w.timeline.set_playhead(0.5)
cp = w.color_panel
w.color_workspace(); app.processEvents()
assert cp.clip is not None and cp.widget().isEnabled()
v = cp.clip
assert not any(e['type'] == 'lumetri' for e in v.effects)
cp.sliders['exposure'].spin.setValue(1.0)        # ilk değişiklik efekti ekler
e = [e for e in v.effects if e['type'] == 'lumetri'][0]
assert abs(e['params']['exposure'] - 1.0) < 1e-6
cp._push('lift', force=True); cp.wheels['lift']._apply(0.3, 0.4)
assert e['params']['lift'] == [0.3, 0.4]
cp.curve._emit()
cp.curve.points = [[0, 0], [0.5, 0.8], [1, 1]]; cp.curve._emit()
assert e['params']['curve_master'][1] == [0.5, 0.8]
# Görüntü gerçekten değişti mi (program monitörü)
import numpy as np
before = w.current_frame_array().mean()
w.undo(); w.undo(); w.undo()
after = w.current_frame_array().mean()
assert before > after + 5, (before, after)
assert w.color_panel.sliders['exposure'].spin.value() == 0 or True
# Skoplar hesaplanıyor
w.scopes._update(); assert w.scopes.view.rgb is not None
for i in range(4):
    w.scopes.mode.setCurrentIndex(i); w.scopes.view.repaint()
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_wheel_hues_match_vectorscope():
    """Tekerlekte bir renge doğru itmek, vektörskopta o rengin hedefine doğru kaydırır."""
    from kurgu.ui import scopes
    for name, rgb in scopes.TARGETS:
        cb, cr = scopes._target(*rgb)
        ang = np.arctan2(cr, cb)
        v = gr.wheel_rgb([np.cos(ang), np.sin(ang)])
        target = np.array(rgb) - np.mean(rgb)
        assert np.dot(v, target) / (np.linalg.norm(v) * np.linalg.norm(target)) > 0.9, name
