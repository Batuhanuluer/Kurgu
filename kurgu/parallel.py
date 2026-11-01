"""Büyük kareleri yatay şeritlere bölüp çekirdekler arasında paylaştırma.

NumPy işlemlerinin çoğu GIL'i bırakır; 4K bir kare 8 çekirdekte 4-6 kat hızlı işlenir.
Küçük karelerde (önizleme) bölmenin maliyeti kazancı geçtiği için tek parça çalışır.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

MIN_PIXELS = 1_000_000  # ~1 MP altında bölme
WORKERS = max(1, min(int(os.environ.get("KURGU_THREADS", 0)) or (os.cpu_count() or 1), 16))
_pool: ThreadPoolExecutor | None = None


def pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="kurgu-px")
    return _pool


def rows(fn, arr, *extra_row_arrays):
    """fn(arr[a:b], *[x[a:b] ...]) çağrılarını şeritler hâlinde paralel yapar.
    fn şeridi yerinde değiştirmelidir (dönüş değeri kullanılmaz). Ek diziler (ör. dither)
    aynı satır aralığıyla dilimlenir; None olanlar None geçer."""
    h = arr.shape[0]
    n = WORKERS if (WORKERS > 1 and arr.shape[0] * arr.shape[1] >= MIN_PIXELS) else 1
    if n == 1:
        fn(arr, *extra_row_arrays)
        return arr
    bounds = [(i * h // n, (i + 1) * h // n) for i in range(n)]
    futs = [pool().submit(fn, arr[a:b], *[(x[a:b] if x is not None else None)
                                          for x in extra_row_arrays]) for a, b in bounds]
    for f in futs:
        f.result()  # hatayı çağırana ilet
    return arr
