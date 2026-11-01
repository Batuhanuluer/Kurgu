"""Renk eşleştirme (Premiere Lumetri "Color Match" benzeri) ve palet çıkarma.

Yöntem: doğrusal Monge-Kantorovich renk aktarımı (Pitié & Kokaram, 2007).
Kaynak klibin ve referans görselin RGB dağılımları (ortalama + kovaryans) ölçülür;
kaynağı referansa taşıyan en iyi afin dönüşüm hesaplanır:

    T = Σs^-½ (Σs^½ Σr Σs^½)^½ Σs^-½,   çıktı = T (x − μs) + μr

Bu dönüşüm tek bir 3×3 matris + kaydırma olduğu için her karede yalnızca bir matris
çarpımıdır (hızlı) ve klip boyunca sabit kalır (titreme yapmaz).
"""
from __future__ import annotations

import numpy as np

from .media import SourcePool

LUMA = np.array([0.299, 0.587, 0.114])
SAMPLE_W = 192


def _pixels(arr: np.ndarray) -> np.ndarray:
    """(N, 3) piksel listesi; palet görsellerindeki saf beyaz ayırıcılar hariç."""
    px = arr[..., :3].reshape(-1, 3).astype(np.float64)
    if arr.shape[-1] == 4:
        px = px[arr[..., 3].reshape(-1) > 128]
    keep = ~np.all(px > 242, axis=1)
    return px[keep] if keep.sum() > 100 else px


def stats(px: np.ndarray) -> dict:
    mean = px.mean(axis=0)
    cov = np.cov(px, rowvar=False) + np.eye(3) * 1.0  # tek renkli görseller için düzenleme
    return {"mean": mean.tolist(), "cov": cov.tolist()}


def palette(px: np.ndarray, k: int = 6, iters: int = 12, seed: int = 0) -> list[str]:
    """Baskın renkler (k-ortalama), açıktan koyuya değil, en çok kullanılandan aza."""
    rng = np.random.default_rng(seed)
    if len(px) > 6000:
        px = px[rng.choice(len(px), 6000, replace=False)]
    # k-means++ başlangıcı
    centers = [px[rng.integers(len(px))]]
    for _ in range(1, k):
        d = np.min(((px[:, None, :] - np.array(centers)[None]) ** 2).sum(-1), axis=1)
        centers.append(px[rng.choice(len(px), p=d / max(d.sum(), 1e-9))])
    c = np.array(centers)
    for _ in range(iters):
        lab = np.argmin(((px[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
        for j in range(k):
            m = lab == j
            if m.any():
                c[j] = px[m].mean(axis=0)
    counts = np.bincount(lab, minlength=k)
    order = np.argsort(-counts)
    return ["#%02x%02x%02x" % tuple(int(v) for v in np.clip(c[j], 0, 255)) for j in order]


# ------------------------------------------------------------ ölçüm
def sample_clip_pixels(project, clip, n_frames: int = 8, pre=None) -> np.ndarray:
    """Klibin süresine yayılmış birkaç kareden küçük boyutlu pikseller."""
    m = project.media.get(clip.media_id)
    if m is None:
        raise ValueError("Klibin medyası bulunamadı")
    pool = SourcePool()
    try:
        src = pool.video_source(m)
        if src is None:
            raise ValueError(f"Açılamadı: {m.path}")
        if m.kind == "image":
            times = [0.0]
        else:
            a, b = clip.in_point, max(clip.out_point, clip.in_point + 1e-3)
            times = [a + (b - a) * (i + 0.5) / n_frames for i in range(n_frames)]
        chunks = []
        for t in times:
            f = src.frame_at(min(t, max(m.duration - 0.05, 0.0)) if m.kind != "image" else 0.0)
            if f is None:
                continue
            h = max(2, int(f.height * SAMPLE_W / max(f.width, 1)))
            fmt = "rgba" if pre is not None or f.format.name.startswith(
                ("rgba", "bgra", "argb", "abgr", "yuva")) else "rgb24"
            arr = f.to_ndarray(width=SAMPLE_W, height=h, format=fmt)
            if pre is not None:
                arr = pre(arr)
            chunks.append(_pixels(arr))
        if not chunks:
            raise ValueError("Kare okunamadı")
        return np.concatenate(chunks)
    finally:
        pool.close()


def sample_media_pixels(project, media) -> np.ndarray:
    """Proje medyasından (görsel ya da videonun tamamı) piksel örnekleri."""
    from .model import Clip
    dur = media.duration if media.kind != "image" else 1.0
    fake = Clip(track=0, start=0, in_point=0, out_point=max(dur, 1e-3), media_id=media.id)
    return sample_clip_pixels(project, fake)


def reference_from_pixels(px: np.ndarray, name: str) -> dict:
    s = stats(px)
    return {"ref_name": name, "ref_mean": s["mean"], "ref_cov": s["cov"],
            "ref_palette": palette(px)}


# ------------------------------------------------------------ aktarım
def _sqrtm(m: np.ndarray) -> np.ndarray:
    w, v = np.linalg.eigh((m + m.T) / 2)
    return (v * np.sqrt(np.clip(w, 1e-9, None))) @ v.T


def transfer_matrix(src_mean, src_cov, ref_mean, ref_cov, strength=1.0,
                    match_luma=True) -> np.ndarray:
    """3×4 afin matris: çıktı = M[:, :3] @ rgb + M[:, 3]."""
    ms, mr = np.asarray(src_mean, float), np.asarray(ref_mean, float)
    cs, cr = np.asarray(src_cov, float), np.asarray(ref_cov, float)
    cs_h = _sqrtm(cs)
    cs_ih = np.linalg.inv(cs_h)
    T = cs_ih @ _sqrtm(cs_h @ cr @ cs_h) @ cs_ih
    A = T
    c = mr - T @ ms
    if not match_luma:
        # Parlaklığı koru: yalnızca renk tonu aktarılsın
        one = np.ones((3, 1))
        w = LUMA[None, :]
        A = A + one @ w - one @ (w @ A)
        c = c - one[:, 0] * float((w @ c)[0])
    s = float(np.clip(strength, 0.0, 1.0))
    A = np.eye(3) + s * (A - np.eye(3))
    c = s * c
    return np.hstack([A, c[:, None]]).astype(np.float32)


def analyze_source(project, clip, effect: dict) -> dict:
    """Klibin, zincirde bu efektten ÖNCE gelen işlemler uygulanmış halinin istatistikleri
    (ör. D-Log → Rec.709 LUT'u önce gelirse eşleştirme LUT'lu görüntüye göre yapılır)."""
    from . import effects as fx
    from .render import adjust_color
    before = []
    for e in clip.effects:
        if e is effect or e.get("id") == effect.get("id"):
            break
        if e.get("enabled", True):
            before.append(e)
    scale = SAMPLE_W / max((project.media[clip.media_id].width or SAMPLE_W), 1)

    def pre(arr):
        arr = adjust_color(arr, clip)
        return fx.apply_effects(arr, before, {"scale": scale, "t": clip.start})
    px = sample_clip_pixels(project, clip, pre=pre)
    st = stats(px)
    return {"src_mean": st["mean"], "src_cov": st["cov"]}


def apply_reference(project, clip, ref: dict) -> dict:
    """Klibe renk eşleştirme efektini ekler ya da günceller; efekti döndürür."""
    from . import effects as fx
    eff = next((e for e in clip.effects if e["type"] == "color_match"), None)
    if eff is None:
        eff = fx.new_effect("color_match")
        clip.effects.append(eff)
    eff["params"].update({k: ref[k] for k in ("ref_name", "ref_mean", "ref_cov", "ref_palette")})
    eff["params"].update(analyze_source(project, clip, eff))
    return eff
