"""Maskeler ve karıştırma modları.

Maske, klibin kendi karesinde (dönüşümden önce, 0..1 normalize koordinat) tanımlıdır;
böylece klip taşınınca/ölçeklenince maske de onunla gider (Premiere'deki gibi).

Maske sözlüğü:
    {"id", "shape": "ellipse" | "rect" | "polygon",
     "box": [x0, y0, x1, y1]          # elips/dikdörtgen
     "points": [[x, y], ...]          # çokgen (kalem)
     "feather": px, "expansion": px, "opacity": 0..1,
     "invert": bool, "mode": "add" | "subtract" | "intersect"}
px değerleri proje pikselidir (önizleme ve dışa aktarmada aynı görünür).
"""
from __future__ import annotations

import uuid

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen

BLEND_MODES = [
    ("normal", "Normal"), ("multiply", "Multiply"), ("screen", "Screen"),
    ("overlay", "Overlay"), ("darken", "Darken"), ("lighten", "Lighten"),
    ("color_dodge", "Color Dodge"), ("color_burn", "Color Burn"),
    ("hard_light", "Hard Light"), ("soft_light", "Soft Light"),
    ("difference", "Difference"), ("exclusion", "Exclusion"), ("add", "Add"),
]


def composition_mode(name: str):
    M = QPainter.CompositionMode
    return {
        "multiply": M.CompositionMode_Multiply, "screen": M.CompositionMode_Screen,
        "overlay": M.CompositionMode_Overlay, "darken": M.CompositionMode_Darken,
        "lighten": M.CompositionMode_Lighten, "color_dodge": M.CompositionMode_ColorDodge,
        "color_burn": M.CompositionMode_ColorBurn, "hard_light": M.CompositionMode_HardLight,
        "soft_light": M.CompositionMode_SoftLight, "difference": M.CompositionMode_Difference,
        "exclusion": M.CompositionMode_Exclusion, "add": M.CompositionMode_Plus,
    }.get(name, M.CompositionMode_SourceOver)


def new_mask(shape: str = "ellipse") -> dict:
    m = {"id": uuid.uuid4().hex[:8], "shape": shape, "feather": 20.0, "expansion": 0.0,
         "opacity": 1.0, "invert": False, "mode": "add"}
    if shape == "polygon":
        m["points"] = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]
    else:
        m["box"] = [0.25, 0.25, 0.75, 0.75]
    return m


def mask_path(m: dict, w: float, h: float) -> QPainterPath:
    path = QPainterPath()
    if m.get("shape") == "polygon":
        pts = m.get("points") or []
        if len(pts) >= 3:
            path.moveTo(QPointF(pts[0][0] * w, pts[0][1] * h))
            for x, y in pts[1:]:
                path.lineTo(QPointF(x * w, y * h))
            path.closeSubpath()
    else:
        x0, y0, x1, y1 = m.get("box") or [0.25, 0.25, 0.75, 0.75]
        r = QRectF(QPointF(min(x0, x1) * w, min(y0, y1) * h),
                   QPointF(max(x0, x1) * w, max(y0, y1) * h))
        if m.get("shape") == "rect":
            path.addRect(r)
        else:
            path.addEllipse(r)
    return path


def _raster(m: dict, w: int, h: int, exp: float) -> np.ndarray:
    """Maskeyi (genişletme dahil) w×h boyutunda 0..1 kaplamaya çizer."""
    img = QImage(w, h, QImage.Format_Grayscale8)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = mask_path(m, w, h)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawPath(path)
    if abs(exp) > 0.5:
        st = QPainterPathStroker()
        st.setWidth(2 * abs(exp))
        st.setJoinStyle(Qt.RoundJoin)
        ring = st.createStroke(path)
        if exp > 0:
            p.drawPath(ring)
        else:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.drawPath(ring)
    p.end()
    bpl = img.bytesPerLine()
    a = np.frombuffer(img.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)[:, :w]
    return a.astype(np.float32) / 255.0


def _one(m: dict, w: int, h: int, px: float) -> np.ndarray:
    """Tek maskenin 0..1 kaplama dizisi (yumuşatma ve genişletme dahil).
    Yumuşatma bulanık olduğundan büyük yumuşatmada maske küçük çizilip bulanıklaştırılır ve
    sonra büyütülür: sonuç aynı görünür, 4K'da onlarca kat hızlıdır."""
    f = float(m.get("feather", 0.0)) * px
    exp = float(m.get("expansion", 0.0)) * px
    k = int(min(4, max(1, f / 8)))  # küçültme katsayısı
    if k > 1:
        sw, sh = max(2, -(-w // k)), max(2, -(-h // k))
        a = _raster(m, sw, sh, exp / k)
        from .effects import blur_array
        a = blur_array(a, f / 2 / k)
        q = QImage(np.ascontiguousarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)).data,
                   sw, sh, sw, QImage.Format_Grayscale8)
        q = q.scaled(sw * k, sh * k, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        bpl = q.bytesPerLine()
        big = np.frombuffer(q.constBits(), np.uint8, count=bpl * q.height()).reshape(
            q.height(), bpl)[:h, :w]
        return big.astype(np.float32) / 255.0
    a = _raster(m, w, h, exp)
    if f >= 1:
        from .effects import blur_array
        a = blur_array(a, f / 2)  # yumuşak kenar: kenarın iki yanına yayılır
    return np.clip(a, 0, 1)


_CACHE: dict = {}


def mask_alpha(masks: list, w: int, h: int, px: float = 1.0) -> np.ndarray | None:
    """Klip karesi için birleşik maske (h, w) 0..1; maske yoksa None.
    px: dizi pikseli / proje pikseli. Maskeler değişmedikçe sonuç önbellekten gelir
    (oynatırken her karede yeniden hesaplanmaz)."""
    active = [m for m in masks or [] if m.get("enabled", True)]
    if not active:
        return None
    import json
    key = (json.dumps(active, sort_keys=True, default=str), w, h, round(px, 4))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    out = None
    for m in active:
        a = _one(m, w, h, px) * float(m.get("opacity", 1.0))
        if m.get("invert"):
            a = 1.0 - a
        mode = m.get("mode", "add")
        if out is None:
            out = a if mode != "subtract" else 1.0 - a
        elif mode == "subtract":
            out = out * (1.0 - a)
        elif mode == "intersect":
            out = np.minimum(out, a)
        else:
            out = np.maximum(out, a)
    out.setflags(write=False)  # önbellekteki dizi yanlışlıkla değişmesin
    if len(_CACHE) > 24:
        _CACHE.clear()
    _CACHE[key] = out
    return out


def matte_alpha(layer: QImage, kind: str = "alpha", invert: bool = False) -> np.ndarray:
    """İz matı: başka bir izin görüntüsünden alfa (saydamlık) ya da parlaklık."""
    img = layer.convertToFormat(QImage.Format_RGBA8888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    a = np.frombuffer(img.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)[:, :w * 4]
    a = a.reshape(h, w, 4).astype(np.float32)
    if kind == "luma":
        al = a[..., 3] / 255.0
        # Önceden çarpılmamış RGBA: parlaklık × kaplama
        v = (a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722) / 255.0 * al
    else:
        v = a[..., 3] / 255.0
    return 1.0 - v if invert else v


def apply_alpha_to_layer(layer: QImage, alpha: np.ndarray) -> QImage:
    """ARGB32_Premultiplied katmanın tüm kanallarını alfa ile çarpar (önceden çarpılmış)."""
    img = layer.convertToFormat(QImage.Format_RGBA8888_Premultiplied)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    a = np.frombuffer(img.bits(), np.uint8, count=bpl * h).reshape(h, bpl)[:, :w * 4]
    a = a.reshape(h, w, 4)
    a[...] = (a.astype(np.float32) * alpha[..., None] + 0.5).astype(np.uint8)
    return img


def outline_path(m: dict) -> QPainterPath:
    """Monitör üstü çizim için 0..1 koordinatlı yol."""
    return mask_path(m, 1.0, 1.0)


__all__ = ["BLEND_MODES", "composition_mode", "new_mask", "mask_alpha", "matte_alpha",
           "apply_alpha_to_layer", "outline_path", "QPen"]
