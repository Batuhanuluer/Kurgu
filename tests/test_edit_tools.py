import pytest

from kurgu.media import probe
from kurgu.model import Project


@pytest.fixture
def three(red_video, blue_video):
    """V1: A(0-2) B(2-4) C(4-6) bağlı sesleriyle; B ve C kaynağın ortasından (tutamaçlı)."""
    p = Project(320, 240, 25)
    mr, mb = p.add_media(probe(red_video)), p.add_media(probe(blue_video))
    long = p.add_media(probe(red_video))
    a = p.place_media(mr, 0.0, in_point=0.0, out_point=2.0)
    b = p.place_media(mb, 2.0, in_point=0.0, out_point=2.0)
    c = p.place_media(mr, 4.0, in_point=0.0, out_point=2.0)
    return p, a[0], b[0], c[0]


def vids(p):
    return sorted((c for c in p.clips if p.track_is_video(c.track)), key=lambda c: c.start)


def test_place_with_in_out(red_video):
    p = Project()
    m = p.add_media(probe(red_video))
    v, a = p.place_media(m, 1.0, in_point=0.5, out_point=1.25)
    assert (v.in_point, v.out_point, v.start) == (0.5, 1.25, 1.0) and abs(v.end - 1.75) < 1e-9
    assert a.in_point == 0.5 and a.link_id == v.link_id
    only_video = p.place_media(m, 5.0, video=True, audio=False)
    assert len(only_video) == 1 and p.track_is_video(only_video[0].track)


def test_insert_pushes_everything(three, red_video):
    p, a, b, c = three
    m = p.add_media(probe(red_video))
    made = p.insert_media(m, 3.0, in_point=0.0, out_point=1.0)  # B'nin ortasına 1 sn
    v = vids(p)
    assert [round(x.start, 3) for x in v] == [0, 2, 3, 4, 5]
    assert abs(p.duration - 7.0) < 1e-6  # hiçbir şey silinmedi
    assert len([x for x in p.clips if not p.track_is_video(x.track)]) == 5  # ses de bölündü


def test_ripple_trim_out_and_in(three):
    p, a, b, c = three
    d = p.ripple_trim(a, "out", 1.5)  # A'nın sonunu 0.5 sn kısalt
    assert abs(d + 0.5) < 1e-6
    assert [round(x.start, 3) for x in vids(p)] == [0, 1.5, 3.5]  # boşluk kalmadı
    assert all(abs(x.start - 1.5) < 1e-6 for x in p.linked(b))  # ses de kaydı
    p.ripple_trim(b, "in", 1.5 + 0.25)  # B'nin başından 0.25 sn kes
    v = vids(p)
    assert abs(v[1].start - 1.5) < 1e-6 and abs(v[1].in_point - 0.25) < 1e-6
    assert abs(v[2].start - 3.25) < 1e-6


def _abc(p, red, blue):
    """A: kırmızı 0-1.5 (kaynak 0-1.5, sonunda 0.5 tutamaç)
       B: mavi 1.5-3.0 (kaynak 0.5-2.0, başında 0.5 tutamaç)
       C: kırmızı 3.0-4.0"""
    mr, mb = p.add_media(probe(red)), p.add_media(probe(blue))
    a = p.place_media(mr, 0.0, in_point=0.0, out_point=1.5)[0]
    b = p.place_media(mb, 1.5, in_point=0.5, out_point=2.0)[0]
    c = p.place_media(mr, 3.0, in_point=0.0, out_point=1.0)[0]
    return a, b, c


def test_roll_keeps_total_duration(red_video, blue_video):
    p = Project(320, 240, 25)
    a, b, c = _abc(p, red_video, blue_video)
    total = p.duration
    t = p.roll(a, 1.2)  # kesmeyi sola al: A kısalır, B öne uzar
    assert abs(t - 1.2) < 1e-6 and abs(a.end - 1.2) < 1e-6 and abs(b.start - 1.2) < 1e-6
    assert abs(b.in_point - 0.2) < 1e-6 and abs(p.duration - total) < 1e-6
    assert abs(p.roll(a, 0.1) - 1.0) < 1e-6  # B'nin kaynağının başından öteye gidemez
    assert abs(p.roll(a, 3.0) - 2.0) < 1e-6  # A'nın kaynağının sonundan öteye gidemez
    aud_a = [x for x in p.linked(a) if x is not a][0]
    assert abs(aud_a.end - 2.0) < 1e-6  # bağlı ses de aynı kesmede


def test_slip(red_video, blue_video):
    p = Project(320, 240, 25)
    a, b, c = _abc(p, red_video, blue_video)
    d = p.slip(b, -0.3)  # B'nin yeri/süresi aynı, içeriği 0.3 sn öne
    assert abs(d + 0.3) < 1e-6 and abs(b.in_point - 0.2) < 1e-6
    assert abs(b.start - 1.5) < 1e-6 and abs(b.duration - 1.5) < 1e-6
    assert abs(p.slip(b, -5) + 0.2) < 1e-6  # kaynağın başına dayanır
    assert abs(p.slip(b, 5) - 0.5) < 1e-6  # kaynağın sonuna dayanır (2.0)


def test_slide(red_video, blue_video):
    p = Project(320, 240, 25)
    a, b, c = _abc(p, red_video, blue_video)
    total = p.duration
    dt = p.slide(b, 0.3)  # B sağa: A uzar (tutamacı var), C'nin başı kısalır
    assert abs(dt - 0.3) < 1e-6
    assert abs(a.end - 1.8) < 1e-6 and abs(b.start - 1.8) < 1e-6 and abs(b.in_point - 0.5) < 1e-6
    assert abs(c.start - 3.3) < 1e-6 and abs(c.in_point - 0.3) < 1e-6
    assert abs(p.duration - total) < 1e-6
    assert abs(p.slide(b, 5) - 0.2) < 1e-6  # A'nın tutamacı (2.0'a kadar) biter
