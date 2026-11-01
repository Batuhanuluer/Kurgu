"""Çoklu kamera (multicam): kameraları sesten eşitleme.

Her kameranın sesinden kısa aralıklı bir "ses zarfı" (enerji değişimi) çıkarılır ve
FFT ile çapraz korelasyon yapılarak kameraların birbirine göre kayması bulunur.
Alkış, konuşma, müzik gibi ortak sesler yeterlidir; sinüs gibi tekdüze sesler değil.
"""
from __future__ import annotations

import av
import numpy as np

ENV_RATE = 1000  # zarf örnekleme hızı (Hz): 1 ms çözünürlük
MAX_ANALYSE = 30 * 60  # en çok ilk 30 dakika incelenir


def audio_envelope(path: str, rate: int = ENV_RATE, limit: float = MAX_ANALYSE) -> np.ndarray:
    """Ses başlangıçlarını öne çıkaran zarf (rate Hz). Ses yoksa boş dizi."""
    sub = 8  # 8 kHz'den rate'e
    parts = []
    total = 0
    with av.open(path) as c:
        if not c.streams.audio:
            return np.zeros(0, np.float32)
        s = c.streams.audio[0]
        rs = av.AudioResampler(format="flt", layout="mono", rate=rate * sub)
        for frame in c.decode(s):
            frame.pts = None
            for rf in rs.resample(frame):
                a = rf.to_ndarray().reshape(-1)
                parts.append(a)
                total += len(a)
            if total > limit * rate * sub:
                break
    if not parts:
        return np.zeros(0, np.float32)
    x = np.abs(np.concatenate(parts)).astype(np.float32)
    n = len(x) // sub
    env = x[:n * sub].reshape(n, sub).mean(axis=1)
    # Yavaş değişen düzeyi çıkar, yalnız artışları tut (onset): kamera mikrofonlarının
    # farklı kazancına ve uğultuya karşı dayanıklı
    k = max(1, rate // 20)
    smooth = np.convolve(env, np.ones(k, np.float32) / k, mode="same")
    d = np.maximum(env - smooth, 0)
    d = np.log1p(d * 50)
    d -= d.mean()
    sd = d.std()
    return (d / sd if sd > 1e-9 else d).astype(np.float32)


def find_offset(ref: np.ndarray, other: np.ndarray, rate: int = ENV_RATE,
                max_shift: float | None = None) -> tuple[float, float]:
    """other'ın ref'e göre başlangıç kayması (saniye) ve güven (0..1).
    Dönen kayma d: other'daki t anı, ref'teki t + d anına denk gelir (other d saniye sonra
    başlamış demektir)."""
    if len(ref) < rate // 2 or len(other) < rate // 2:
        return 0.0, 0.0
    n = 1
    while n < len(ref) + len(other):
        n *= 2
    fa = np.fft.rfft(ref, n)
    fb = np.fft.rfft(other, n)
    corr = np.fft.irfft(fa * np.conj(fb), n)
    # lag ≥ 0: other, ref'in lag örnek sonrasında başlıyor; negatifler sondan
    lags = np.concatenate([np.arange(0, len(ref)), np.arange(-len(other) + 1, 0)])
    vals = np.concatenate([corr[:len(ref)], corr[n - len(other) + 1:]])
    if max_shift is not None:
        ok = np.abs(lags) <= max_shift * rate
        lags, vals = lags[ok], vals[ok]
    # Örtüşen uzunluğa göre normalize et (kısa örtüşmeler şişmesin)
    overlap = np.minimum(len(ref) - np.maximum(lags, 0), len(other) + np.minimum(lags, 0))
    # Çok kısa örtüşmeler (ör. yalnızca son 0.5 sn) rastlantısal yüksek skor verir: ele
    need = min(max(3 * rate, int(0.2 * min(len(ref), len(other)))),
               min(len(ref), len(other)))
    ok = overlap >= need
    lags, vals, overlap = lags[ok], vals[ok], overlap[ok]
    if len(lags) == 0:
        return 0.0, 0.0
    score = vals / overlap
    i = int(np.argmax(score))
    best = score[i]
    # Güven: en iyi tepe, ikinci en iyi bölgeye göre ne kadar baskın
    mask = np.abs(lags - lags[i]) > rate // 10
    second = float(score[mask].max()) if mask.any() else 0.0
    conf = float(np.clip((best - max(second, 0)) / (abs(best) + 1e-9), 0, 1))
    # Alt örnek hassasiyeti: parabol tepe
    shift = float(lags[i])
    if 0 < i < len(score) - 1 and lags[i - 1] == lags[i] - 1 and lags[i + 1] == lags[i] + 1:
        a, b, c = score[i - 1], score[i], score[i + 1]
        den = a - 2 * b + c
        if abs(den) > 1e-12:
            shift += 0.5 * (a - c) / den
    return shift / rate, conf


def sync_by_audio(paths: list[str], progress=None) -> tuple[list[float], list[float]]:
    """Her kameranın grup başlangıcına göre kayması (en erken başlayan 0) ve güvenleri.
    İlk dosya referanstır; sesi olmayan kameralar 0 kaymayla kalır (güven 0)."""
    envs = []
    for i, p in enumerate(paths):
        try:
            envs.append(audio_envelope(p))
        except Exception:
            envs.append(np.zeros(0, np.float32))
        if progress:
            progress((i + 1) / (len(paths) + 1))
    raw, conf = [0.0], [1.0]
    for e in envs[1:]:
        d, c = find_offset(envs[0], e)
        raw.append(d)
        conf.append(c)
    base = min(raw)
    return [r - base for r in raw], conf


def sync_by_start(n: int) -> list[float]:
    return [0.0] * n
