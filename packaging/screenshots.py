"""README ekran görüntülerini üretir (ekran gerektirmez, Qt "offscreen").

    python packaging/screenshots.py [çıkış_klasörü]   # varsayılan: docs/screenshots

Görüntülerdeki tüm medya burada kodla üretilir (telifsiz manzara + konuşmaya benzer ses).
Dil KURGU_LANG ile seçilir (varsayılan en).
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KURGU_LANG", "en")

import av  # noqa: E402
import numpy as np  # noqa: E402

PALETTES = {  # gökyüzü üst, ufuk, güneş, tepeler (önden arkaya)
    "dawn": ((40, 52, 110), (250, 160, 120), (255, 236, 180),
             [(70, 60, 110), (52, 44, 88), (30, 26, 56)]),
    "noon": ((60, 140, 230), (190, 225, 250), (255, 250, 220),
             [(110, 170, 90), (70, 135, 70), (40, 95, 55)]),
    "dusk": ((25, 20, 60), (240, 110, 70), (255, 200, 120),
             [(120, 50, 70), (80, 30, 60), (40, 16, 40)]),
}


def scene(w, h, t, pal):
    top, hor, sun, hills = PALETTES[pal]
    y = np.linspace(0, 1, h)[:, None, None]
    sky = (np.array(top) * (1 - y) + np.array(hor) * y) * np.ones((1, w, 1))
    img = sky.astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy, r = w * (0.25 + 0.08 * t), h * (0.55 - 0.05 * t), h * 0.12
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    glow = np.clip(1.6 - d / (r * 2.4), 0, 1)[..., None] ** 2
    img = img * (1 - glow * 0.55) + np.array(sun) * glow * 0.55
    img[d < r] = sun
    x = np.arange(w)
    for i, col in enumerate(hills):
        base = h * (0.58 + 0.12 * i)
        amp = h * (0.07 - 0.015 * i)
        ph = t * (0.3 + 0.35 * i)
        ridge = base + amp * np.sin(x / w * (5 + 3 * i) + ph + i) \
            + amp * 0.4 * np.sin(x / w * (17 + 5 * i) - ph)
        img[yy > ridge[None, :]] = col
    return np.clip(img, 0, 255).astype(np.uint8)


def speech(seconds, sr=48000, seed=0, pitch=140.0):
    """Konuşmaya benzeyen ses: hece zarflı, perde kayan harmonikler."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    f0 = pitch * (1 + 0.08 * np.sin(2 * np.pi * 0.7 * t + seed))
    phase = 2 * np.pi * np.cumsum(f0) / sr
    x = sum(np.sin(k * phase) / k for k in range(1, 7))
    env = np.zeros(n)
    pos = 0.2
    while pos < seconds - 0.3:
        dur = rng.uniform(0.12, 0.3)
        a, b = int(pos * sr), int(min(pos + dur, seconds) * sr)
        env[a:b] = np.hanning(b - a) * rng.uniform(0.5, 1.0)
        pos += dur + (rng.uniform(0.35, 0.7) if rng.random() < 0.18 else rng.uniform(0.02, 0.08))
    return (0.3 * x * env).astype(np.float32)


def make_clip(path, pal, seconds=6.0, w=640, h=360, fps=25, seed=0, pitch=140.0):
    out = av.open(path, "w")
    vs = out.add_stream("mpeg4", rate=fps)
    vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
    vs.bit_rate = 4_000_000
    a = out.add_stream("aac", rate=48000)
    a.layout = "stereo"
    for i in range(int(seconds * fps)):
        f = av.VideoFrame.from_ndarray(scene(w, h, i / fps, pal), format="rgb24")
        f.pts = i
        for p in vs.encode(f):
            out.mux(p)
    x = speech(seconds, seed=seed, pitch=pitch)
    for s in range(0, len(x), 1024):
        c = x[s:s + 1024]
        af = av.AudioFrame.from_ndarray(np.stack([c, c]), format="fltp", layout="stereo")
        af.sample_rate = 48000
        af.pts = s
        for p in a.encode(af):
            out.mux(p)
    for s in (vs, a):
        for p in s.encode():
            out.mux(p)
    out.close()
    return path


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="kurgu-shots-")
    os.environ["HOME"] = os.environ["USERPROFILE"] = tmp  # kullanıcı ayarlarına dokunma
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    from kurgu.app import apply_dark_theme
    apply_dark_theme(app)
    import kurgu.ui.player as pl
    pl.HAVE_AUDIO = False
    from kurgu import captions as cap
    from kurgu import effects as fx
    from kurgu import grading as gr
    from kurgu import keyframes as kf
    from kurgu.ui.main_window import MainWindow

    clips = [make_clip(os.path.join(tmp, f"{n}.mp4"), n, seed=i, pitch=120 + 30 * i)
             for i, n in enumerate(("dawn", "noon", "dusk"))]
    w = MainWindow(None)
    w.resize(1600, 960)
    w.show()
    app.processEvents()
    w._mismatch = lambda m: False  # "projeyi klibe uydur?" sorusu açılmasın
    w.project.width, w.project.height = 640, 360
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.exec = lambda self: 0
    items = w.import_paths(clips)
    t = 0.0
    for m in items:
        w.place_media(m, t, None)
        t += 6.0
    p = w.project
    v1 = p.track_indices("video")[0]
    first = [c for c in p.clips if c.track == v1][0]
    second = sorted((c for c in p.clips if c.track == v1), key=lambda c: c.start)[1]
    second.trans_in, second.trans_in_dur = "dissolve", 1.0
    e = fx.new_effect("lumetri")
    e["params"].update({"temperature": 18.0, "saturation": 115.0, "contrast": 12.0})
    e["params"]["gain"] = [0.12, 0.05]
    second.effects.append(e)
    kf.set_key(first, "scale", first.source_time(0.0), 1.0, p.fps)
    kf.set_key(first, "scale", first.source_time(5.0), 1.2, p.fps)
    title = p.add_text("Sunrise", 0.5)
    title.out_point = 3.0
    title.font_size, title.pos_y = 46, -95
    words, pos = [], 0.4
    for wd in "We left before sunrise to catch the first light over the hills".split():
        words.append((pos, pos + 0.32, wd))
        pos += 0.4
    p.add_captions(cap.words_to_cues(words))
    ti = p.caption_track(create=False)
    p.tracks[ti].style.update(cap.REELS_STYLE)
    cap.rechunk(p, ti, 3)
    w.on_edited()
    w.source.load(items[1])
    w.timeline.selected = {second.id}
    w._on_selection()
    w.timeline.set_playhead(1.9)
    w.timeline.zoom_fit()
    for _ in range(5):
        app.processEvents()
    w.monitor.refresh(w.timeline.playhead)
    app.processEvents()
    w.grab().save(os.path.join(out_dir, "editing.png"))

    # Renk çalışma alanı
    w.color_workspace()
    w.color_panel.set_clip(second)
    w.timeline.set_playhead(9.0)
    for _ in range(5):
        app.processEvents()
    w.monitor.refresh(9.0)
    w.scopes.refresh() if hasattr(w, "scopes") and hasattr(w.scopes, "refresh") else None
    app.processEvents()
    w.grab().save(os.path.join(out_dir, "color.png"))

    # Altyazılar ve mikser
    w.show_dock(w.captions_dock)
    w.show_dock(w.mixer_dock)
    w.timeline.set_playhead(1.5)
    for _ in range(5):
        app.processEvents()
    w.monitor.refresh(1.5)
    app.processEvents()
    w.grab().save(os.path.join(out_dir, "captions.png"))
    w.dirty = False
    w.close()
    _ = gr
    print("Ekran görüntüleri:", out_dir)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screenshots"))
