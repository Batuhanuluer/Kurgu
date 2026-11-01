from fractions import Fraction

import av
import numpy as np

from kurgu import proxy as px
from kurgu.media import SourcePool, VideoSource, probe
from kurgu.model import Project
from kurgu.render import Compositor, qimage_to_rgb


def _hevc10(path, w=1280, h=720, times_ms=(0, 40, 80, 200, 240, 600)):
    """10-bit HEVC, düzensiz zaman damgalı (D-Log + telefon karışımı bir senaryo)."""
    with av.open(str(path), "w") as out:
        s = out.add_stream("libx265", rate=25)
        s.width, s.height, s.pix_fmt = w, h, "yuv420p10le"
        s.time_base = Fraction(1, 1000)
        s.codec_context.time_base = Fraction(1, 1000)
        s.options = {"x265-params": "log-level=error"}
        for i, t in enumerate(times_ms):
            img = np.zeros((h, w, 3), np.uint16)
            img[..., 0] = (i + 1) * 6000  # kare numarası kırmızı kanalda
            img[:, : w // 4, 2] = 60000   # solda mavi şerit
            f = av.VideoFrame.from_ndarray(img, format="rgb48le").reformat(format="yuv420p10le")
            f.pts = t
            f.time_base = Fraction(1, 1000)
            for p in s.encode(f):
                out.mux(p)
        for p in s.encode():
            out.mux(p)
    return str(path)


def test_proxy_matches_source(qapp, tmp_path):
    src = _hevc10(tmp_path / "dlog.mp4")
    m = probe(src)
    assert m.codec == "hevc" and m.bit_depth == 10 and px.needs_proxy(m)
    steps = []
    path = px.make_proxy(m, str(tmp_path / "proxies"), progress=steps.append)
    assert path.endswith(".proxy.mp4") and steps
    with av.open(path) as c:
        vs = c.streams.video[0]
        frames = list(c.decode(vs))
        assert (vs.codec_context.width, vs.codec_context.height) == (960, 540)
        assert frames[0].format.name == "yuv420p10le"  # 10-bit korunur
        assert [round(f.time * 1000) for f in frames] == [0, 40, 80, 200, 240, 600]
    # Aynı zamanda aynı kare (proxy ve orijinal)
    a, b = VideoSource(src), VideoSource(path)
    for t in (0.05, 0.21, 0.5):
        ra = a.frame_at(t).to_ndarray(format="rgb24")[..., 0].mean()
        rb = b.frame_at(t).to_ndarray(format="rgb24")[..., 0].mean()
        assert abs(ra - rb) < 4
    # Önizleme proxy'yi, dışa aktarma orijinali kullanır
    m.proxy = path
    p = Project(1280, 720, 25)
    p.place_media(p.add_media(m), 0.0)
    prev = Compositor(p, SourcePool(use_proxy=True))
    full = Compositor(p, SourcePool(use_proxy=False))
    assert prev.pool.video_source(m).is_proxy and not full.pool.video_source(m).is_proxy
    i1 = qimage_to_rgb(prev.compose(0.1, 640, 360)).astype(int)
    i2 = qimage_to_rgb(full.compose(0.1, 640, 360)).astype(int)
    assert np.abs(i1 - i2).mean() < 6
    # Var olan proxy yeniden üretilmez
    assert px.make_proxy(m, str(tmp_path / "proxies")) == path


def test_cancel_leaves_no_partial_file(tmp_path):
    src = _hevc10(tmp_path / "x.mp4")
    m = probe(src)
    folder = tmp_path / "p"
    try:
        px.make_proxy(m, str(folder), cancelled=lambda: True)
    except RuntimeError:
        pass
    assert not any(folder.iterdir())


def test_small_h264_needs_no_proxy(red_video):
    assert not px.needs_proxy(probe(red_video))
