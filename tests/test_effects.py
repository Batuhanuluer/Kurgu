import numpy as np
import pytest

from kurgu import effects as fx
from kurgu import transitions
from kurgu.media import SourcePool, probe
from kurgu.model import Project
from kurgu.render import Compositor, qimage_to_rgb

from conftest import make_video


@pytest.fixture(scope="session")
def green_video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("g") / "green.mp4", color=(0, 255, 0))


def _setup(qapp, bottom, top):
    p = Project(320, 240, 25)
    a = p.add_media(probe(bottom))
    b = p.add_media(probe(top))
    p.place_media(a, 0.0)
    v2, a2 = p.track_indices("video")[1], p.track_indices("audio")[1]
    up = p.place_media(b, 0.0, video_track=v2, audio_track=a2)[0]
    return p, up, Compositor(p, SourcePool(p.sample_rate))


def test_every_effect_runs():
    arr = (np.random.default_rng(0).random((48, 64, 4)) * 255).astype(np.uint8)
    for eid in fx.EFFECTS:
        e = fx.new_effect(eid)
        out = fx.apply_effects(arr.copy(), [e], {"scale": 1.0, "t": 0.5})
        assert out.shape == arr.shape and out.dtype == np.uint8, eid


def test_chroma_key_reveals_lower_track(qapp, red_video, green_video):
    p, up, comp = _setup(qapp, red_video, green_video)
    px = qimage_to_rgb(comp.compose(1.0, 320, 240))[200, 160]
    assert px[1] > 200 and px[0] < 60  # yeşil üstte
    up.effects.append(fx.new_effect("chroma_key"))
    px = qimage_to_rgb(comp.compose(1.0, 320, 240))[200, 160]
    assert px[0] > 200 and px[1] < 60  # yeşil silindi, kırmızı göründü


def test_black_white_and_blur(qapp, red_video):
    p = Project(320, 240, 25)
    c = p.place_media(p.add_media(probe(red_video)), 0.0)[0]
    comp = Compositor(p, SourcePool())
    c.effects.append(fx.new_effect("black_white"))
    px = qimage_to_rgb(comp.compose(1.0, 320, 240))[200, 160].astype(int)
    assert abs(px[0] - px[1]) < 6 and abs(px[1] - px[2]) < 6
    c.effects[0]["enabled"] = False
    assert qimage_to_rgb(comp.compose(1.0, 320, 240))[200, 160][0] > 200


def test_cross_dissolve_and_audio_crossfade(qapp, red_video, blue_video):
    p = Project(320, 240, 25)
    a = p.place_media(p.add_media(probe(red_video)), 0.0)
    b = p.place_media(p.add_media(probe(blue_video)), a[0].end)
    p.apply_transition(b[0], "in", "cross_dissolve", 1.0)
    assert b[1].trans_in == "cross_dissolve"  # bağlı ses klibi de aldı
    comp = Compositor(p, SourcePool(p.sample_rate))
    cut = b[0].start
    mid = qimage_to_rgb(comp.compose(cut, 320, 240))[200, 160].astype(int)
    assert 80 < mid[0] < 180 and 80 < mid[2] < 180  # yarı kırmızı yarı mavi
    before = qimage_to_rgb(comp.compose(cut - 0.6, 320, 240))[200, 160]
    assert before[0] > 200
    # Sabit güçlü crossfade: giden ses azalır, gelen ses yükselir
    out_amp = np.abs(comp.mix(cut - 0.3, 2400)).max()
    in_amp = np.abs(comp.mix(cut + 0.1, 2400)).max()
    assert 0.2 < out_amp < 0.299 and 0.18 < in_amp < 0.29


def test_single_sided_and_all_transitions(qapp, red_video):
    p = Project(320, 240, 25)
    c = p.place_media(p.add_media(probe(red_video)), 0.0)[0]
    comp = Compositor(p, SourcePool())
    for kind, _n, _cat in transitions.TRANSITIONS:
        p.apply_transition(c, "in", kind, 1.0)
        img = qimage_to_rgb(comp.compose(0.5, 320, 240))
        assert img.shape == (240, 320, 3), kind
    p.apply_transition(c, "in", "dip_black", 1.0)
    assert qimage_to_rgb(comp.compose(0.1, 320, 240))[200, 160].max() < 40
    p.apply_transition(c, "in", "", 1.0)
    p.apply_transition(c, "out", "cross_dissolve", 1.0)
    assert c.trans_out == "cross_dissolve"
    assert qimage_to_rgb(comp.compose(c.end - 0.05, 320, 240))[200, 160][0] < 40


def test_split_keeps_independent_effects(red_video):
    p = Project()
    c = p.place_media(p.add_media(probe(red_video)), 0.0)[0]
    c.effects.append(fx.new_effect("blur"))
    c.trans_in = "cross_dissolve"
    p.split_at(1.0)
    right = [x for x in p.clips if x.start == 1.0 and p.track_is_video(x.track)][0]
    right.effects[0]["params"]["radius"] = 50
    assert c.effects[0]["params"]["radius"] == 10.0
    assert right.trans_in == "" and c.trans_in == "cross_dissolve"
    q = Project.from_dict(p.to_dict())
    assert any(x.effects for x in q.clips)


def test_one_sided_out_transitions_hide_clip(qapp, red_video):
    """Komşusu olmayan klibin sonundaki geçiş, klibi gerçekten kaybettirmeli."""
    p = Project(320, 240, 25)
    c = p.place_media(p.add_media(probe(red_video)), 0.0)[0]
    comp = Compositor(p, SourcePool())
    for kind, _n, _cat in transitions.TRANSITIONS:
        if kind == "dip_white":
            continue
        p.apply_transition(c, "out", kind, 1.0)
        early = qimage_to_rgb(comp.compose(c.end - 0.95, 320, 240))[60:].mean(0).mean(0)
        late = qimage_to_rgb(comp.compose(c.end - 0.1, 320, 240))[60:].mean(0).mean(0)
        assert late[0] < early[0] * 0.5, kind


def test_adjacency_tolerates_float_drift(red_video, blue_video):
    p = Project(320, 240, 25)
    a = p.place_media(p.add_media(probe(red_video)), 0.0)[0]
    b = p.place_media(p.add_media(probe(blue_video)), a.end + 0.01)[0]
    assert p.prev_adjacent(b) is a
