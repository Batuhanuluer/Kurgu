import numpy as np

from kurgu import effects as fx
from kurgu import keyframes as kf
from kurgu.media import SourcePool, probe
from kurgu.model import Project
from kurgu.render import Compositor, qimage_to_rgb


def test_interpolation_modes():
    ks = [[0.0, 0.0, "linear"], [2.0, 10.0, "linear"]]
    assert kf.eval_keys(ks, -1) == 0 and kf.eval_keys(ks, 3) == 10
    assert abs(kf.eval_keys(ks, 1.0) - 5) < 1e-9
    ks[0][2] = "ease"
    assert kf.eval_keys(ks, 0.5) < 2.5 and abs(kf.eval_keys(ks, 1.0) - 5) < 1e-9
    ks[0][2] = "hold"
    assert kf.eval_keys(ks, 1.9) == 0
    arr = kf.eval_keys_array([[0, 0, "linear"], [1, 1, "ease"], [2, 0, "hold"]],
                             np.array([-1, 0.5, 1.5, 3.0]))
    assert np.allclose(arr, [0, 0.5, 0.5, 0], atol=1e-6)


def test_position_animation_renders(qapp, red_video):
    p = Project(320, 240, 25)
    c = p.place_media(p.add_media(probe(red_video)), 0.0)[0]
    c.scale = 0.3
    kf.set_key(c, "pos_x", c.source_time(0.0), -100, p.fps)
    kf.set_key(c, "pos_x", c.source_time(1.5), 100, p.fps)
    comp = Compositor(p, SourcePool())

    def red_center_x(t):
        img = qimage_to_rgb(comp.compose(t, 320, 240))
        xs = np.where((img[150, :, 0] > 150) & (img[150, :, 2] < 100))[0]
        return xs.mean()
    x0, x1, x2 = red_center_x(0.0), red_center_x(0.75), red_center_x(1.5)
    assert x0 < 90 and 140 < x1 < 180 and x2 > 230


def test_effect_param_animation_and_split(qapp, red_video):
    p = Project(320, 240, 25)
    c = p.place_media(p.add_media(probe(red_video)), 0.0)[0]
    e = fx.new_effect("black_white")
    c.effects.append(e)
    prop = kf.fx_prop(e, "amount")
    kf.set_key(c, prop, 0.0, 0.0, p.fps)
    kf.set_key(c, prop, 2.0, 1.0, p.fps)
    comp = Compositor(p, SourcePool())
    early = qimage_to_rgb(comp.compose(0.05, 320, 240))[200, 160].astype(int)
    late = qimage_to_rgb(comp.compose(1.9, 320, 240))[200, 160].astype(int)
    assert early[0] - early[2] > 120 and abs(late[0] - late[2]) < 25
    # Bölünce iki parça da animasyonu kaynak zamanında korur
    p.split_at(1.0, [c.id])
    right = [x for x in p.clips if x.start == 1.0 and p.track_is_video(x.track)][0]
    assert abs(kf.value_at(right, prop, 1.0) - 0.5) < 1e-6
    assert abs(kf.value_at(c, prop, 0.5) - 0.25) < 1e-6


def test_volume_keyframes(red_video):
    p = Project()
    clips = p.place_media(p.add_media(probe(red_video)), 0.0)
    a = [c for c in clips if not p.track_is_video(c.track)][0]
    kf.set_key(a, "volume", 0.5, 1.0, p.fps)
    kf.set_key(a, "volume", 1.0, 0.0, p.fps)
    comp = Compositor(p, SourcePool(p.sample_rate))
    loud = np.abs(comp.mix(0.2, 4800)).max()
    silent = np.abs(comp.mix(1.2, 4800)).max()
    assert loud > 0.2 and silent < 1e-4


def test_toggle_and_move_keys():
    from kurgu.model import Clip
    c = Clip(track=0, start=0, in_point=0, out_point=5, kind="text", text="x")
    kf.toggle_animation(c, "opacity", 1.0, 25)
    assert kf.is_animated(c, "opacity")
    kf.set_key(c, "opacity", 3.0, 0.0, 25)
    kf.move_keys_at(c, 3.0, 4.0, 25)
    assert kf.keys(c, "opacity")[-1][0] == 4.0
    kf.toggle_animation(c, "opacity", 2.0, 25)  # kapat: o anki değer sabitlenir
    assert not kf.is_animated(c, "opacity") and abs(c.opacity - 0.6666666) < 1e-3
