"""Dışa aktarma: biçimler, hazır ayarlar ve kodlama.

Biçimler: H.264, H.265 (8/10-bit), ProRes 422 HQ (10-bit), VP9 (WebM), AV1, animasyonlu GIF,
yalnız ses (MP3 / WAV). 10-bit biçimlerde kareler 16 bit/kanal birleştirilir (Compositor
deep), böylece 10-bit kaynakların ve renk düzeltmesinin ara tonları korunur.

Hazır ayar (preset) = biçim + çözünürlük + kalite; hedefin en-boy oranı sekanstan farklıysa
(ör. 16:9 sekanstan 9:16 Reels) görüntü siyah kenarlarla ortalanır.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Callable

import av
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from .media import SourcePool
from .model import Project


@dataclass
class Format:
    key: str
    name: str
    ext: str
    vcodec: str | None
    pix_fmt: str | None
    acodec: str | None
    deep: bool = False          # 16 bit birleştirme + 10-bit kodlama
    crf: bool = True            # kalite CRF ile mi
    options: dict | None = None
    audio_rate: int | None = None


FORMATS = {f.key: f for f in [
    Format("h264", "MP4 · H.264", "mp4", "libx264", "yuv420p", "aac"),
    Format("hevc", "MP4 · H.265 (HEVC)", "mp4", "libx265", "yuv420p", "aac"),
    Format("hevc10", "MP4 · H.265 10-bit", "mp4", "libx265", "yuv420p10le", "aac", deep=True),
    Format("prores", "MOV · ProRes 422 HQ (10-bit)", "mov", "prores_ks", "yuv422p10le",
           "pcm_s16le", deep=True, crf=False, options={"profile": "3", "vendor": "apl0"}),
    Format("vp9", "WebM · VP9", "webm", "libvpx-vp9", "yuv420p", "libopus",
           audio_rate=48000),
    Format("av1", "MP4 · AV1", "mp4", "libsvtav1", "yuv420p", "aac"),
    Format("gif", "GIF (animated)", "gif", "gif", "pal8", None, crf=False),
    Format("mp3", "MP3 (audio only)", "mp3", None, None, "libmp3lame", crf=False),
    Format("wav", "WAV (audio only)", "wav", None, None, "pcm_s16le", crf=False),
]}


@dataclass
class Preset:
    key: str
    name: str
    fmt: str
    width: int | None = None     # None = sekansla aynı
    height: int | None = None
    quality: str = "high"        # high / medium / small
    max_fps: float | None = None


PRESETS = [
    Preset("match", "Match Sequence · H.264 (high quality)", "h264"),
    Preset("youtube1080", "YouTube 1080p", "h264", 1920, 1080),
    Preset("youtube4k", "YouTube 4K", "h264", 3840, 2160),
    Preset("reels", "Instagram Reels / TikTok / Shorts (9:16)", "h264", 1080, 1920),
    Preset("square", "Instagram Post (1:1)", "h264", 1080, 1080),
    Preset("x720", "X / Twitter 720p", "h264", 1280, 720, "medium"),
    Preset("hevc10", "H.265 10-bit (high quality, small file)", "hevc10"),
    Preset("prores", "Master · ProRes 422 HQ", "prores"),
    Preset("webm", "Web · WebM VP9", "vp9", quality="medium"),
    Preset("gif", "Animated GIF (480 px, 15 fps)", "gif", 480, None, max_fps=15),
    Preset("mp3", "Audio · MP3", "mp3"),
    Preset("wav", "Audio · WAV", "wav"),
]

CRF = {  # biçime göre kalite -> CRF
    "libx264": {"high": 18, "medium": 23, "small": 28},
    "libx265": {"high": 20, "medium": 25, "small": 30},
    "libvpx-vp9": {"high": 24, "medium": 32, "small": 40},
    "libsvtav1": {"high": 26, "medium": 34, "small": 42},
}


def available(fmt: Format) -> bool:
    for c in (fmt.vcodec, fmt.acodec):
        if c:
            try:
                av.codec.Codec(c, "w")
            except Exception:
                return False
    return True


def target_size(project: Project, preset: Preset | None, width=None, height=None):
    """Çıktı boyutu (çift sayılar). Yalnız genişlik verilirse oran korunur."""
    w = width or (preset.width if preset else None)
    h = height or (preset.height if preset else None)
    if w and not h:
        h = round(w * project.height / project.width)
    if h and not w:
        w = round(h * project.width / project.height)
    w, h = w or project.width, h or project.height
    return max(2, int(w) // 2 * 2), max(2, int(h) // 2 * 2)


# ------------------------------------------------------------ kare dönüştürme
def _qimage_rgb24(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)
    return arr[:, :w * 3].reshape(h, w, 3).copy()


def _qimage_rgb48(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGBX64)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), np.uint16, count=bpl * h // 2).reshape(h, bpl // 2)
    return np.ascontiguousarray(arr[:, :w * 4].reshape(h, w, 4)[..., :3])


def letterbox(img: QImage, w: int, h: int, deep: bool) -> QImage:
    if img.width() == w and img.height() == h:
        return img
    out = QImage(w, h, QImage.Format_RGBX64 if deep else QImage.Format_RGB32)
    out.fill(QColor(0, 0, 0))
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.drawImage(QRectF((w - img.width()) / 2, (h - img.height()) / 2, img.width(),
                       img.height()), img)
    p.end()
    return out


# ------------------------------------------------------------ GIF paleti
def median_cut(pixels: np.ndarray, n: int = 256) -> np.ndarray:
    """(N, 3) uint8 piksellerden n renklik palet (medyan kesme)."""
    boxes = [pixels.astype(np.int16)]
    while len(boxes) < n:
        i = max(range(len(boxes)), key=lambda k: (np.ptp(boxes[k], axis=0).max()
                                                   if len(boxes[k]) > 1 else -1))
        b = boxes[i]
        if len(b) < 2 or np.ptp(b, axis=0).max() == 0:
            break
        ch = int(np.argmax(np.ptp(b, axis=0)))
        b = b[np.argsort(b[:, ch], kind="stable")]
        mid = len(b) // 2
        boxes[i:i + 1] = [b[:mid], b[mid:]]
    pal = np.array([bx.mean(axis=0) for bx in boxes], np.float32)
    if len(pal) < n:
        pal = np.vstack([pal, np.zeros((n - len(pal), 3), np.float32)])
    return np.clip(pal + 0.5, 0, 255).astype(np.uint8)


def palette_lut(pal: np.ndarray) -> np.ndarray:
    """5 bit/kanal (32768) renk -> en yakın palet indeksi tablosu."""
    g = (np.arange(32) * 255 / 31).astype(np.float32)
    cube = np.stack(np.meshgrid(g, g, g, indexing="ij"), -1).reshape(-1, 3)
    pf = pal.astype(np.float32)
    out = np.empty(len(cube), np.uint8)
    for s in range(0, len(cube), 4096):
        d = ((cube[s:s + 4096, None, :] - pf[None]) ** 2).sum(-1)
        out[s:s + 4096] = d.argmin(1)
    return out


_BAYER = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
                   np.float32) / 16 - 0.5) * 8  # ±4: 5 bitlik adımın yarısı


def quantize(rgb: np.ndarray, lut: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    d = np.tile(_BAYER, (h // 4 + 1, w // 4 + 1))[:h, :w, None]
    q = np.clip((rgb.astype(np.float32) + d) * (31 / 255) + 0.5, 0, 31).astype(np.int32)
    return lut[(q[..., 0] << 10) | (q[..., 1] << 5) | q[..., 2]]


# ------------------------------------------------------------ dışa aktarma
def export_media(project: Project, path: str, fmt_key: str = "h264",
                 width: int | None = None, height: int | None = None,
                 quality: str = "high", start: float = 0.0, end: float | None = None,
                 max_fps: float | None = None,
                 progress: Callable[[float], None] | None = None,
                 cancelled: Callable[[], bool] | None = None,
                 hardware: bool = False) -> bool:
    """Sekansın [start, end) aralığını verilen biçimde yazar. İptal edilirse False.
    hardware: H.264/H.265 (8-bit) için çalışan bir GPU kodlayıcı varsa onu kullan."""
    from .render import Compositor, ensure_gui_app
    ensure_gui_app()
    fmt = FORMATS[fmt_key]
    w, h = target_size(project, None, width, height)
    end = project.duration if end is None else min(end, project.duration)
    start = max(0.0, min(start, end))
    fps = project.fps
    step = 1
    if max_fps and fps > max_fps:
        step = max(1, round(fps / max_fps))
    out_fps = fps / step
    sr = fmt.audio_rate or project.sample_rate
    n_frames = max(1, int(round((end - start) * out_fps)))
    # Sekans oranını koruyarak hedefe sığdır (gerekirse siyah kenar)
    s = min(w / project.width, h / project.height)
    cw, ch = max(2, int(project.width * s) // 2 * 2), max(2, int(project.height * s) // 2 * 2)
    comp = Compositor(project, SourcePool(project.sample_rate), deep=fmt.deep)
    out = av.open(path, "w")
    ok = False
    try:
        vs = ast = None
        hw_name = None
        if hardware and fmt.key in ("h264", "hevc"):
            from . import hw
            hw_name = hw.hw_encoder(fmt.key)
        if fmt.vcodec:
            vs = out.add_stream(hw_name or fmt.vcodec,
                                rate=Fraction(out_fps).limit_denominator(1001))
            vs.width, vs.height = w, h
            vs.pix_fmt = ("nv12" if hw_name and hw_name.endswith("_qsv") else fmt.pix_fmt)
            opts = dict(fmt.options or {})
            if hw_name:  # GPU kodlayıcılar: kaliteye göre bit hızı (her sürücüde çalışır)
                bpp = {"high": 0.22, "medium": 0.12, "small": 0.07}[quality]
                if fmt.key == "hevc":
                    bpp *= 0.6
                vs.bit_rate = int(w * h * out_fps * bpp)
                if hw_name.endswith("_nvenc"):
                    opts["preset"] = "p5"
            elif fmt.crf and fmt.vcodec in CRF:
                opts["crf"] = str(CRF[fmt.vcodec][quality])
                if fmt.vcodec == "libx264":
                    opts["preset"] = "veryfast" if quality != "high" else "medium"
                elif fmt.vcodec == "libx265":
                    opts["preset"] = "fast"
                    opts["x265-params"] = "log-level=error"
                elif fmt.vcodec == "libvpx-vp9":
                    opts["b"] = "0"
                    opts["row-mt"] = "1"
                    opts["deadline"] = "good"
                    opts["cpu-used"] = "4"
                elif fmt.vcodec == "libsvtav1":
                    opts["preset"] = "8"
            if fmt.key in ("hevc", "hevc10") and fmt.ext == "mp4":
                vs.codec_tag = "hvc1"  # Apple cihazlarda oynatılabilsin
            vs.options = opts
        if fmt.acodec:
            ast = out.add_stream(fmt.acodec, rate=sr)
            ast.layout = "stereo"
            if fmt.acodec in ("aac", "libmp3lame", "libopus"):
                ast.bit_rate = {"high": 320000, "medium": 192000, "small": 128000}[quality] \
                    if fmt.acodec == "libmp3lame" else 192000
        lut = None
        if fmt.key == "gif":
            # Ortak palet: aralıktan seçilen karelerden
            samples = []
            for t in np.linspace(start, max(start, end - 1e-3), 8):
                rgb = _qimage_rgb24(letterbox(comp.compose(float(t), cw, ch), w, h, False))
                samples.append(rgb.reshape(-1, 3)[::7])
            pal = median_cut(np.concatenate(samples), 256)
            lut = palette_lut(pal)
            bgra = np.zeros((256, 4), np.uint8)  # FFmpeg pal8: BGRA sırası
            bgra[:, 0], bgra[:, 1], bgra[:, 2], bgra[:, 3] = pal[:, 2], pal[:, 1], pal[:, 0], 255
        a_src = None
        if ast is not None:
            from .render import Compositor as _C
            a_src = _C(project, SourcePool(sr)) if sr != project.sample_rate else comp
            if sr != project.sample_rate:
                a_src.project = _resampled(project, sr)
        samples_done = 0
        a_frame = 1024 if fmt.acodec != "libopus" else 960
        total_samples = int(round((end - start) * sr))
        for i in range(n_frames if vs is not None else 0):
            if cancelled and cancelled():
                return False
            t = start + i / out_fps
            img = letterbox(comp.compose(t, cw, ch), w, h, fmt.deep)
            if fmt.key == "gif":
                vf = av.VideoFrame.from_ndarray((quantize(_qimage_rgb24(img), lut), bgra),
                                                format="pal8")
            elif fmt.deep:
                vf = av.VideoFrame.from_ndarray(_qimage_rgb48(img), format="rgb48le")
            else:
                vf = av.VideoFrame.from_ndarray(_qimage_rgb24(img), format="rgb24")
            vf.pts = i
            for pkt in vs.encode(vf):
                out.mux(pkt)
            if ast is not None:
                target = min(int(round((i + 1) / out_fps * sr)), total_samples)
                samples_done = _write_audio(a_src, ast, out, start, sr, samples_done, target,
                                            a_frame, final=(i == n_frames - 1))
            if progress and (i % 5 == 0 or i == n_frames - 1):
                progress((i + 1) / n_frames)
        if vs is None and ast is not None:  # yalnız ses
            while samples_done < total_samples:
                if cancelled and cancelled():
                    return False
                tgt = min(samples_done + sr, total_samples)
                samples_done = _write_audio(a_src, ast, out, start, sr, samples_done, tgt,
                                            a_frame, final=tgt >= total_samples)
                if progress:
                    progress(samples_done / max(total_samples, 1))
        if vs is not None:
            for pkt in vs.encode():
                out.mux(pkt)
        if ast is not None:
            for pkt in ast.encode():
                out.mux(pkt)
        ok = True
        return True
    finally:
        out.close()
        comp.pool.close()
        if not ok:
            import os
            try:
                os.remove(path)
            except OSError:
                pass


def _resampled(project: Project, sr: int) -> Project:
    q = Project.from_dict(project.to_dict())
    q.sample_rate = sr
    return q


def _audio_fmt(ast) -> str:
    names = [f.name for f in (ast.codec_context.codec.audio_formats or [])]
    for want in ("fltp", "flt", "s16p", "s16"):
        if not names or want in names:
            return want
    return names[0]


def _write_audio(comp, ast, out, start, sr, done, target, chunk, final=False):
    fmt = _audio_fmt(ast)
    while done + chunk <= target or (final and done < target):
        n = min(chunk, target - done)
        if n <= 0:
            break
        pcm = comp.mix(start + done / sr, n)
        if n < chunk and ast.codec_context.name in ("libopus", "opus"):
            pcm = np.vstack([pcm, np.zeros((chunk - n, 2), np.float32)])  # sabit çerçeve
        pcm = np.clip(pcm, -1, 1)
        if fmt.startswith("s16"):
            pcm = (pcm * 32767).astype(np.int16)
        else:
            pcm = pcm.astype(np.float32)
        # düzlemsel (p): (kanal, örnek); paketli: (1, örnek·kanal)
        data = pcm.T if fmt.endswith("p") else pcm.reshape(1, -1)
        af = av.AudioFrame.from_ndarray(np.ascontiguousarray(data), format=fmt,
                                        layout="stereo")
        af.sample_rate = sr
        af.pts = done
        for pkt in ast.encode(af):
            out.mux(pkt)
        done += n
    return done


def export_frame(project: Project, t: float, path: str, width=None, height=None) -> str:
    """Tek kareyi PNG/JPEG olarak kaydeder (Premiere "Kareyi Dışa Aktar")."""
    from .render import Compositor, ensure_gui_app
    ensure_gui_app()
    w, h = target_size(project, None, width, height)
    comp = Compositor(project, SourcePool(project.sample_rate))
    try:
        img = comp.compose(t, w, h)
        if not img.save(path):
            raise OSError(f"Kaydedilemedi: {path}")
    finally:
        comp.pool.close()
    return path
