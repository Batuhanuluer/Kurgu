"""Lumetri tarzı renk düzeltme (Premiere "Lumetri Color").

Tek bir efekt ("lumetri"), Premiere'deki panelin bölümlerini içerir:

- Temel: beyaz dengesi (sıcaklık, ton), pozlama, kontrast, parlak alanlar, gölgeler,
  beyazlar, siyahlar, doygunluk, canlılık
- Eğriler: ana (RGB) eğri ve ayrı R, G, B eğrileri (noktalardan geçen yumuşak eğri)
- Renk tekerlekleri: gölgeler (lift), orta tonlar (gamma), parlak alanlar (gain) için
  renk kayması + parlaklık

Hız için doygunluk dışındaki her şey kanal başına 256 girişli tek bir tabloya (1B LUT)
toplanır; tablo parametreler değişmedikçe önbellekte kalır. Böylece 4K karede bile
düzeltme birkaç milisaniye sürer.
"""
from __future__ import annotations

import math

import numpy as np

# Parametre anahtarı -> (etiket, min, maks, varsayılan, adım)
BASIC = [
    ("temperature", "Temperature", -100.0, 100.0, 0.0, 1.0),
    ("tint", "Tint", -100.0, 100.0, 0.0, 1.0),
    ("exposure", "Exposure", -4.0, 4.0, 0.0, 0.05),
    ("contrast", "Contrast", -100.0, 100.0, 0.0, 1.0),
    ("highlights", "Highlights", -100.0, 100.0, 0.0, 1.0),
    ("shadows", "Shadows", -100.0, 100.0, 0.0, 1.0),
    ("whites", "Whites", -100.0, 100.0, 0.0, 1.0),
    ("blacks", "Blacks", -100.0, 100.0, 0.0, 1.0),
    ("saturation", "Saturation", 0.0, 200.0, 100.0, 1.0),
    ("vibrance", "Vibrance", -100.0, 100.0, 0.0, 1.0),
]
WHEELS = [("lift", "Shadows"), ("gamma", "Midtones"), ("gain", "Highlights")]
CURVES = ["curve_master", "curve_r", "curve_g", "curve_b"]
IDENTITY_CURVE = [[0.0, 0.0], [1.0, 1.0]]


def defaults() -> dict:
    p = {k: d for k, _l, _a, _b, d, _s in BASIC}
    for k, _l in WHEELS:
        p[k] = [0.0, 0.0]          # tekerlek: renk kayması (x, y) birim daire içinde
        p[k + "_luma"] = 0.0       # tekerlek yanındaki parlaklık kaydırıcısı (-100..100)
    for k in CURVES:
        p[k] = [list(pt) for pt in IDENTITY_CURVE]
    return p


# ------------------------------------------------------------ yardımcılar
def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# Tekerlekte kırmızının açısı: vektörskoptaki yeriyle aynı (~103°, Premiere/Resolve gibi)
HUE_OFFSET = 103.0


def wheel_rgb(xy) -> np.ndarray:
    """Tekerlekteki nokta (x sağ, y yukarı) -> RGB kayma yönü (toplamı ~0, renk tonu değişir).
    Açı = renk tonu (103° kırmızı, saat yönünün tersine sarı, yeşil, camgöbeği, mavi,
    macenta — vektörskoptaki sırayla aynı), uzaklık = miktar."""
    x, y = float(xy[0]), float(xy[1])
    r = min(math.hypot(x, y), 1.0)
    if r < 1e-6:
        return np.zeros(3, np.float32)
    a = math.atan2(y, x) - math.radians(HUE_OFFSET)
    v = np.array([math.cos(a), math.cos(a - 2 * math.pi / 3), math.cos(a + 2 * math.pi / 3)],
                 np.float32)
    return v * r


def monotone_curve(points, n: int = 256) -> np.ndarray:
    """Noktalardan geçen, taşma yapmayan yumuşak eğri (Fritsch–Carlson monoton kübik).
    points: [[x, y], ...] 0..1 aralığında. Dönüş: n örnek."""
    pts = sorted((min(max(float(x), 0), 1), min(max(float(y), 0), 1)) for x, y in points)
    xs, ys = [], []
    for x, y in pts:  # aynı x'te tek nokta
        if xs and abs(x - xs[-1]) < 1e-6:
            ys[-1] = y
        else:
            xs.append(x)
            ys.append(y)
    t = np.linspace(0, 1, n, dtype=np.float64)
    if len(xs) == 1:
        return np.full(n, ys[0], np.float32)
    xs, ys = np.array(xs), np.array(ys)
    h = np.diff(xs)
    d = np.diff(ys) / h
    m = np.empty(len(xs))
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, len(xs) - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else \
            (3 * (h[i - 1] + h[i])) / ((2 * h[i] + h[i - 1]) / d[i - 1] + (h[i] + 2 * h[i - 1]) / d[i])
    i = np.clip(np.searchsorted(xs, t, side="right") - 1, 0, len(xs) - 2)
    hh = h[i]
    s = (t - xs[i]) / hh
    h00 = (1 + 2 * s) * (1 - s) ** 2
    h10 = s * (1 - s) ** 2
    h01 = s * s * (3 - 2 * s)
    h11 = s * s * (s - 1)
    y = h00 * ys[i] + h10 * hh * m[i] + h01 * ys[i + 1] + h11 * hh * m[i + 1]
    y = np.where(t < xs[0], ys[0], np.where(t > xs[-1], ys[-1], y))
    return np.clip(y, 0, 1).astype(np.float32)


def is_identity_curve(points) -> bool:
    return not points or [[round(float(a), 4), round(float(b), 4)] for a, b in
                          sorted(points)] == IDENTITY_CURVE


# ------------------------------------------------------------ tonal tablo
def tone_tables(p: dict, n: int = 256) -> np.ndarray:
    """(n, 3) float32: her kanal için giriş 0..1 -> çıkış 0..1 (doygunluk hariç her şey)."""
    x = np.linspace(0, 1, n, dtype=np.float32)
    v = np.stack([x, x, x], axis=1)  # (n, 3)
    # Beyaz dengesi (kanal kazançları)
    t, g = p.get("temperature", 0) / 100.0, p.get("tint", 0) / 100.0
    gains = np.array([1 + 0.22 * t + 0.08 * g, 1 - 0.18 * g, 1 - 0.22 * t + 0.08 * g],
                     np.float32)
    v = v * gains
    # Pozlama: doğrusal ışıkta çarp
    e = p.get("exposure", 0.0)
    if e:
        v = np.power(np.clip(v, 0, None), 2.2) * (2.0 ** e)
        v = np.power(v, 1 / 2.2)
    # Kontrast: orta griden S eğrisi / yassılaştırma
    c = p.get("contrast", 0) / 100.0
    if c:
        s = np.clip(v, 0, 1)
        sc = s * s * (3 - 2 * s)  # S eğrisi
        v = v + (sc - s) * c * 1.4 if c > 0 else 0.5 + (v - 0.5) * (1 + c * 0.7)
    # Parlak alanlar / gölgeler: yalnız o bölgeyi yumuşakça it
    hl = p.get("highlights", 0) / 100.0
    if hl:
        w = smoothstep(0.45, 1.0, np.clip(v, 0, 1))
        v = v + hl * 0.35 * w * (np.clip(1 - v, 0, 1) if hl > 0 else np.clip(v, 0, 1))
    sh = p.get("shadows", 0) / 100.0
    if sh:
        w = 1 - smoothstep(0.0, 0.55, np.clip(v, 0, 1))
        v = v + sh * 0.35 * w * (np.clip(1 - v, 0, 1) if sh > 0 else np.clip(v, 0, 1))
    # Beyazlar / siyahlar: uç noktalar
    wh = p.get("whites", 0) / 100.0
    if wh:
        v = v / (1 - 0.25 * wh) if wh > 0 else v * (1 + 0.25 * wh)
    bl = p.get("blacks", 0) / 100.0
    if bl:
        k = 0.15 * bl
        v = k + v * (1 - k) if bl > 0 else (v + k) / (1 + k)
    v = np.clip(v, 0, 1)
    # Eğriler
    master = p.get("curve_master")
    if master and not is_identity_curve(master):
        v = np.interp(v, x, monotone_curve(master, n))
    for ch, key in enumerate(("curve_r", "curve_g", "curve_b")):
        pts = p.get(key)
        if pts and not is_identity_curve(pts):
            v[:, ch] = np.interp(v[:, ch], x, monotone_curve(pts, n))
    # Renk tekerlekleri (ASC CDL benzeri lift / gamma / gain)
    lift = wheel_rgb(p.get("lift", [0, 0])) * 0.25 + p.get("lift_luma", 0) / 100.0 * 0.25
    gam = wheel_rgb(p.get("gamma", [0, 0])) * 0.5 + p.get("gamma_luma", 0) / 100.0 * 0.6
    gain = wheel_rgb(p.get("gain", [0, 0])) * 0.4 + p.get("gain_luma", 0) / 100.0 * 0.5
    if np.any(lift):
        v = v + lift * (1 - v)
    if np.any(gam):
        v = np.power(np.clip(v, 0, 1), 1.0 / np.clip(1 + gam, 0.2, 5.0))
    if np.any(gain):
        v = v * (1 + gain)
    return np.clip(v, 0, 1).astype(np.float32)


_CACHE: dict = {}


def _key(p: dict):
    def norm(v):
        if isinstance(v, (list, tuple)):
            return tuple(norm(x) for x in v)
        if isinstance(v, float):
            return round(v, 5)
        return v
    return tuple(sorted((k, norm(v)) for k, v in p.items() if k != "_"))


def tables_u8(p: dict):
    """Önbellekli (256, 3) uint8 tablo + doygunluk gerekiyor mu."""
    k = _key(p)
    hit = _CACHE.get(k)
    if hit is None:
        t = tone_tables(p)
        hit = ((t * 255 + 0.5).astype(np.uint8), t)
        if len(_CACHE) > 32:
            _CACHE.clear()
        _CACHE[k] = hit
    return hit


def is_identity(p: dict) -> bool:
    d = defaults()
    for k, v in p.items():
        if k in CURVES:
            if not is_identity_curve(v):
                return False
        elif k in d and isinstance(d[k], list):
            if any(abs(float(a)) > 1e-6 for a in v):
                return False
        elif k in d and abs(float(v) - float(d[k])) > 1e-6:
            return False
    return True


def apply(arr: np.ndarray, p: dict, dither=None) -> np.ndarray:
    """arr: (H, W, 3|4) uint8, yerinde değiştirilir (büyük karede çekirdekler arasında)."""
    if is_identity(p):
        return arr
    from .parallel import rows
    tables_u8(p)  # tablo tek seferde, şeritlerden önce
    rows(lambda a, d: _apply8(a, p, d), arr, dither)
    return arr


def _apply8(arr: np.ndarray, p: dict, dither=None) -> np.ndarray:
    lut8, lutf = tables_u8(p)
    sat = p.get("saturation", 100.0) / 100.0
    vib = p.get("vibrance", 0.0) / 100.0
    rgb = arr[..., :3]
    idx = rgb.astype(np.uint16)
    idx[..., 1] += 256
    idx[..., 2] += 512
    if abs(sat - 1) < 1e-6 and abs(vib) < 1e-6:
        arr[..., :3] = np.ascontiguousarray(lut8.T).ravel()[idx]  # tek toplama işlemi
        return arr
    f = (np.ascontiguousarray(lutf.T).ravel() * 255)[idx]
    w = np.array([0.2126, 0.7152, 0.0722], np.float32)
    if abs(vib) < 1e-6:
        # Doygunluk doğrusal: Y + (c - Y)·k  =  M·c  (tek matris çarpımı)
        m = (sat * np.eye(3) + (1 - sat) * np.outer(np.ones(3), w)).astype(np.float32)
        f = (f.reshape(-1, 3) @ m.T).reshape(f.shape)
    else:
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        y = r * w[0] + g * w[1] + b * w[2]
        # Canlılık: az doygun renkleri daha çok, zaten doygun olanları daha az etkiler
        cur = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
        k = 1 - np.minimum(cur * (1.5 / 255.0), 1.0)
        k *= sat * vib
        k += sat
        f -= y[..., None]
        f *= k[..., None]
        f += y[..., None]
    if dither is not None:
        f += dither
    f += 0.5
    np.clip(f, 0, 255, out=f)
    arr[..., :3] = f.astype(np.uint8)
    return arr


_CACHE16: dict = {}


def tables16(p: dict):
    """16 bit giriş için kanal başına 65536 girişli tablo (bir kez hesaplanır):
    uint16 sonuç tablosu ve float (0..65535) tablo, ikisi de kanal kanal düzleştirilmiş."""
    k = _key(p)
    hit = _CACHE16.get(k)
    if hit is None:
        lut = tone_tables(p, 4097)  # 12 bit çözünürlük, aradaki değerler doğrusal
        x = np.linspace(0, 4096, 65536, dtype=np.float32)
        full = np.stack([np.interp(x, np.arange(4097, dtype=np.float32), lut[:, ch])
                         for ch in range(3)])  # (3, 65536)
        f = (full * 65535).astype(np.float32).ravel()
        u = np.clip(f + 0.5, 0, 65535).astype(np.uint16)
        hit = (u, f)
        if len(_CACHE16) > 8:
            _CACHE16.clear()
        _CACHE16[k] = hit
    return hit


def apply16(arr: np.ndarray, p: dict) -> np.ndarray:
    """16 bit (uint16, 0..65535) RGB dizisinde aynı düzeltme: 10-bit dışa aktarma yolunda
    bantlanmasız sonuç için. Tek tablo okumasıyla (8-bit yol gibi) hızlıdır."""
    if is_identity(p):
        return arr
    from .parallel import rows
    tables16(p)
    rows(lambda a: _apply16(a, p), arr)
    return arr


def _apply16(arr: np.ndarray, p: dict) -> np.ndarray:
    u, f16 = tables16(p)
    rgb = arr[..., :3]
    idx = rgb.astype(np.uint32)
    idx[..., 1] += 65536
    idx[..., 2] += 131072
    sat = p.get("saturation", 100.0) / 100.0
    vib = p.get("vibrance", 0.0) / 100.0
    if abs(sat - 1) < 1e-6 and abs(vib) < 1e-6:
        arr[..., :3] = u[idx]
        return arr
    f = f16[idx]
    w = np.array([0.2126, 0.7152, 0.0722], np.float32)
    if abs(vib) < 1e-6:
        m = (sat * np.eye(3) + (1 - sat) * np.outer(np.ones(3), w)).astype(np.float32)
        f = (f.reshape(-1, 3) @ m.T).reshape(f.shape)
    else:
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        y = r * w[0] + g * w[1] + b * w[2]
        cur = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
        k = 1 - np.minimum(cur * (1.5 / 65535.0), 1.0)
        k *= sat * vib
        k += sat
        f -= y[..., None]
        f *= k[..., None]
        f += y[..., None]
    f += 0.5
    np.clip(f, 0, 65535, out=f)
    arr[..., :3] = f.astype(np.uint16)
    return arr
