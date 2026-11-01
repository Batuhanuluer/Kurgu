"""Ses işleme: klip ses efektleri, iz düzeyi/pan, ses yüksekliği ölçümü (LUFS).

Ses parça parça (ör. 1024 örnek) karıştırılır. Parça sınırlarında süreksizlik olmasın diye
efektler durumsuz tasarlandı: EQ ve uğultu giderme doğrusal fazlı FIR süzgeçtir ve her
parça, süzgeç uzunluğu kadar önceki/sonraki sesle birlikte işlenir; dinamik efektler
(kompresör, kapı) zarfı kayan pencereyle hesaplar ve aynı şekilde öncesini okur.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


def db2lin(db: float) -> float:
    return 10 ** (db / 20.0)


def lin2db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-9))


def pan_gains(pan: float) -> tuple[float, float]:
    """Sabit güç pan yasası: merkezde iki kanal da 1 (0 dB)."""
    a = (min(max(pan, -1.0), 1.0) + 1) * math.pi / 4
    return math.cos(a) * math.sqrt(2), math.sin(a) * math.sqrt(2)


# ------------------------------------------------------------ FIR tasarımı
FIR_TAPS = 1025  # ~21 ms @48k: 50 Hz'e kadar düzgün yanıt
HUM_TAPS = 16385  # dar çentik için uzun süzgeç (yalnız uğultu giderici)


def _design_fir(gain_fn: Callable[[np.ndarray], np.ndarray], sr: int,
                taps: int = FIR_TAPS) -> np.ndarray:
    """İstenen genlik yanıtından (frekans -> doğrusal kazanç) doğrusal fazlı FIR."""
    n = 1 << max(13, int(math.ceil(math.log2(taps * 2))))  # süzgeçten uzun FFT
    f = np.fft.rfftfreq(n, 1 / sr)
    mag = gain_fn(f).astype(np.float64)
    h = np.fft.irfft(mag, n)
    h = np.roll(h, taps // 2)[:taps]
    h *= np.blackman(taps)
    return h.astype(np.float32)


def _shelf(f, fc, gain_db, high: bool):
    g = db2lin(gain_db)
    x = (f / fc) ** 2
    s = x / (1 + x) if high else 1 / (1 + x)  # yumuşak geçiş
    return 1 + (g - 1) * s


def _peak(f, fc, gain_db, q):
    g = db2lin(gain_db)
    bw = fc / max(q, 0.1)
    return 1 + (g - 1) / (1 + ((f - fc) / (bw / 2)) ** 2)


def _fir_conv(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """x (n+taps-1, 2) 'valid' FFT konvolüsyonu -> (n, 2)."""
    n_out = len(x) - len(h) + 1
    if n_out <= 0:
        return np.zeros((0, x.shape[1]), np.float32)
    nfft = 1 << int(math.ceil(math.log2(len(x) + len(h))))
    H = np.fft.rfft(h, nfft)
    X = np.fft.rfft(x, nfft, axis=0)
    y = np.fft.irfft(X * H[:, None], nfft, axis=0)
    return y[len(h) - 1:len(h) - 1 + n_out].astype(np.float32)


@dataclass
class AudioFx:
    id: str
    name: str
    params: list = field(default_factory=list)  # (anahtar, etiket, min, maks, varsayılan, birim)


AUDIO_FX = {
    "eq": AudioFx("eq", "Equalizer", [
        ("low_cut", "Low Cut", 0, 400, 0, "Hz"),
        ("low", "Bass (100 Hz)", -18, 18, 0, "dB"),
        ("mid", "Mid (1 kHz)", -18, 18, 0, "dB"),
        ("presence", "Presence (3.5 kHz)", -18, 18, 0, "dB"),
        ("high", "Treble (10 kHz)", -18, 18, 0, "dB"),
    ]),
    "compressor": AudioFx("compressor", "Compressor", [
        ("threshold", "Threshold", -60, 0, -18, "dB"),
        ("ratio", "Ratio", 1, 20, 4, ":1"),
        ("release", "Release", 20, 1000, 150, "ms"),
        ("makeup", "Make-up Gain", 0, 24, 6, "dB"),
    ]),
    "gate": AudioFx("gate", "Noise Gate", [
        ("threshold", "Threshold", -90, -10, -50, "dB"),
        ("floor", "Reduction", -80, 0, -40, "dB"),
        ("hold", "Hold", 10, 500, 120, "ms"),
    ]),
    "hum": AudioFx("hum", "Hum Remover", [
        ("freq", "Mains Frequency", 50, 60, 50, "Hz"),
        ("harmonics", "Harmonics", 1, 6, 4, ""),
    ]),
    "deesser": AudioFx("deesser", "De-esser", [
        ("amount", "Amount", 0, 18, 8, "dB"),
    ]),
}


def new_audio_fx(kind: str) -> dict:
    import uuid
    d = AUDIO_FX[kind]
    return {"type": kind, "enabled": True, "id": uuid.uuid4().hex[:8],
            "params": {p[0]: p[4] for p in d.params}}


def _params(fx: dict) -> dict:
    d = AUDIO_FX[fx["type"]]
    p = {k[0]: k[4] for k in d.params}
    p.update(fx.get("params", {}))
    return p


_FIR_CACHE: dict = {}


def _fir_for(fx: dict, sr: int) -> np.ndarray | None:
    p = _params(fx)
    key = (fx["type"], tuple(sorted((k, round(float(v), 3)) for k, v in p.items())), sr)
    h = _FIR_CACHE.get(key)
    if h is not None:
        return h
    if fx["type"] == "eq":
        if all(abs(p[k]) < 1e-6 for k in ("low", "mid", "presence", "high")) and p["low_cut"] < 1:
            return None

        def resp(f):
            r = _shelf(f, 250, p["low"], False) * _peak(f, 1000, p["mid"], 0.9) * \
                _peak(f, 3500, p["presence"], 1.2) * _shelf(f, 6000, p["high"], True)
            if p["low_cut"] >= 1:
                x = (f / p["low_cut"]) ** 4
                r = r * np.sqrt(x / (1 + x))  # 24 dB/oktav
            return r
    elif fx["type"] == "hum":
        def resp(f):
            r = np.ones_like(f)
            for k in range(1, int(p["harmonics"]) + 1):
                fc = p["freq"] * k
                r *= 1 - 1 / (1 + ((f - fc) / 4.0) ** 4)  # dar, derin çentik (~8 Hz)
            return r
    else:
        return None
    h = _design_fir(resp, sr, HUM_TAPS if fx["type"] == "hum" else FIR_TAPS)
    if len(_FIR_CACHE) > 32:
        _FIR_CACHE.clear()
    _FIR_CACHE[key] = h
    return h


def _env_db(x: np.ndarray, win: int) -> np.ndarray:
    """Kayan RMS zarfı (dB), stereo'nun en yükseği."""
    sq = (x.astype(np.float64) ** 2).max(axis=1)
    c = np.concatenate([[0.0], np.cumsum(sq)])
    w = max(1, win)
    idx = np.arange(len(sq))
    lo = np.maximum(idx - w + 1, 0)
    rms = (c[idx + 1] - c[lo]) / (idx + 1 - lo)
    return 10 * np.log10(np.maximum(rms, 1e-12))


def _smooth(g: np.ndarray, win: int) -> np.ndarray:
    """Kazancı geriye dönük kayan ortalama ile yumuşat (tıklama olmasın)."""
    w = max(1, win)
    c = np.concatenate([[0.0], np.cumsum(g.astype(np.float64))])
    idx = np.arange(len(g))
    lo = np.maximum(idx - w + 1, 0)
    return ((c[idx + 1] - c[lo]) / (idx + 1 - lo)).astype(np.float32)


def padding(fx_list: list, sr: int) -> tuple[int, int]:
    """Kesintisiz işleme için gereken (önce, sonra) örnek sayısı."""
    pre = post = 0
    for fx in fx_list or []:
        if not fx.get("enabled", True) or fx["type"] not in AUDIO_FX:
            continue
        p = _params(fx)
        if fx["type"] in ("eq", "hum"):
            n = HUM_TAPS if fx["type"] == "hum" else FIR_TAPS
            pre, post = pre + n // 2, post + n // 2
        elif fx["type"] == "compressor":
            pre += int(sr * (p["release"] / 1000 + 0.02))
        elif fx["type"] == "gate":
            pre += int(sr * (p["hold"] / 1000 + 0.02))
        elif fx["type"] == "deesser":
            pre, post = pre + FIR_TAPS // 2 + int(sr * 0.03), post + FIR_TAPS // 2
    return pre, post


def process(x: np.ndarray, fx_list: list, sr: int) -> np.ndarray:
    """x: (pre + n + post, 2) -> efektler uygulanmış aynı uzunlukta dizi (kenarlar ısınma
    payıdır; çağıran ortadaki n örneği kullanır). Her efekt uzunluğu korur."""
    for fx in fx_list or []:
        if not fx.get("enabled", True) or fx["type"] not in AUDIO_FX:
            continue
        p = _params(fx)
        t = fx["type"]
        if t in ("eq", "hum"):
            h = _fir_for(fx, sr)
            if h is None:
                continue
            half = len(h) // 2
            padded = np.vstack([np.zeros((half, 2), np.float32), x,
                                np.zeros((half, 2), np.float32)])
            x = _fir_conv(padded, h)
        elif t == "compressor":
            env = _env_db(x, int(sr * 0.01))
            over = np.maximum(env - p["threshold"], 0)
            gr = -over * (1 - 1 / max(p["ratio"], 1.0))
            g = 10 ** (_smooth(gr, int(sr * p["release"] / 1000)) / 20)
            x = x * (g * db2lin(p["makeup"]))[:, None]
        elif t == "gate":
            env = _env_db(x, int(sr * 0.01))
            g = np.where(env > p["threshold"], 1.0, db2lin(p["floor"])).astype(np.float32)
            # Tutma: kısa sessizliklerde kapanmasın (geriye dönük en büyük ≈ ortalama ile)
            g = _smooth(g, int(sr * p["hold"] / 1000))
            g = np.minimum(1.0, g * 1.6)
            x = x * g[:, None]
        elif t == "deesser":
            # Tıslama bandını (5–10 kHz) ayır, yüksekse yalnızca o bandı kıs
            band = {"type": "eq", "params": {"low_cut": 0, "low": -18, "mid": -18,
                                             "presence": -6, "high": 6}}
            h = _fir_for(band, sr)
            half = len(h) // 2
            pad = np.vstack([np.zeros((half, 2), np.float32), x,
                             np.zeros((half, 2), np.float32)])
            s_band = _fir_conv(pad, h)
            env_s = _env_db(s_band, int(sr * 0.005))
            env_all = _env_db(x, int(sr * 0.005))
            excess = np.clip(env_s - env_all + 6, 0, None)  # tıslama baskınsa
            red = -np.minimum(excess, p["amount"])
            g = 10 ** (_smooth(red, int(sr * 0.02)) / 20)
            x = x + s_band * (g - 1)[:, None]
    return x.astype(np.float32)


# ------------------------------------------------------------ ses yüksekliği (LUFS)
def _biquad_mag(f, b, a, fs):
    w = 2 * np.pi * np.minimum(f, fs / 2 - 1) / fs
    z = np.exp(-1j * w)
    return np.abs((b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z))


def _k_weight(sr: int) -> np.ndarray:
    """ITU-R BS.1770 K ağırlığı: standarttaki iki biquad'ın (48 kHz katsayıları) genlik
    yanıtından tasarlanan doğrusal fazlı FIR (ölçüm için faz önemsiz)."""
    key = ("kw", sr)
    h = _FIR_CACHE.get(key)
    if h is None:
        def resp(f):
            pre = _biquad_mag(f, (1.53512485958697, -2.69169618940638, 1.19839281085285),
                              (1.0, -1.69065929318241, 0.73248077421585), 48000)
            rlb = _biquad_mag(f, (1.0, -2.0, 1.0), (1.0, -1.99004745483398, 0.99007225036621),
                              48000)
            return pre * rlb
        h = _design_fir(resp, sr, 4097)
        _FIR_CACHE[key] = h
    return h


def integrated_loudness(x: np.ndarray, sr: int) -> float:
    """Bütünleşik ses yüksekliği (LUFS, BS.1770 kapılı ölçüm). Sessizse -inf."""
    if len(x) < sr * 0.4:
        return float("-inf")
    h = _k_weight(sr)
    half = len(h) // 2
    y = _fir_conv(np.vstack([np.zeros((half, 2), np.float32), x.astype(np.float32),
                             np.zeros((half, 2), np.float32)]), h)
    blk, hop = int(sr * 0.4), int(sr * 0.1)
    sq = (y.astype(np.float64) ** 2).sum(axis=1)  # kanalların toplamı (L+R ağırlık 1)
    c = np.concatenate([[0.0], np.cumsum(sq)])
    starts = np.arange(0, len(sq) - blk + 1, hop)
    ms = (c[starts + blk] - c[starts]) / blk
    lk = -0.691 + 10 * np.log10(np.maximum(ms, 1e-12))
    gated = ms[lk > -70]
    if len(gated) == 0:
        return float("-inf")
    rel = -0.691 + 10 * np.log10(gated.mean()) - 10
    gated = ms[(lk > -70) & (lk > rel)]
    if len(gated) == 0:
        return float("-inf")
    return float(-0.691 + 10 * np.log10(gated.mean()))


def true_peak_db(x: np.ndarray) -> float:
    return lin2db(float(np.abs(x).max()) if len(x) else 0.0)


LOUDNESS_TARGETS = [(-14.0, "YouTube / Spotify (-14 LUFS)"),
                    (-16.0, "Podcast / Apple (-16 LUFS)"),
                    (-23.0, "TV broadcast EBU R128 (-23 LUFS)"),
                    (-1.0, "Peak -1 dBFS")]


def normalize_gain(x: np.ndarray, sr: int, target: float, peak_mode: bool = False,
                   ceiling_db: float = -1.0) -> float:
    """Hedefe ulaşmak için doğrusal kazanç. Tepe tavanı (ceiling) aşılmaz."""
    pk = true_peak_db(x)
    if peak_mode:
        return db2lin(target - pk) if pk > -120 else 1.0
    lu = integrated_loudness(x, sr)
    if not math.isfinite(lu):
        return 1.0
    g_db = target - lu
    g_db = min(g_db, ceiling_db - pk)  # kırpılma olmasın
    return db2lin(g_db)
