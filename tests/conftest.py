import os

import av
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def make_video(path, seconds=2.0, fps=25, w=320, h=240, color=(255, 0, 0), tone=440.0):
    """Test için düz renkli, sinüs sesli küçük bir video üretir."""
    out = av.open(str(path), "w")
    vs = out.add_stream("mpeg4", rate=fps)
    vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
    a = out.add_stream("aac", rate=48000)
    a.layout = "stereo"
    n = int(seconds * fps)
    for i in range(n):
        img = np.zeros((h, w, 3), np.uint8)
        img[:] = color
        img[: h // 4, : int(w * i / n) + 1] = 255  # ilerleme çubuğu
        f = av.VideoFrame.from_ndarray(img, format="rgb24")
        f.pts = i
        for p in vs.encode(f):
            out.mux(p)
    sr, chunk = 48000, 1024
    total = int(seconds * sr)
    for s in range(0, total, chunk):
        t = (np.arange(s, min(s + chunk, total)) / sr).astype(np.float32)
        x = (0.3 * np.sin(2 * np.pi * tone * t)).astype(np.float32)
        af = av.AudioFrame.from_ndarray(np.stack([x, x]), format="fltp", layout="stereo")
        af.sample_rate = sr
        af.pts = s
        for p in a.encode(af):
            out.mux(p)
    for s in (vs, a):
        for p in s.encode():
            out.mux(p)
    out.close()
    return str(path)


@pytest.fixture(scope="session")
def red_video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("m") / "red.mp4", color=(255, 0, 0))


@pytest.fixture(scope="session")
def blue_video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("m") / "blue.mp4", color=(0, 0, 255), tone=880)


@pytest.fixture(scope="session")
def qapp():
    from kurgu.render import ensure_gui_app
    ensure_gui_app()
    from PySide6.QtWidgets import QApplication
    return QApplication.instance()
