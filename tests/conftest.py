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


def make_camera(path, master, start, seconds, color, fps=25, w=160, h=90, sr=48000):
    """Ortak bir ses kaydının [start, start+seconds) bölümünü taşıyan düz renkli 'kamera'."""
    out = av.open(str(path), "w")
    vs = out.add_stream("mpeg4", rate=fps)
    vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
    a = out.add_stream("aac", rate=sr)
    a.layout = "mono"
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = color
    for i in range(int(seconds * fps)):
        f = av.VideoFrame.from_ndarray(img, format="rgb24")
        f.pts = i
        for p in vs.encode(f):
            out.mux(p)
    seg = master[int(start * sr):int((start + seconds) * sr)].astype(np.float32)
    for s in range(0, len(seg), 1024):
        af = av.AudioFrame.from_ndarray(seg[None, s:s + 1024], format="fltp", layout="mono")
        af.sample_rate = sr
        af.pts = s
        for p in a.encode(af):
            out.mux(p)
    for s in (vs, a):
        for p in s.encode():
            out.mux(p)
    out.close()
    return str(path)


def burst_audio(seconds, sr=48000, seed=5):
    rng = np.random.default_rng(seed)
    x = np.zeros(int(seconds * sr), np.float32)
    for _ in range(int(seconds * 3)):
        s = rng.integers(0, len(x) - sr // 4)
        n = int(rng.integers(sr // 60, sr // 6))
        x[s:s + n] += (rng.normal(0, 0.3, n) * np.exp(-np.arange(n) / (n / 3))).astype(np.float32)
    return x


@pytest.fixture(scope="session")
def cameras(tmp_path_factory):
    """Üç kamera: aynı olayın sesi; 0 s, 1.24 s ve 0.52 s'de başlıyorlar."""
    d = tmp_path_factory.mktemp("cams")
    master = burst_audio(12)
    return [make_camera(d / "cam1.mp4", master, 0.0, 8, (220, 40, 40)),
            make_camera(d / "cam2.mp4", master, 1.24, 8, (40, 200, 40)),
            make_camera(d / "cam3.mp4", master, 0.52, 6, (40, 60, 230))], [0.0, 1.24, 0.52]
