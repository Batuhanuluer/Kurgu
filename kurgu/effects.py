"""Video efektleri (filtreler).

Her efekt, numpy dizisi (H, W, 3 veya 4, uint8) üzerinde çalışan bir
fonksiyondur. Yeni bir efekt eklemek için `register` ile kaydetmek yeterli;
arayüz (Efektler paneli ve denetçi) parametrelerden otomatik oluşur.

Parametre tanımı: (anahtar, etiket, tür, varsayılan, min, maks, adım, ondalık)
tür: "float", "bool" veya "color".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


@dataclass
class EffectDef:
    id: str
    name: str
    category: str
    apply: Callable
    params: list = field(default_factory=list)
    needs_alpha: bool = False


EFFECTS: dict[str, EffectDef] = {}
CATEGORIES = ["Keying", "Color Correction", "Blur & Sharpen", "Stylize", "Transform"]


def register(id, name, category, params=(), needs_alpha=False):
    def deco(fn):
        EFFECTS[id] = EffectDef(id, name, category, fn, list(params), needs_alpha)
        return fn
    return deco


def new_effect(effect_id: str) -> dict:
    d = EFFECTS[effect_id]
    import uuid
    return {"type": effect_id, "enabled": True, "id": uuid.uuid4().hex[:8],
            "params": {p[0]: p[3] for p in d.params}}


def needs_alpha(effects: list) -> bool:
    return any(e.get("enabled", True) and e["type"] in EFFECTS
               and EFFECTS[e["type"]].needs_alpha for e in effects)


def apply_effects(arr: np.ndarray, effects: list, ctx: dict) -> np.ndarray:
    """ctx: {"scale": dizi pikseli / proje pikseli, "t": zaman}"""
    for e in effects:
        if not e.get("enabled", True):
            continue
        d = EFFECTS.get(e["type"])
        if d is None:
            continue
        params = {p[0]: p[3] for p in d.params}
        params.update(e.get("params", {}))
        arr = d.apply(arr, params, ctx)
    return arr


# ------------------------------------------------------------ yardımcılar
def hex_to_rgb(h: str):
    h = h.lstrip("#")
    if len(h) != 6:
        return (0, 255, 0)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb(arr):
    return arr[..., :3].astype(np.float32)


def _put(arr, rgb):
    arr[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return arr


def _mix(arr, rgb_new, amount):
    if amount >= 1:
        return _put(arr, rgb_new)
    old = _rgb(arr)
    return _put(arr, old + (rgb_new - old) * amount)


def _box1d(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    a = np.moveaxis(a, axis, 0)
    pad = np.concatenate([np.repeat(a[:1], r + 1, 0), a, np.repeat(a[-1:], r, 0)])
    c = np.cumsum(pad, axis=0, dtype=np.float32)
    out = (c[2 * r + 1:] - c[:-2 * r - 1]) / (2 * r + 1)
    return np.moveaxis(out, 0, axis)


def blur_array(a: np.ndarray, radius: float, passes: int = 3) -> np.ndarray:
    """Üç geçişli kutu bulanıklığı ≈ Gauss bulanıklığı (yarıçaptan bağımsız hız)."""
    r = int(round(radius))
    if r < 1:
        return a
    out = a.astype(np.float32)
    h, w = out.shape[:2]
    r_y, r_x = min(r, max(1, h - 1)), min(r, max(1, w - 1))
    for _ in range(passes):
        out = _box1d(out, r_x, 1)
        out = _box1d(out, r_y, 0)
    return out


def _lut(arr, lut):
    arr[..., :3] = lut[arr[..., :3]]
    return arr


_GRID_CACHE: dict = {}


def _radial(h, w):
    key = (h, w)
    g = _GRID_CACHE.get(key)
    if g is None:
        y = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
        x = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
        g = np.sqrt(x * x + y * y) / math.sqrt(2)
        if len(_GRID_CACHE) > 8:
            _GRID_CACHE.clear()
        _GRID_CACHE[key] = g
    return g


# ------------------------------------------------------------ anahtarlama
def _cbcr(r, g, b):
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b
    return cb, cr


@register("chroma_key", "Chroma Key", "Keying", [
    ("color", "Key Color", "color", "#00ff00", 0, 0, 0, 0),
    ("tolerance", "Tolerance", "float", 0.5, 0.0, 1.0, 0.01, 2),
    ("softness", "Edge Softness", "float", 0.2, 0.0, 1.0, 0.01, 2),
    ("spill", "Spill Suppression", "float", 0.6, 0.0, 1.0, 0.05, 2),
    ("choke", "Choke", "float", 0.0, 0.0, 1.0, 0.05, 2),
], needs_alpha=True)
def _chroma_placeholder(arr, p, ctx):
    return chroma_key(arr, p, ctx)


def _chroma_alpha(rgb, key, tolerance, softness, choke):
    """Anahtar renk yönüne benzerlikten saydamlık (0..1). rgb: (..., 3) float."""
    kr, kg, kb = key
    cb, cr = _cbcr(rgb[..., 0], rgb[..., 1], rgb[..., 2])
    kcb, kcr = _cbcr(kr, kg, kb)
    kmag = max(math.hypot(kcb, kcr), 20.0)
    ux, uy = kcb / kmag, kcr / kmag
    along = cb * ux + cr * uy
    perp = np.abs(-cb * uy + cr * ux)
    score = (along - 1.5 * perp) / kmag
    lo = 0.3 * (1.0 - tolerance)
    hi = lo + max(softness, 0.01) * 0.5
    a = 1.0 - np.clip((score - lo) / (hi - lo), 0.0, 1.0)
    if choke > 0:
        a = np.clip((a - choke * 0.5) / max(1 - choke * 0.5, 1e-3), 0, 1)
    return a


_LUT_BITS = 6
_CHROMA_LUTS: dict = {}


def _chroma_lut(key, tolerance, softness, choke) -> np.ndarray:
    """64×64×64 renk küpü için saydamlık tablosu (uint8). Parametreler değişmedikçe
    bir kez hesaplanır; her karede piksel başına formül yerine tablodan okunur."""
    k = (key, round(tolerance, 4), round(softness, 4), round(choke, 4))
    lut = _CHROMA_LUTS.get(k)
    if lut is None:
        n = 1 << _LUT_BITS
        step = 256 // n
        v = (np.arange(n, dtype=np.float32) * step + (step - 1) / 2)
        grid = np.stack(np.meshgrid(v, v, v, indexing="ij"), axis=-1)  # r, g, b
        lut = (_chroma_alpha(grid, key, tolerance, softness, choke) * 255 + 0.5) \
            .astype(np.uint8).reshape(-1)
        if len(_CHROMA_LUTS) > 16:
            _CHROMA_LUTS.clear()
        _CHROMA_LUTS[k] = lut
    return lut


def chroma_key(arr, p, ctx):
    """Kroma düzleminde anahtar yönüne benzerlik: piksel rengi anahtar renk yönünde
    güçlü (along) ve o yönden az sapmış (perp) ise saydamlaşır. Böylece perdenin
    aydınlık ve gölgeli bölgeleri birlikte silinir, ten ve beyaz/siyah korunur.

    Hız için: saydamlık 6 bit/kanal renk tablosundan okunur, renk taşması giderme
    tamsayı aritmetiğiyle yalnızca anahtar kanalda yapılır.
    """
    key = hex_to_rgb(p["color"])
    lut = _chroma_lut(key, p["tolerance"], p["softness"], p["choke"])
    sh = 8 - _LUT_BITS
    r = arr[..., 0] >> sh
    g = arr[..., 1] >> sh
    b = arr[..., 2] >> sh
    idx = (r.astype(np.uint32) << (2 * _LUT_BITS)) | (g.astype(np.uint32) << _LUT_BITS) | b
    a = lut[idx]
    if p["spill"] > 0:
        ch = int(np.argmax(key))
        o1, o2 = [i for i in range(3) if i != ch]
        c = arr[..., ch].astype(np.int16)
        lim = (arr[..., o1].astype(np.int16) + arr[..., o2]) >> 1
        d = np.maximum(c - lim, 0)
        c -= (d * int(p["spill"] * 256)) >> 8
        arr[..., ch] = c.astype(np.uint8)
    alpha = arr[..., 3]
    if alpha.min() == 255:
        arr[..., 3] = a
    else:
        arr[..., 3] = ((alpha.astype(np.uint16) * a + 127) // 255).astype(np.uint8)
    return arr


@register("luma_key", "Luma Key", "Keying", [
    ("threshold", "Threshold", "float", 0.15, 0.0, 1.0, 0.01, 2),
    ("softness", "Edge Softness", "float", 0.1, 0.0, 1.0, 0.01, 2),
    ("invert", "Invert", "bool", False, 0, 0, 0, 0),
], needs_alpha=True)
def luma_key(arr, p, ctx):
    rgb = _rgb(arr)
    y = (rgb @ np.array([0.299, 0.587, 0.114], np.float32)) / 255.0
    a = np.clip((y - p["threshold"]) / max(p["softness"], 1e-3), 0, 1)
    if p["invert"]:
        a = 1 - a
    arr[..., 3] = (arr[..., 3].astype(np.float32) * a).astype(np.uint8)
    return arr


# ------------------------------------------------------------ renk
@register("black_white", "Black & White", "Color Correction", [
    ("amount", "Amount", "float", 1.0, 0.0, 1.0, 0.05, 2)])
def black_white(arr, p, ctx):
    rgb = _rgb(arr)
    g = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    return _mix(arr, np.repeat(g[..., None], 3, -1), p["amount"])


@register("sepia", "Sepia", "Color Correction", [
    ("amount", "Amount", "float", 1.0, 0.0, 1.0, 0.05, 2)])
def sepia(arr, p, ctx):
    m = np.array([[0.393, 0.769, 0.189], [0.349, 0.686, 0.168], [0.272, 0.534, 0.131]],
                 np.float32)
    return _mix(arr, _rgb(arr) @ m.T, p["amount"])


@register("invert", "Invert", "Color Correction", [
    ("amount", "Amount", "float", 1.0, 0.0, 1.0, 0.05, 2)])
def invert(arr, p, ctx):
    return _mix(arr, 255.0 - _rgb(arr), p["amount"])


@register("hue", "Hue Shift", "Color Correction", [
    ("degrees", "Hue", "float", 0.0, -180.0, 180.0, 1, 0)])
def hue(arr, p, ctx):
    a = math.radians(p["degrees"])
    c, s = math.cos(a), math.sin(a)
    m = np.array([
        [0.299 + 0.701 * c + 0.168 * s, 0.587 - 0.587 * c + 0.330 * s,
         0.114 - 0.114 * c - 0.497 * s],
        [0.299 - 0.299 * c - 0.328 * s, 0.587 + 0.413 * c + 0.035 * s,
         0.114 - 0.114 * c + 0.292 * s],
        [0.299 - 0.300 * c + 1.250 * s, 0.587 - 0.588 * c - 1.050 * s,
         0.114 + 0.886 * c - 0.203 * s]], np.float32)
    return _put(arr, _rgb(arr) @ m.T)


@register("temperature", "Temperature & Tint", "Color Correction", [
    ("temperature", "Temperature", "float", 0.0, -1.0, 1.0, 0.02, 2),
    ("tint", "Tint", "float", 0.0, -1.0, 1.0, 0.02, 2)])
def temperature(arr, p, ctx):
    rgb = _rgb(arr)
    t, g = p["temperature"] * 40, p["tint"] * 40
    rgb[..., 0] += t
    rgb[..., 2] -= t
    rgb[..., 1] -= g
    return _put(arr, rgb)


@register("levels", "Levels / Gamma", "Color Correction", [
    ("black", "Black Point", "float", 0.0, 0.0, 0.5, 0.01, 2),
    ("white", "White Point", "float", 1.0, 0.5, 1.0, 0.01, 2),
    ("gamma", "Gamma", "float", 1.0, 0.2, 3.0, 0.05, 2)])
def levels(arr, p, ctx):
    x = np.arange(256, dtype=np.float32) / 255
    x = np.clip((x - p["black"]) / max(p["white"] - p["black"], 1e-3), 0, 1)
    lut = (np.power(x, 1.0 / max(p["gamma"], 1e-3)) * 255).astype(np.uint8)
    return _lut(arr, lut)


@register("tint_color", "Color Overlay", "Color Correction", [
    ("color", "Color", "color", "#ff8800", 0, 0, 0, 0),
    ("amount", "Amount", "float", 0.3, 0.0, 1.0, 0.05, 2)])
def tint_color(arr, p, ctx):
    rgb = _rgb(arr)
    col = np.array(hex_to_rgb(p["color"]), np.float32)
    lum = (rgb @ np.array([0.299, 0.587, 0.114], np.float32))[..., None] / 255
    return _mix(arr, lum * col, p["amount"])


@register("posterize", "Posterize", "Stylize", [
    ("levels", "Levels", "float", 6, 2, 32, 1, 0)])
def posterize(arr, p, ctx):
    n = max(2, int(p["levels"]))
    x = np.arange(256, dtype=np.float32)
    lut = (np.round(x / 255 * (n - 1)) / (n - 1) * 255).astype(np.uint8)
    return _lut(arr, lut)


# ------------------------------------------------------------ bulanıklık
@register("blur", "Gaussian Blur", "Blur & Sharpen", [
    ("radius", "Blurriness", "float", 10.0, 0.0, 200.0, 0.5, 1)])
def blur(arr, p, ctx):
    r = p["radius"] * ctx.get("scale", 1.0) / 2
    if r < 0.5:
        return arr
    out = blur_array(arr, r)
    arr[...] = np.clip(out, 0, 255).astype(np.uint8)
    return arr


@register("sharpen", "Sharpen", "Blur & Sharpen", [
    ("amount", "Amount", "float", 0.8, 0.0, 5.0, 0.05, 2)])
def sharpen(arr, p, ctx):
    rgb = _rgb(arr)
    b = blur_array(rgb, max(1.0, ctx.get("scale", 1.0)), passes=1)
    return _put(arr, rgb + (rgb - b) * p["amount"])


# ------------------------------------------------------------ stilize
@register("vignette", "Vignette", "Stylize", [
    ("amount", "Amount", "float", 0.6, 0.0, 1.0, 0.05, 2),
    ("size", "Size", "float", 0.6, 0.0, 1.0, 0.05, 2)])
def vignette(arr, p, ctx):
    h, w = arr.shape[:2]
    r = _radial(h, w)
    m = np.clip((r - p["size"] * 0.75) / 0.45, 0, 1)
    m = m * m * (3 - 2 * m)
    return _put(arr, _rgb(arr) * (1 - p["amount"] * m)[..., None])


@register("mosaic", "Mosaic", "Stylize", [
    ("size", "Block Size", "float", 24, 2, 200, 1, 0)])
def mosaic(arr, p, ctx):
    b = max(2, int(round(p["size"] * ctx.get("scale", 1.0))))
    h, w = arr.shape[:2]
    small = arr[b // 2::b, b // 2::b]
    if small.size == 0:
        return arr
    big = np.repeat(np.repeat(small, b, 0), b, 1)
    out = np.empty_like(arr)
    out[...] = big[:h, :w] if big.shape[0] >= h and big.shape[1] >= w else \
        np.pad(big, ((0, max(0, h - big.shape[0])), (0, max(0, w - big.shape[1])), (0, 0)),
               mode="edge")[:h, :w]
    return out


@register("noise", "Film Grain", "Stylize", [
    ("amount", "Amount", "float", 0.2, 0.0, 1.0, 0.02, 2)])
def noise(arr, p, ctx):
    h, w = arr.shape[:2]
    rng = np.random.default_rng(int(ctx.get("t", 0) * 1000) & 0xFFFFFFFF)
    n = rng.normal(0, p["amount"] * 45, (h, w, 1)).astype(np.float32)
    return _put(arr, _rgb(arr) + n)


# ------------------------------------------------------------ dönüştürme
@register("crop", "Crop", "Transform", [
    ("left", "Left", "float", 0.0, 0.0, 100.0, 0.5, 1),
    ("right", "Right", "float", 0.0, 0.0, 100.0, 0.5, 1),
    ("top", "Top", "float", 0.0, 0.0, 100.0, 0.5, 1),
    ("bottom", "Bottom", "float", 0.0, 0.0, 100.0, 0.5, 1)], needs_alpha=True)
def crop(arr, p, ctx):
    h, w = arr.shape[:2]
    l, r = int(w * p["left"] / 100), int(w * p["right"] / 100)
    t, b = int(h * p["top"] / 100), int(h * p["bottom"] / 100)
    if l:
        arr[:, :l, 3] = 0
    if r:
        arr[:, w - r:, 3] = 0
    if t:
        arr[:t, :, 3] = 0
    if b:
        arr[h - b:, :, 3] = 0
    return arr


@register("flip_h", "Horizontal Flip", "Transform")
def flip_h(arr, p, ctx):
    return arr[:, ::-1]


@register("flip_v", "Vertical Flip", "Transform")
def flip_v(arr, p, ctx):
    return arr[::-1]
