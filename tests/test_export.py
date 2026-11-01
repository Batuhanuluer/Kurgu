"""Dışa aktarma: biçimler, 10-bit, hazır ayar boyutları, I/O aralığı, GIF, kare."""
import os
import subprocess
import sys

import av
import numpy as np
import pytest

from kurgu import exporter as ex
from kurgu.media import probe
from kurgu.model import Project


def _proj(video):
    p = Project(320, 180, 25.0)
    p.place_media(p.add_media(probe(video)), 0.0)
    return p


@pytest.fixture(scope="module")
def clip16x9(tmp_path_factory):
    import sys as _s
    _s.path.insert(0, os.path.dirname(__file__))
    from conftest import make_video
    return make_video(tmp_path_factory.mktemp("e") / "a.mp4", seconds=2.0, w=320, h=180)


def _info(path):
    with av.open(path) as c:
        v = c.streams.video[0] if c.streams.video else None
        a = c.streams.audio[0] if c.streams.audio else None
        n = sum(1 for _ in c.decode(video=0)) if v else 0
        return (v.codec_context.name if v else None, v.codec_context.pix_fmt if v else None,
                (v.width, v.height) if v else None, n,
                a.codec_context.name if a else None,
                float(c.duration / av.time_base) if c.duration else 0.0)


@pytest.mark.parametrize("key", [k for k, f in ex.FORMATS.items() if ex.available(f)])
def test_every_format_writes_playable_file(qapp, tmp_path, clip16x9, key):
    f = ex.FORMATS[key]
    out = str(tmp_path / f"o.{f.ext}")
    assert ex.export_media(_proj(clip16x9), out, key, max_fps=15 if key == "gif" else None)
    vc, pix, size, n, ac, dur = _info(out)
    if f.vcodec:
        assert size == (320, 180) and n >= (24 if key == "gif" else 49)  # GIF: 25→12.5 fps
        if f.deep:
            assert "10" in pix
    else:
        assert vc is None and ac is not None and 1.8 < dur < 2.3


def test_ten_bit_keeps_fine_gradient(qapp, tmp_path):
    """10-bit kaynak + 10-bit çıktı: yumuşak geçişte 8-bit'ten çok daha fazla ton kalır."""
    src = str(tmp_path / "g10.mov")
    out = av.open(src, "w")
    s = out.add_stream("prores_ks", rate=25)
    s.width, s.height, s.pix_fmt = 256, 64, "yuv422p10le"
    ramp = np.linspace(20000, 24000, 256)  # 8-bit'te ~16 ton, 10-bit'te ~64
    img = np.zeros((64, 256, 3), np.uint16)
    img[...] = ramp[None, :, None].astype(np.uint16)
    for i in range(10):
        fr = av.VideoFrame.from_ndarray(img, format="rgb48le")
        fr.pts = i
        for pk in s.encode(fr):
            out.mux(pk)
    for pk in s.encode():
        out.mux(pk)
    out.close()
    p = Project(256, 64, 25.0)
    p.place_media(p.add_media(probe(src)), 0.0)

    def levels(path):
        with av.open(path) as c:
            f = next(c.decode(video=0))
            y = f.to_ndarray(format="rgb48le")[32, :, 1]
            return len(np.unique(y >> 6))  # 10-bit düzeyleri
    deep = str(tmp_path / "d.mov")
    ex.export_media(p, deep, "prores")
    flat = str(tmp_path / "f.mp4")
    ex.export_media(p, flat, "h264")
    assert levels(deep) > 2 * levels(flat), (levels(deep), levels(flat))


def test_letterbox_for_vertical_preset(qapp, tmp_path, clip16x9):
    out = str(tmp_path / "r.mp4")
    ex.export_media(_proj(clip16x9), out, "h264", 180, 320)
    with av.open(out) as c:
        f = next(c.decode(video=0)).to_ndarray(format="rgb24")
    assert f.shape == (320, 180, 3)
    assert f[:40].max() < 30 and f[-40:].max() < 30   # üst/alt siyah kenar
    assert f[160, 90].max() > 100                       # ortada görüntü


def test_in_out_range(qapp, tmp_path, clip16x9):
    p = _proj(clip16x9)
    p.seq_in, p.seq_out = 0.4, 1.2
    a, b = p.in_out_range()
    out = str(tmp_path / "io.mp4")
    ex.export_media(p, out, "h264", start=a, end=b)
    _vc, _pix, _s, n, _ac, dur = _info(out)
    assert n == 20 and abs(dur - 0.8) < 0.1
    q = Project.from_dict(p.to_dict())
    assert (q.seq_in, q.seq_out) == (0.4, 1.2)


def test_gif_palette_quality(qapp, tmp_path):
    rng = np.random.default_rng(0)
    pix = rng.integers(0, 256, (5000, 3)).astype(np.uint8)
    pal = ex.median_cut(pix, 256)
    lut = ex.palette_lut(pal)
    img = np.zeros((32, 64, 3), np.uint8)
    img[..., 0] = np.linspace(0, 255, 64)[None, :]
    img[..., 2] = 128
    q = pal[ex.quantize(img, lut)].astype(int)
    assert np.abs(q - img.astype(int)).mean() < 20


def test_export_frame(qapp, tmp_path, clip16x9):
    out = ex.export_frame(_proj(clip16x9), 1.0, str(tmp_path / "f.png"))
    from PySide6.QtGui import QImage
    img = QImage(out)
    assert img.width() == 320 and img.height() == 180


def test_export_dialog(tmp_path, clip16x9):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication; app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from PySide6.QtWidgets import QMessageBox
QMessageBox.exec = lambda self: 0
from kurgu.ui.main_window import MainWindow, ExportDialog
w = MainWindow(None); w.show(); app.processEvents()
(m,) = w.import_paths([{clip16x9!r}])
w.place_media(m, 0.0, None)
w.timeline.set_playhead(0.4); w.mark_in(); w.timeline.set_playhead(1.2); w.mark_out()
assert (w.project.seq_in, w.project.seq_out) == (0.4, 1.2)
d = ExportDialog(w, w.project, {str(tmp_path / 'x.mp4')!r}, w.settings)
assert d.range.currentData() == 'io'                     # I/O varsa varsayılan aralık
d.preset.setCurrentIndex(d.preset.findData('reels'))
path, o = d.values()
assert o['fmt_key'] == 'h264' and (o['width'], o['height']) == (1080, 1920)
assert abs(o['start'] - 0.4) < 1e-6 and abs(o['end'] - 1.2) < 1e-6
d.preset.setCurrentIndex(d.preset.findData('prores'))
assert d.path.text().endswith('.mov') and '10-bit' in d.summary.text()
d.fmt.setCurrentIndex(d.fmt.findData('gif'))
assert d.preset.currentData() == 'custom' and d.path.text().endswith('.gif')
w.clear_marks(); assert w.project.seq_in is None
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
