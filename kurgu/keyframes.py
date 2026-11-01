"""Anahtar kare (keyframe) animasyonu.

Anahtarlar klibin *kaynak zamanına* (medya saniyesi, in_point ile aynı ölçek)
bağlıdır. Böylece klip bölündüğünde, kırpıldığında ya da taşındığında
animasyon içerikle birlikte doğru yerde kalır (Premiere ile aynı mantık).

clip.keyframes = {
    "pos_x": [[kaynak_zamanı, değer, "linear" | "ease" | "hold"], ...],
    "fx.<efekt_id>.<param>": [...],      # ör. renk tekerleği [x, y] (liste değer)
    "mask.<maske_id>.<param>": [...],    # ör. "box", "points" (maske yolu), "feather"
}
Değer sayı ya da (iç içe) sayı listesi olabilir; listeler eleman eleman enterpole edilir.
Biçimleri uyuşmayan iki anahtar arasında (ör. nokta sayısı değişmiş maske) değer tutulur.
Bir anahtarın enterpolasyonu, o anahtardan bir sonrakine kadar olan bölümü belirler.
"""
from __future__ import annotations

import copy
import uuid

import numpy as np

from .model import Clip

# Animasyonlanabilen klip özellikleri
CLIP_PROPS = ["scale", "pos_x", "pos_y", "rotation", "opacity",
              "brightness", "contrast", "saturation", "volume"]
INTERPS = ["linear", "ease", "hold"]
DEFAULT_INTERP = "linear"


# ------------------------------------------------------------ yardımcılar
def effect_id(effect: dict) -> str:
    if not effect.get("id"):
        effect["id"] = uuid.uuid4().hex[:8]
    return effect["id"]


def fx_prop(effect: dict, param: str) -> str:
    return f"fx.{effect_id(effect)}.{param}"


def tol(fps: float) -> float:
    return 0.5 / max(fps, 1.0)


def keys(clip: Clip, prop: str) -> list:
    return clip.keyframes.get(prop) or []


def is_animated(clip: Clip, prop: str) -> bool:
    return bool(clip.keyframes.get(prop))


def _mask(clip: Clip, mid: str):
    for m in clip.masks or []:
        if m.get("id") == mid:
            return m
    return None


def static_value(clip: Clip, prop: str):
    if prop.startswith("mask."):
        _m, mid, param = prop.split(".", 2)
        m = _mask(clip, mid)
        return copy.deepcopy(m.get(param)) if m else None
    if prop.startswith("fx."):
        _f, eid, param = prop.split(".", 2)
        for e in clip.effects:
            if e.get("id") == eid:
                return e["params"].get(param)
        return None
    return getattr(clip, prop)


def set_static(clip: Clip, prop: str, value):
    if prop.startswith("mask."):
        _m, mid, param = prop.split(".", 2)
        m = _mask(clip, mid)
        if m is not None:
            m[param] = copy.deepcopy(value)
        return
    if prop.startswith("fx."):
        _f, eid, param = prop.split(".", 2)
        for e in clip.effects:
            if e.get("id") == eid:
                e["params"][param] = value
        return
    setattr(clip, prop, value)


# ------------------------------------------------------------ değerlendirme
def _ease(u):
    return u * u * (3 - 2 * u)


def _shape(v):
    if isinstance(v, (list, tuple)):
        return tuple(_shape(x) for x in v)
    return None if isinstance(v, (int, float)) and not isinstance(v, bool) else "x"


def lerp(v0, v1, u: float):
    """İki değer arasında enterpolasyon; listeler eleman eleman (biçim uyuşmazsa v0)."""
    if isinstance(v0, (list, tuple)):
        if _shape(v0) != _shape(v1):
            return copy.deepcopy(v0)
        return [lerp(a, b, u) for a, b in zip(v0, v1)]
    if isinstance(v0, bool) or not isinstance(v0, (int, float)) \
            or not isinstance(v1, (int, float)):
        return v0
    return v0 + (v1 - v0) * u


def _copy(v):
    return copy.deepcopy(v) if isinstance(v, (list, tuple, dict)) else v


def eval_keys(ks: list, s: float, default=0.0):
    if not ks:
        return default
    if s <= ks[0][0]:
        return _copy(ks[0][1])
    if s >= ks[-1][0]:
        return _copy(ks[-1][1])
    for (t0, v0, it), (t1, v1, _i) in zip(ks, ks[1:]):
        if t0 <= s <= t1:
            if it == "hold" or t1 <= t0:
                return _copy(v0)
            u = (s - t0) / (t1 - t0)
            if it == "ease":
                u = _ease(u)
            return lerp(v0, v1, u)
    return _copy(ks[-1][1])


def eval_keys_array(ks: list, s: np.ndarray, default=1.0) -> np.ndarray:
    """Ses için vektörel değerlendirme."""
    if not ks:
        return np.full(len(s), default, np.float32)
    ts = np.array([k[0] for k in ks], np.float64)
    vs = np.array([k[1] for k in ks], np.float64)
    if all(k[2] == "linear" for k in ks[:-1]):
        return np.interp(s, ts, vs).astype(np.float32)
    out = np.empty(len(s), np.float64)
    idx = np.clip(np.searchsorted(ts, s, side="right") - 1, 0, len(ts) - 1)
    for i in np.unique(idx):
        m = idx == i
        if i >= len(ts) - 1:
            out[m] = vs[-1]
            continue
        t0, t1, it = ts[i], ts[i + 1], ks[i][2]
        u = np.clip((s[m] - t0) / max(t1 - t0, 1e-9), 0, 1)
        if it == "hold":
            u = np.zeros_like(u)
        elif it == "ease":
            u = _ease(u)
        out[m] = vs[i] + (vs[i + 1] - vs[i]) * u
    out[s < ts[0]] = vs[0]
    return out.astype(np.float32)


def value_at(clip: Clip, prop: str, t: float):
    """Zaman çizelgesindeki t anında özelliğin değeri (animasyonlu ya da sabit)."""
    ks = keys(clip, prop)
    if not ks:
        return static_value(clip, prop)
    return eval_keys(ks, clip.source_time(t))


def animated(clip: Clip, t: float) -> Clip:
    """Klibin t anındaki değerlerle doldurulmuş hafif bir kopyası (çizim için)."""
    if not clip.keyframes:
        return clip
    c = copy.copy(clip)
    s = clip.source_time(t)
    fx_vals: dict = {}
    mask_vals: dict = {}
    for prop, ks in clip.keyframes.items():
        if not ks:
            continue
        v = eval_keys(ks, s)
        if prop.startswith("mask."):
            _m, mid, param = prop.split(".", 2)
            mask_vals.setdefault(mid, {})[param] = v
        elif prop.startswith("fx."):
            _f, eid, param = prop.split(".", 2)
            fx_vals.setdefault(eid, {})[param] = v
        else:
            setattr(c, prop, v)
    if fx_vals:
        new = []
        for e in clip.effects:
            vals = fx_vals.get(e.get("id"))
            if vals:
                e = dict(e)
                e["params"] = {**e["params"], **vals}
            new.append(e)
        c.effects = new
    if mask_vals:
        c.masks = [{**m, **mask_vals[m.get("id")]} if m.get("id") in mask_vals else m
                   for m in clip.masks]
    return c


# ------------------------------------------------------------ düzenleme
def key_index(ks: list, s: float, fps: float) -> int | None:
    for i, k in enumerate(ks):
        if abs(k[0] - s) <= tol(fps):
            return i
    return None


def set_key(clip: Clip, prop: str, s: float, value: float, fps: float, interp=None):
    ks = clip.keyframes.setdefault(prop, [])
    i = key_index(ks, s, fps)
    value = _copy(value)
    if i is not None:
        ks[i][1] = value
        if interp:
            ks[i][2] = interp
    else:
        ks.append([s, value, interp or DEFAULT_INTERP])
        ks.sort(key=lambda k: k[0])


def remove_key(clip: Clip, prop: str, s: float, fps: float):
    ks = clip.keyframes.get(prop) or []
    i = key_index(ks, s, fps)
    if i is not None:
        ks.pop(i)
    if not ks:
        clip.keyframes.pop(prop, None)


def toggle_animation(clip: Clip, prop: str, s: float, fps: float):
    """Kronometre: açıkken tüm anahtarları siler (o anki değer sabit kalır), kapalıyken
    o anki değerle ilk anahtarı oluşturur."""
    if is_animated(clip, prop):
        v = eval_keys(keys(clip, prop), s)
        clip.keyframes.pop(prop, None)
        set_static(clip, prop, v)
    else:
        set_key(clip, prop, s, static_value(clip, prop), fps)


def all_key_times(clip: Clip) -> list[float]:
    """Klipteki tüm anahtar zamanları (kaynak saniyesi), tekilleştirilmiş."""
    times = sorted(k[0] for ks in clip.keyframes.values() for k in ks)
    out: list[float] = []
    for t in times:
        if not out or t - out[-1] > 1e-4:
            out.append(t)
    return out


def move_keys_at(clip: Clip, s_old: float, s_new: float, fps: float):
    for ks in clip.keyframes.values():
        for k in ks:
            if abs(k[0] - s_old) <= tol(fps):
                k[0] = s_new
        ks.sort(key=lambda k: k[0])


def set_interp_at(clip: Clip, s: float, interp: str, fps: float):
    for ks in clip.keyframes.values():
        for k in ks:
            if abs(k[0] - s) <= tol(fps):
                k[2] = interp


def remove_keys_at(clip: Clip, s: float, fps: float):
    for prop in list(clip.keyframes):
        remove_key(clip, prop, s, fps)


def drop_mask_keys(clip: Clip, mask: dict):
    mid = mask.get("id")
    if mid:
        for prop in [p for p in clip.keyframes if p.startswith(f"mask.{mid}.")]:
            clip.keyframes.pop(prop, None)


def drop_effect_keys(clip: Clip, effect: dict):
    eid = effect.get("id")
    if eid:
        for prop in [p for p in clip.keyframes if p.startswith(f"fx.{eid}.")]:
            clip.keyframes.pop(prop, None)


def retarget(keyframes: dict, src: Clip, dst: Clip) -> dict:
    """Anahtarları başka bir klibe taşı (Özellikleri Yapıştır): klip başına göre hizala."""
    out = {}
    for prop, ks in keyframes.items():
        out[prop] = [[dst.in_point + (k[0] - src.in_point) / src.speed * dst.speed, _copy(k[1]), k[2]]
                     for k in ks]
    return out
