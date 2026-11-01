"""Performans: kalıcı render dosyaları, donanım çözme/kodlama geri dönüşü."""
import os
import time

import numpy as np
from PySide6.QtGui import QColor, QImage

from kurgu import hw
from kurgu.media import SourcePool, VideoSource, probe
from kurgu.model import Project
from kurgu.rendercache import FrameCache, FrameSig, RenderWorker


def test_disk_render_cache_survives_restart(qapp, tmp_path):
    d = str(tmp_path / "render")
    c = FrameCache(64, d, 100)
    img = QImage(320, 180, QImage.Format_RGB32)
    img.fill(QColor(200, 30, 30))
    key = (12, "abcdef0123", 320, 180)
    c.put(key, img, persist=True)
    c.put((13, "x", 320, 180), img)  # kalıcı değil
    c2 = FrameCache(64, d, 100)  # program yeniden açıldı
    assert c2.has(key) and not c2.has((13, "x", 320, 180))
    got = c2.get(key)
    assert got.width() == 320 and abs(got.pixelColor(10, 10).red() - 200) < 6
    c2.clear()  # yalnız bellek
    assert c2.has(key)
    c2.clear(disk=True)
    assert not c2.has(key) and not os.listdir(d)


def test_disk_budget_trims_oldest(qapp, tmp_path):
    d = str(tmp_path / "r")
    c = FrameCache(64, d, 1)  # 1 MB
    rng = np.random.default_rng(0)
    for i in range(12):
        arr = rng.integers(0, 256, (360, 640, 4), dtype=np.uint8)
        img = QImage(arr.data, 640, 360, 640 * 4, QImage.Format_RGB32).copy()
        c.put((i, "s", 640, 360), img, persist=True)
        time.sleep(0.01)
    assert c.disk_bytes <= 1024 * 1024
    assert c._name((11, "s", 640, 360)) in c.disk_index
    assert c._name((0, "s", 640, 360)) not in c.disk_index  # en eskisi silindi


def test_render_worker_persists(qapp, tmp_path, red_video):
    p = Project(320, 240, 25.0)
    p.place_media(p.add_media(probe(red_video)), 0.0)
    cache = FrameCache(64, str(tmp_path / "r"), 100)
    w = RenderWorker(cache)
    done = []
    w.submit(p, [0, 1, 2], 160, 120, done=done.append, persist=True)
    for _ in range(200):
        if done:
            break
        time.sleep(0.02)
    w.shutdown()
    sig = FrameSig(p)
    assert done == [True]
    assert FrameCache(64, str(tmp_path / "r"), 100).has((1, sig(1 / 25), 160, 120))


def test_sig_changes_when_media_file_changes(qapp, tmp_path, red_video):
    import shutil
    f = str(tmp_path / "v.mp4")
    shutil.copy(red_video, f)
    p = Project(320, 240, 25.0)
    p.place_media(p.add_media(probe(f)), 0.0)
    s1 = FrameSig(p)(0.5)
    st = os.stat(f)
    os.utime(f, (st.st_atime, st.st_mtime + 10))  # dosya değişti (aynı ad)
    assert FrameSig(p)(0.5) != s1


def test_hw_decode_falls_back_to_software(monkeypatch, red_video):
    monkeypatch.setattr(hw, "MODE", "auto")
    monkeypatch.setattr(hw, "decoder_types", lambda: ["no_such_device"])
    import av.codec.hwaccel as hwa

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("device not available")
    monkeypatch.setattr(hwa, "HWAccel", Boom)
    src = VideoSource(red_video)
    ref = VideoSource(red_video, hw=False)
    a = src.frame_at(0.5).to_ndarray(format="rgb24")
    b = ref.frame_at(0.5).to_ndarray(format="rgb24")
    assert np.array_equal(a, b)
    monkeypatch.setattr(hw, "MODE", "off")
    hw.open_video(red_video).close()
    assert hw.LAST_USED[red_video] == ""


def test_hw_encoder_probe_and_export_fallback(qapp, tmp_path, red_video, monkeypatch):
    assert hw.encoder_works("definitely_not_an_encoder") is False
    from kurgu import exporter as ex
    p = Project(320, 240, 25.0)
    p.place_media(p.add_media(probe(red_video)), 0.0)
    monkeypatch.setattr(hw, "hw_encoder", lambda kind: None)  # GPU yoksa yazılım kodlayıcı
    out = str(tmp_path / "o.mp4")
    assert ex.export_media(p, out, "h264", hardware=True)
    import av
    with av.open(out) as c:
        assert c.streams.video[0].codec_context.name == "h264"


def test_pool_uses_hw_open(monkeypatch, red_video):
    calls = []
    real = hw.open_video
    monkeypatch.setattr(hw, "open_video", lambda path: calls.append(path) or real(path))
    m = probe(red_video)
    pool = SourcePool()
    assert pool.video_source(m).frame_at(0.1) is not None
    assert calls == [red_video]
    pool.close()
