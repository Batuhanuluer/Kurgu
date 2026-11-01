"""Telefon videoları: dönme bilgisi (dikey çekim) ve değişken kare hızı (VFR)."""
import struct
from fractions import Fraction

import av
import numpy as np
import pytest

from kurgu.media import SourcePool, VideoSource, probe, thumbnail
from kurgu.model import Project
from kurgu.render import Compositor, qimage_to_rgb


def _write_with_rotation(path, deg, w=320, h=180):
    """Yatay kaydedilmiş + MP4 dönüşüm matrisi: telefonların dikey videoyu sakladığı biçim.
    Kaydedilen karenin SOL kenarında kırmızı şerit var."""
    with av.open(str(path), "w") as out:
        s = out.add_stream("libx264", rate=25)
        s.width, s.height, s.pix_fmt = w, h, "yuv420p"
        for i in range(10):
            img = np.zeros((h, w, 3), np.uint8)
            img[:, :40] = (255, 0, 0)
            f = av.VideoFrame.from_ndarray(img, format="rgb24")
            f.pts = i
            for p in s.encode(f):
                out.mux(p)
        for p in s.encode():
            out.mux(p)
    data = bytearray(open(path, "rb").read())
    i = data.find(b"tkhd")
    assert data[i + 4] == 0
    off = i + 4 + 4 + 20 + 8 + 8  # sürüm/bayrak, zamanlar+kimlik+süre, rezerv, katman..ses
    one, neg, z = 0x00010000, 0xFFFF0000, 0
    mats = {90: (z, one, z, neg, z, z, z, z, 0x40000000),
            180: (neg, z, z, z, neg, z, z, z, 0x40000000)}
    data[off:off + 36] = struct.pack(">9I", *mats[deg])
    open(path, "wb").write(bytes(data))
    return str(path)


def test_portrait_phone_video_is_upright(qapp, tmp_path):
    path = _write_with_rotation(tmp_path / "dikey.mp4", 90)
    m = probe(path)
    assert (m.width, m.height) == (180, 320) and m.rotation == 3  # saat yönünde 90°
    p = Project(1080, 1920, 25)  # dikey (Reels) proje
    p.place_media(p.add_media(m), 0.0)
    img = qimage_to_rgb(Compositor(p, SourcePool()).compose(0.1, 180, 320))
    # Saat yönünde 90° dönünce soldaki kırmızı şerit üste gelir ve görüntü kareyi doldurur
    assert img[5, 90, 0] > 200 and img[5, 90, 2] < 60
    assert img[300, 90, 0] < 60
    th = thumbnail(path, "video", 90)
    assert th.shape[:2] == (160, 90) and th[3, 45, 0] > 200


def test_upside_down_video(qapp, tmp_path):
    path = _write_with_rotation(tmp_path / "ters.mp4", 180)
    m = probe(path)
    assert (m.width, m.height) == (320, 180)
    p = Project(320, 180, 25)
    p.place_media(p.add_media(m), 0.0)
    img = qimage_to_rgb(Compositor(p, SourcePool()).compose(0.1, 320, 180))
    assert img[90, 310, 0] > 200 and img[90, 5, 0] < 60  # şerit sağa geçti


def test_variable_frame_rate_follows_timestamps(tmp_path):
    """VFR: kareler düzensiz zamanlarda; doğru kare zaman damgasına göre seçilmeli."""
    path = tmp_path / "vfr.mp4"
    times_ms = [0, 33, 50, 120, 400, 410, 700, 1000]  # düzensiz aralıklar
    with av.open(str(path), "w") as out:
        s = out.add_stream("libx264", rate=30)
        s.width, s.height, s.pix_fmt = 64, 64, "yuv420p"
        s.time_base = Fraction(1, 1000)
        s.codec_context.time_base = Fraction(1, 1000)
        for i, t in enumerate(times_ms):
            img = np.full((64, 64, 3), i * 30, np.uint8)  # kare numarası parlaklıkta
            f = av.VideoFrame.from_ndarray(img, format="rgb24")
            f.pts = t
            f.time_base = Fraction(1, 1000)
            for p in s.encode(f):
                out.mux(p)
        for p in s.encode():
            out.mux(p)
    src = VideoSource(str(path))

    def idx(t):
        return round(float(src.frame_at(t).to_ndarray(format="rgb24").mean()) / 30)
    # 0.2 sn'de en son gösterilen kare 120 ms'deki (3), 0.5 sn'de 410 ms'deki (5)
    assert [idx(t) for t in (0.01, 0.2, 0.5, 0.75, 0.3)] == [0, 3, 5, 6, 3]
