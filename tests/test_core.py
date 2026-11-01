import av
import numpy as np

from kurgu.media import probe, SourcePool
from kurgu.model import History, Project
from kurgu.render import Compositor, export, qimage_to_rgb


def test_probe(red_video):
    m = probe(red_video)
    assert m.kind == "video" and m.has_audio and m.has_video
    assert abs(m.duration - 2.0) < 0.15
    assert (m.width, m.height) == (320, 240)


def test_place_split_delete_undo(red_video):
    p = Project()
    m = p.add_media(probe(red_video))
    clips = p.place_media(m, 0.0)
    assert len(clips) == 2 and clips[0].link_id == clips[1].link_id
    h = History()
    h.push(p)
    p.split_at(1.0)
    assert len(p.clips) == 4
    left = [c for c in p.clips if c.start == 0 and p.track_is_video(c.track)][0]
    right = [c for c in p.clips if c.start == 1.0 and p.track_is_video(c.track)][0]
    assert abs(left.out_point - 1.0) < 1e-6 and abs(right.in_point - 1.0) < 1e-6
    assert left.link_id != right.link_id
    p.delete([left.id], ripple=True)  # bağlı ses klibi de silinir, boşluk kapanır
    assert len(p.clips) == 2 and all(abs(c.start) < 1e-6 for c in p.clips)
    p = h.undo(p)
    assert len(p.clips) == 2 and abs(p.duration - m.duration) < 1e-6


def test_overwrite_on_move(red_video, blue_video):
    p = Project()
    a = p.add_media(probe(red_video))
    b = p.add_media(probe(blue_video))
    p.place_media(a, 0.0)
    cb = p.place_media(b, 3.0)[0]
    p.move(cb, 1.0)  # kırmızının 1-2 sn arası mavinin altında kalır
    vt = p.track_indices("video")[0]
    on_v1 = p.clips_on_track(vt)
    assert [round(c.start, 3) for c in on_v1] == [0.0, 1.0]
    assert abs(on_v1[0].end - 1.0) < 1e-6


def test_trim_and_speed(red_video):
    p = Project()
    m = p.add_media(probe(red_video))
    v = p.place_media(m, 0.0)[0]
    p.trim(v, "in", 0.5)
    assert abs(v.start - 0.5) < 1e-6 and abs(v.in_point - 0.5) < 1e-6
    p.trim(v, "out", 10.0)  # kaynak süresinden uzun olamaz
    assert v.out_point <= m.duration + 1e-6
    p.set_speed(v, 2.0)
    assert all(c.speed == 2.0 for c in p.linked(v))


def test_save_load(tmp_path, red_video):
    p = Project(1280, 720, 24)
    m = p.add_media(probe(red_video))
    p.place_media(m, 0.0)
    p.add_text("Merhaba Dünya", 0.5)
    f = tmp_path / "p.kurgu"
    p.save(str(f))
    q = Project.load(str(f))
    assert (q.width, q.height, q.fps) == (1280, 720, 24)
    assert len(q.clips) == 3 and any(c.text == "Merhaba Dünya" for c in q.clips)


def test_compose_layers_and_mix(qapp, red_video, blue_video):
    p = Project(640, 360, 25)
    a = p.add_media(probe(red_video))
    b = p.add_media(probe(blue_video))
    p.place_media(a, 0.0)
    v2 = p.track_indices("video")[1]
    a2 = p.track_indices("audio")[1]
    top = p.place_media(b, 0.0, video_track=v2, audio_track=a2)[0]
    top.scale = 0.5
    comp = Compositor(p, SourcePool(p.sample_rate))
    rgb = qimage_to_rgb(comp.compose(1.0, 640, 360))
    center, corner = rgb[200, 320], rgb[330, 100]
    assert center[2] > 200 and center[0] < 60  # ortada mavi (üst katman, yarı boy)
    assert corner[0] > 200 and corner[2] < 60  # köşede kırmızı (alt katman)
    top.opacity = 0.0
    rgb = qimage_to_rgb(comp.compose(1.0, 640, 360))
    assert rgb[200, 320][0] > 200
    pcm = comp.mix(0.5, 4800)
    assert pcm.shape == (4800, 2) and 0.3 < np.abs(pcm).max() <= 1.0


def test_export(qapp, tmp_path, red_video):
    p = Project(320, 240, 25)
    m = p.add_media(probe(red_video))
    p.place_media(m, 0.0)
    p.add_text("Kurgu", 0.0).out_point = 2.0
    out = tmp_path / "out.mp4"
    steps = []
    assert export(p, str(out), progress=steps.append)
    assert steps and steps[-1] == 1.0
    with av.open(str(out)) as c:
        assert c.streams.video and c.streams.audio
        frames = sum(1 for _ in c.decode(video=0))
        assert abs(frames - 50) <= 2
