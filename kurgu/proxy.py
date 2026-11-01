"""Proxy (hafif çalışma kopyası) üretimi.

4K, H.265/HEVC, ProRes ya da 10-bit (ör. DJI D-Log) gibi ağır videolar düzenleme sırasında
akıcı oynamaz. Proxy, aynı videonun küçük ve hızlı çözülen bir kopyasıdır:
  - 540p yükseklik, H.264, kısa GOP (her 12 karede anahtar kare → hızlı atlama)
  - 10-bit kaynaklarda 10-bit kalır (D-Log renk işlemleri önizlemede de bantsız)
  - Telefonların dönme bilgisi uygulanmış (dik) yazılır
  - Kare zaman damgaları kaynağınkiyle birebir aynıdır (değişken kare hızlı videolar da)
Önizleme proxy'yi, dışa aktarma her zaman orijinal dosyayı kullanır.
"""
from __future__ import annotations

import hashlib
import os
from fractions import Fraction

import av

from .media import display_size, oriented_array, rotation_steps

PROXY_DIR = os.path.join(os.path.expanduser("~"), ".kurgu", "proxies")
PROXY_HEIGHT = 540
HEAVY_CODECS = {"hevc", "h265", "prores", "dnxhd", "vp9", "av1", "mpeg2video"}


def needs_proxy(media) -> bool:
    if media.kind != "video" or not media.has_video:
        return False
    big = media.width * media.height > 1920 * 1080 * 1.05
    return big or media.codec in HEAVY_CODECS or media.bit_depth > 8


def proxy_path(media, folder: str | None = None) -> str:
    folder = folder or PROXY_DIR
    try:
        st = os.stat(media.path)
        sig = f"{os.path.abspath(media.path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = os.path.abspath(media.path)
    h = hashlib.blake2b(sig.encode(), digest_size=8).hexdigest()
    base = os.path.splitext(os.path.basename(media.path))[0][:40]
    return os.path.join(folder, f"{base}-{h}.proxy.mp4")


def existing_proxy(media, folder: str | None = None) -> str:
    p = proxy_path(media, folder)
    return p if os.path.exists(p) else ""


def make_proxy(media, folder: str | None = None, progress=None, cancelled=None) -> str:
    """Proxy dosyasını üretir ve yolunu döndürür (zaten varsa hemen döndürür)."""
    dest = proxy_path(media, folder)
    if os.path.exists(dest):
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part.mp4"
    hi = media.bit_depth > 8
    pix = "yuv420p10le" if hi else "yuv420p"
    arr_fmt = "rgb48le" if hi else "rgb24"
    with av.open(media.path) as src:
        vs = src.streams.video[0]
        vs.thread_type = "AUTO"
        start = vs.start_time or 0
        total = float(vs.duration * vs.time_base) if vs.duration else media.duration
        out = av.open(tmp, "w")
        ok = False
        try:
            os_ = None
            k = 0
            for f in src.decode(vs):
                if cancelled and cancelled():
                    raise RuntimeError("cancelled")
                if os_ is None:
                    k = rotation_steps(f)
                    fw, fh = display_size(f, k)
                    h = min(PROXY_HEIGHT, fh) // 2 * 2
                    w = max(2, int(round(fw * h / fh)) // 2 * 2)
                    rate = vs.average_rate or Fraction(30)
                    os_ = out.add_stream("libx264", rate=rate)
                    os_.width, os_.height, os_.pix_fmt = w, h, pix
                    os_.time_base = vs.time_base
                    os_.codec_context.time_base = vs.time_base
                    os_.options = {"crf": "20", "preset": "veryfast", "g": "12",
                                   "bf": "0", "tune": "fastdecode"}
                if f.pts is None:
                    continue
                if k:
                    nf = av.VideoFrame.from_ndarray(oriented_array(f, w, h, arr_fmt, k),
                                                    format=arr_fmt).reformat(format=pix)
                else:
                    nf = f.reformat(width=w, height=h, format=pix)
                nf.pts = f.pts - start
                nf.time_base = vs.time_base
                for pkt in os_.encode(nf):
                    out.mux(pkt)
                if progress and total:
                    progress(min(1.0, float((f.pts - start) * vs.time_base) / total))
            if os_ is not None:
                for pkt in os_.encode():
                    out.mux(pkt)
            ok = True
        finally:
            out.close()
            if not ok:  # iptal / hata: yarım dosya kalmasın
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    os.replace(tmp, dest)
    return dest
