"""Donanım (GPU) hızlandırma: video çözme ve kodlama.

Çözme: NVIDIA (CUDA), Intel (QSV), AMD/Intel (VAAPI, D3D11VA), Apple (VideoToolbox).
4K HEVC 10-bit gibi ağır telefon/drone videolarında asıl yük çözmededir; donanım çözücü
bunu ekran kartına aktarır. Kullanılamazsa sessizce yazılıma döner.

Kodlama: h264/hevc için NVENC, QSV, AMF, VideoToolbox, Media Foundation. Bir kodlayıcı
sistemde gerçekten çalışıyor mu, küçük bir deneme kodlamasıyla bir kez denetlenir.
"""
from __future__ import annotations

import io
import sys
import threading

import av
import numpy as np

PRIORITY = {
    "win32": ["d3d11va", "cuda", "qsv", "dxva2"],
    "darwin": ["videotoolbox"],
    "linux": ["vaapi", "cuda", "qsv"],
}
ENCODERS = {
    "h264": ["h264_nvenc", "h264_videotoolbox", "h264_qsv", "h264_amf", "h264_mf"],
    "hevc": ["hevc_nvenc", "hevc_videotoolbox", "hevc_qsv", "hevc_amf", "hevc_mf"],
}

MODE = "auto"  # "auto" | "off" — ayarlardan
_failed: set[str] = set()
_enc_ok: dict[str, bool] = {}
_lock = threading.Lock()
LAST_USED: dict[str, str] = {}  # dosya -> kullanılan çözücü türü ("" = yazılım)


def decoder_types() -> list[str]:
    try:
        from av.codec.hwaccel import hwdevices_available
        avail = set(hwdevices_available())
    except Exception:
        return []
    plat = "linux" if sys.platform.startswith("linux") else sys.platform
    return [t for t in PRIORITY.get(plat, []) if t in avail and t not in _failed]


def open_video(path: str):
    """Görüntü okumak için kapsayıcı aç; mümkünse donanım çözücüyle."""
    if MODE != "off":
        try:
            from av.codec.hwaccel import HWAccel
        except Exception:
            HWAccel = None
        if HWAccel is not None:
            for t in decoder_types():
                try:
                    c = av.open(path, hwaccel=HWAccel(t, allow_software_fallback=True))
                    LAST_USED[path] = t
                    return c
                except Exception:
                    with _lock:
                        _failed.add(t)  # bu sistemde yok: bir daha deneme
    LAST_USED[path] = ""
    return av.open(path)


def encoder_works(name: str) -> bool:
    """Kodlayıcı derlemede var ve bu makinede gerçekten açılabiliyor mu (önbellekli)."""
    with _lock:
        if name in _enc_ok:
            return _enc_ok[name]
    ok = False
    try:
        av.codec.Codec(name, "w")
        buf = io.BytesIO()
        out = av.open(buf, "w", format="mp4")
        s = out.add_stream(name, rate=25)
        s.width, s.height, s.pix_fmt = 256, 144, "yuv420p"
        if name.endswith(("_qsv", "_vaapi")):
            s.pix_fmt = "nv12"
        img = np.zeros((144, 256, 3), np.uint8)
        for i in range(3):
            f = av.VideoFrame.from_ndarray(img, format="rgb24")
            f.pts = i
            for p in s.encode(f):
                out.mux(p)
        for p in s.encode():
            out.mux(p)
        out.close()
        ok = buf.tell() > 0
    except Exception:
        ok = False
    with _lock:
        _enc_ok[name] = ok
    return ok


def hw_encoder(kind: str) -> str | None:
    """'h264' / 'hevc' için çalışan ilk donanım kodlayıcı (yoksa None)."""
    for name in ENCODERS.get(kind, []):
        if encoder_works(name):
            return name
    return None


def summary() -> dict:
    return {"decoders": decoder_types(), "h264": hw_encoder("h264"), "hevc": hw_encoder("hevc")}
