import os

import av
import numpy as np

from kurgu import colormatch as cm
from kurgu import effects as fx
from kurgu.media import SourcePool, probe
from kurgu.model import Project
from kurgu.render import Compositor, qimage_to_rgb

from conftest import make_video


def _teal_reference(path):
    """Pinterest tarzı: soğuk camgöbeği sahne + altta beyaz ayraçlı palet şeridi."""
    h, w = 240, 320
    img = np.zeros((h, w, 3), np.uint8)
    yy = np.linspace(0, 1, h)[:, None]
    img[..., 0] = (40 + 50 * yy).astype(np.uint8)
    img[..., 1] = (80 + 60 * yy).astype(np.uint8)
    img[..., 2] = (90 + 50 * yy).astype(np.uint8)
    img[200:, :] = 255  # beyaz ayraç
    for i in range(5):
        img[205:235, 5 + i * 63:60 + i * 63] = (60 + i * 10, 100 + i * 12, 110 + i * 8)
    f = av.VideoFrame.from_ndarray(img, format="rgb24")
    with av.open(str(path), "w") as c:
        s = c.add_stream("png")
        s.width, s.height, s.pix_fmt = w, h, "rgb24"
        for pk in s.encode(f):
            c.mux(pk)
        for pk in s.encode():
            c.mux(pk)
    return str(path)


def test_transfer_matches_statistics():
    rng = np.random.default_rng(1)
    src = rng.normal([200, 120, 60], [30, 20, 10], (5000, 3))
    ref = rng.normal([60, 110, 120], [15, 25, 20], (5000, 3))
    s, r = cm.stats(src), cm.stats(ref)
    m = cm.transfer_matrix(s["mean"], s["cov"], r["mean"], r["cov"])
    out = src @ m[:, :3].T + m[:, 3]
    assert np.allclose(out.mean(0), ref.mean(0), atol=1.0)
    assert np.allclose(np.cov(out, rowvar=False), np.cov(ref, rowvar=False), rtol=0.08, atol=6)
    # Parlaklığı koru: renk değişir ama luma aynı kalır
    m2 = cm.transfer_matrix(s["mean"], s["cov"], r["mean"], r["cov"], match_luma=False)
    out2 = src @ m2[:, :3].T + m2[:, 3]
    assert np.allclose(out2 @ cm.LUMA, src @ cm.LUMA, atol=1e-3)


def test_reference_image_to_warm_clip(qapp, tmp_path):
    ref_path = _teal_reference(tmp_path / "pinterest.png")
    warm = make_video(tmp_path / "warm.mp4", 2, color=(210, 140, 80))
    p = Project(320, 240, 25)
    ref_media = p.add_media(probe(ref_path))
    assert ref_media.kind == "image"
    ref = cm.reference_from_pixels(cm.sample_media_pixels(p, ref_media), ref_media.name)
    assert len(ref["ref_palette"]) == 6 and "#ffffff" not in ref["ref_palette"]
    clip = p.place_media(p.add_media(probe(warm)), 0.0)[0]
    cm.apply_reference(p, clip, ref)
    comp = Compositor(p, SourcePool())
    px = qimage_to_rgb(comp.compose(1.0, 320, 240))[150, 160].astype(int)
    assert px[2] > px[0] and px[1] > px[0]  # sıcak turuncu → soğuk camgöbeği


def test_cube_lut_and_chain_order(qapp, tmp_path):
    # Basit "log → normal" LUT: kontrastı açan bir eğri (her kanal x^0.6 değil, x^1.8)
    n = 9
    path = tmp_path / "dlog_to_709.cube"
    with open(path, "w") as f:
        f.write('TITLE "test"\nLUT_3D_SIZE %d\n' % n)
        for b in range(n):
            for g in range(n):
                for r in range(n):
                    v = [(x / (n - 1)) ** 1.8 for x in (r, g, b)]
                    f.write('%.6f %.6f %.6f\n' % tuple(v))
    cube = fx.load_cube(str(path))
    assert cube.shape == (n, n, n, 3) and abs(cube[n - 1, 0, 0, 0] - 1) < 1e-6
    arr = np.full((4, 4, 3), 128, np.uint8)
    e = fx.new_effect("lut")
    e["params"]["path"] = str(path)
    out = fx.apply_effects(arr.copy(), [e], {"scale": 1})
    assert abs(int(out[0, 0, 0]) - round(255 * (128 / 255) ** 1.8)) <= 3
    # Renk eşleştirme, kendinden önceki LUT'u hesaba katar
    vid = make_video(tmp_path / "flat.mp4", 1, color=(128, 128, 128))
    p = Project(320, 240, 25)
    clip = p.place_media(p.add_media(probe(vid)), 0.0)[0]
    clip.effects.append(e)
    eff = cm.apply_reference(p, clip, {"ref_name": "x", "ref_mean": [10, 20, 30],
                                       "ref_cov": np.eye(3).tolist(), "ref_palette": []})
    assert clip.effects[-1] is eff and eff["params"]["src_mean"][0] < 100  # LUT'lu ölçüm
