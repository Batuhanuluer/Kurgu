"""Görüntü birleştirme (compositing), ses miksajı ve dışa aktarma.

Katmanlar QPainter ile birleştirilir: ölçekleme, döndürme, saydamlık ve
alfa karıştırma C++ tarafında yapıldığı için Python'da bile hızlıdır.
"""
from __future__ import annotations

import math
import time
from typing import Callable

import av
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

from . import effects as fx
from . import keyframes as kf
from . import transitions
from .media import SourcePool, display_size, oriented_array
from .model import Clip, Project

ALPHA_FORMATS = ("rgba", "bgra", "argb", "abgr", "yuva", "ya8", "ya16", "pal8")


def ensure_gui_app():
    """Metin çizimi için bir QGuiApplication gerekir (komut satırında da)."""
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.instance() is None:
        import os
        import sys
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        ensure_gui_app.app = QGuiApplication(sys.argv[:1])


def high_bit(frame) -> bool:
    try:
        return max(c.bits for c in frame.format.components) > 8
    except Exception:
        return False


def read_high_bit(frame, w: int, h: int, alpha: bool, k: int = 0) -> np.ndarray:
    """10/12-bit kareyi 16-bit okuyup dither'lı olarak 8-bit'e indirir: kaybolacak ara tonlar
    ince grene dönüşür, sonradan kontrast açılınca bantlanma oluşmaz."""
    a = oriented_array(frame, w, h, "rgb48le", k).astype(np.float32) * (1 / 257)
    a += fx._dither((h, w))
    rgb = np.clip(np.rint(a), 0, 255).astype(np.uint8)
    if not alpha:
        return rgb
    out = np.empty((h, w, 4), np.uint8)
    out[..., :3] = rgb
    out[..., 3] = 255
    return out


def adjust_color(arr: np.ndarray, clip: Clip) -> np.ndarray:
    if clip.brightness == 0 and clip.contrast == 1 and clip.saturation == 1:
        return arr
    rgb = arr[..., :3].astype(np.float32)
    if clip.contrast != 1 or clip.brightness != 0:
        rgb = (rgb - 128.0) * clip.contrast + 128.0 + clip.brightness * 255.0
    if clip.saturation != 1:
        gray = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
        rgb = gray[..., None] + (rgb - gray[..., None]) * clip.saturation
    out = arr.copy()
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return out


class Compositor:
    def __init__(self, project: Project, pool: SourcePool | None = None):
        self.project = project
        self.pool = pool or SourcePool(project.sample_rate)
        self.disabled_effect = None  # damlalık için geçici olarak kapatılan efekt

    # ------------------------------------------------------------ görüntü
    def track_state(self, track: int, t: float):
        """Bir izde t anında ne çizileceği:
        ("clip", klip) | ("trans", A, B, ilerleme, tür) | None"""
        p = self.project
        clips = [c for c in p.clips if c.track == track]
        for c in clips:
            info = p.trans_in_info(c)
            if info is not None:
                prev, w0, d = info
                if w0 <= t < w0 + d:
                    return ("trans", prev, c, (t - w0) / d, c.trans_in)
            out = p.trans_out_info(c)
            if out is not None:
                w0, d = out
                if w0 <= t < c.end:
                    return ("trans", c, None, (t - w0) / d, c.trans_out)
        best = None
        for c in clips:
            if c.contains(t):
                best = c
        return ("clip", best) if best is not None else None

    def active_clip(self, track: int, t: float) -> Clip | None:
        st = self.track_state(track, t)
        if st is None:
            return None
        return st[1] if st[0] == "clip" else (st[2] or st[1])

    def compose(self, t: float, out_w: int, out_h: int) -> QImage:
        p = self.project
        img = QImage(out_w, out_h, QImage.Format_RGB32)
        img.fill(QColor(0, 0, 0))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            for ti in p.track_indices("video"):  # V1 en altta
                if p.tracks[ti].muted:
                    continue
                st = self.track_state(ti, t)
                if st is None:
                    continue
                if st[0] == "clip":
                    self.draw_clip(painter, st[1], t, out_w, out_h)
                else:
                    _k, a, b, prog, kind = st
                    la = self.render_layer(a, t, out_w, out_h) if a is not None else None
                    lb = self.render_layer(b, t, out_w, out_h) if b is not None else None
                    transitions.draw(painter, kind, la, lb, prog, out_w, out_h)
        finally:
            painter.end()
        return img

    def render_layer(self, clip: Clip, t: float, w: int, h: int) -> QImage:
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.SmoothPixmapTransform, True)
        q.setRenderHint(QPainter.Antialiasing, True)
        self.draw_clip(q, clip, t, w, h)
        q.end()
        return img

    def draw_clip(self, painter: QPainter, clip: Clip, t: float, out_w: int, out_h: int):
        clip = kf.animated(clip, t)  # anahtar karelerden o anki değerler
        k = out_w / self.project.width  # proje pikselinden çıktı pikseline
        tt = min(max(t, clip.start), clip.end - 1e-6)  # geçiş uzantısında solma sabit
        alpha = clip.opacity * clip.fade_factor(tt)
        if alpha <= 0.001:
            return
        painter.save()
        painter.setOpacity(min(alpha, 1.0))
        painter.translate(out_w / 2 + clip.pos_x * k, out_h / 2 + clip.pos_y * k)
        if clip.rotation:
            painter.rotate(clip.rotation)
        if clip.kind == "text":
            self._draw_text(painter, clip, k)
        else:
            self._draw_media(painter, clip, t, k)
        painter.restore()

    def _draw_media(self, painter: QPainter, clip: Clip, t: float, k: float):
        p = self.project
        media = p.media.get(clip.media_id)
        if media is None:
            return
        src = self.pool.video_source(media)
        if src is None:
            return
        st = clip.source_time(t)
        if media.kind != "image":  # geçiş uzantısında kaynak dışına taşma: kareyi dondur
            st = min(max(st, 0.0), max(media.duration - 0.5 / max(media.fps, 1), 0.0))
        frame = src.frame_at(st)
        if frame is None:
            return
        rot = media.rotation if media.kind == "video" and not getattr(src, "is_proxy", False) else 0
        fw, fh = display_size(frame, rot)
        fit = min(p.width / fw, p.height / fh)
        s = fit * clip.scale * k
        dw, dh = max(1, int(round(fw * s))), max(1, int(round(fh * s)))
        # Döndürme yoksa ve büyütme yapılmıyorsa ölçeklemeyi swscale yapsın (hızlı).
        tw, th = (dw, dh) if (s <= 1.0 and not clip.rotation) else (fw, fh)
        dis = self.disabled_effect
        effects = [e for e in clip.effects
                   if dis is None or (e is not dis and (not e.get("id")
                                                        or e.get("id") != dis.get("id")))]
        has_alpha = frame.format.name.startswith(ALPHA_FORMATS) or fx.needs_alpha(effects)
        fmt = "rgba" if has_alpha else "rgb24"
        hibit = bool(effects) and fx.is_grading(effects) and high_bit(frame)
        if hibit:
            arr = read_high_bit(frame, tw, th, has_alpha, rot)
        else:
            arr = oriented_array(frame, tw, th, fmt, rot)
        arr = adjust_color(arr, clip)
        if effects:
            # dizi pikseli / proje pikseli: efekt boyutları önizleme ve çıktıda aynı görünsün
            scale = tw / max(fw * fit * clip.scale, 1e-6)
            arr = fx.apply_effects(arr, effects, {"scale": scale, "t": t, "hibit": hibit})
        arr = np.ascontiguousarray(arr)
        qfmt = QImage.Format_RGBA8888 if has_alpha else QImage.Format_RGB888
        qimg = QImage(arr.data, tw, th, arr.strides[0], qfmt)
        painter.drawImage(QRectF(-dw / 2, -dh / 2, dw, dh), qimg)

    def _draw_text(self, painter: QPainter, clip: Clip, k: float):
        font = QFont()
        font.setPixelSize(max(1, int(clip.font_size * clip.scale * k)))
        font.setBold(True)
        path = QPainterPath()
        lines = clip.text.split("\n") or [""]
        from PySide6.QtGui import QFontMetricsF
        fm = QFontMetricsF(font)
        lh = fm.height()
        total = lh * len(lines)
        for i, line in enumerate(lines):
            w = fm.horizontalAdvance(line)
            path.addText(QPointF(-w / 2, -total / 2 + fm.ascent() + i * lh), font, line)
        outline = max(1.0, font.pixelSize() / 16)
        painter.setPen(QPen(QColor(0, 0, 0, 200), outline * 2, Qt.SolidLine, Qt.RoundCap,
                            Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(clip.color))
        painter.drawPath(path)

    # ------------------------------------------------------------ ses
    def clip_audio_span(self, c: Clip):
        """Geçiş uzantıları dahil klibin duyulduğu aralık ve kazanç zarfı bilgisi."""
        p = self.project
        lo, hi = c.start, c.end
        tin = p.trans_in_info(c)
        if tin is not None and tin[0] is not None:
            lo = tin[1]
        nxt = p.next_adjacent(c)
        tnx = p.trans_in_info(nxt) if nxt is not None else None
        if tnx is not None and tnx[0] is c:
            hi = tnx[1] + tnx[2]
        else:
            tnx = None
        tout = p.trans_out_info(c) if tnx is None else None
        return lo, hi, tin, tnx, tout

    def mix(self, t: float, n: int) -> np.ndarray:
        """t anından itibaren n örneklik stereo miks (n, 2) float32."""
        p = self.project
        sr = p.sample_rate
        out = np.zeros((n, 2), np.float32)
        t1 = t + n / sr
        for ti in p.track_indices("audio"):
            if p.tracks[ti].muted:
                continue
            for c in p.clips:
                if c.track != ti or (c.volume <= 0 and not kf.is_animated(c, "volume")):
                    continue
                lo, hi, tin, tnx, tout = self.clip_audio_span(c)
                if hi <= t or lo >= t1:
                    continue
                media = p.media.get(c.media_id)
                if media is None:
                    continue
                src = self.pool.audio_source(media)
                if src is None:
                    continue
                s0 = max(t, lo)
                s1 = min(t1, hi)
                i0 = int(round((s0 - t) * sr))
                count = min(n - i0, int(round((s1 - s0) * sr)))
                if count <= 0:
                    continue
                st = c.source_time(s0)
                if abs(c.speed - 1.0) < 1e-6:
                    data = src.read(st, count)
                else:
                    m = int(math.ceil(count * c.speed)) + 1
                    raw = src.read(st, m)
                    xs = np.linspace(0, m - 1, count)
                    idx = np.arange(m)
                    data = np.stack([np.interp(xs, idx, raw[:, 0]),
                                     np.interp(xs, idx, raw[:, 1])], axis=1).astype(np.float32)
                times = s0 + np.arange(count, dtype=np.float64) / sr
                if kf.is_animated(c, "volume"):
                    src_t = c.in_point + (times - c.start) * c.speed
                    gain = kf.eval_keys_array(kf.keys(c, "volume"), src_t)
                else:
                    gain = np.full(count, c.volume, np.float32)
                if c.fade_in > 0 or c.fade_out > 0:
                    ct = np.clip(times, c.start, c.end)
                    if c.fade_in > 0:
                        gain *= np.clip((ct - c.start) / c.fade_in, 0, 1).astype(np.float32)
                    if c.fade_out > 0:
                        gain *= np.clip((c.end - ct) / c.fade_out, 0, 1).astype(np.float32)
                # Geçişler: sabit güçlü crossfade (sin/cos)
                if tin is not None:
                    q = np.clip((times - tin[1]) / tin[2], 0, 1)
                    gain *= np.sin(q * math.pi / 2).astype(np.float32)
                if tnx is not None:
                    q = np.clip((times - tnx[1]) / tnx[2], 0, 1)
                    gain *= np.cos(q * math.pi / 2).astype(np.float32)
                elif tout is not None:
                    q = np.clip((times - tout[0]) / tout[1], 0, 1)
                    gain *= np.cos(q * math.pi / 2).astype(np.float32)
                out[i0:i0 + count] += data * gain[:, None]
        np.clip(out, -1.0, 1.0, out=out)
        return out


def qimage_to_rgb(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)
    return arr[:, :w * 3].reshape(h, w, 3).copy()


def pick_video_codec() -> str:
    for name in ("libx264", "h264", "mpeg4"):
        try:
            av.codec.Codec(name, "w")
            return name
        except Exception:
            continue
    raise RuntimeError("Uygun video kodlayıcı bulunamadı")


def export(project: Project, path: str, width: int | None = None, height: int | None = None,
           quality: int = 20, start: float = 0.0, end: float | None = None,
           progress: Callable[[float], None] | None = None,
           cancelled: Callable[[], bool] | None = None) -> bool:
    """Projeyi MP4 (H.264 + AAC) olarak dışa aktarır. İptal edilirse False döner."""
    ensure_gui_app()
    w = (width or project.width) // 2 * 2
    h = (height or project.height) // 2 * 2
    end = project.duration if end is None else end
    fps = project.fps
    sr = project.sample_rate
    total_frames = max(1, int(round((end - start) * fps)))
    comp = Compositor(project, SourcePool(sr))
    from fractions import Fraction
    rate = Fraction(fps).limit_denominator(1001)
    out = av.open(path, "w")
    try:
        codec = pick_video_codec()
        vs = out.add_stream(codec, rate=rate)
        vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
        if codec == "libx264":
            vs.options = {"crf": str(quality), "preset": "veryfast"}
        else:
            vs.bit_rate = int(w * h * fps * 0.12)
        ast = out.add_stream("aac", rate=sr)
        ast.layout = "stereo"
        ast.bit_rate = 192000
        samples_done = 0
        chunk = 1024
        t0 = time.time()
        for i in range(total_frames):
            if cancelled and cancelled():
                return False
            t = start + i / fps
            rgb = qimage_to_rgb(comp.compose(t, w, h))
            vf = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            vf.pts = i
            for pkt in vs.encode(vf):
                out.mux(pkt)
            target = int(round((i + 1) / fps * sr))
            while samples_done + chunk <= target or (i == total_frames - 1
                                                    and samples_done < target):
                n = min(chunk, max(target - samples_done, 0)) if i == total_frames - 1 else chunk
                if n <= 0:
                    break
                pcm = comp.mix(start + samples_done / sr, n)
                af = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm.T), format="fltp",
                                                layout="stereo")
                af.sample_rate = sr
                af.pts = samples_done
                for pkt in ast.encode(af):
                    out.mux(pkt)
                samples_done += n
            if progress and (i % 5 == 0 or i == total_frames - 1):
                progress((i + 1) / total_frames)
        for pkt in vs.encode():
            out.mux(pkt)
        for pkt in ast.encode():
            out.mux(pkt)
        _ = t0
        return True
    finally:
        out.close()
        comp.pool.close()
