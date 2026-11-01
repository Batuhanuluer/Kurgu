"""Kurgu simgesini üretir: kurgu/resources/icon.png (+ .ico, .icns).

Çalıştır: python packaging/make_icon.py
"""
import os
import sys

# Windows CI/konsolda çıktı kodlaması cp1252 olabilir: Türkçe karakterler çökmesin
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "kurgu", "resources")


def draw(size=1024) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = size / 1024
    # Arka plan: yuvarlatılmış kare, koyu laciverdan maviye
    bg = QLinearGradient(0, 0, size, size)
    bg.setColorAt(0, QColor("#1b2440"))
    bg.setColorAt(1, QColor("#2f63b8"))
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(40 * s, 40 * s, 944 * s, 944 * s), 210 * s, 210 * s)
    # Film şeridi delikleri (üst ve alt)
    p.setBrush(QColor(255, 255, 255, 40))
    for i in range(6):
        x = (170 + i * 130) * s
        p.drawRoundedRect(QRectF(x, 110 * s, 70 * s, 46 * s), 10 * s, 10 * s)
        p.drawRoundedRect(QRectF(x, 868 * s, 70 * s, 46 * s), 10 * s, 10 * s)
    # Zaman çizelgesi klipleri: üç renkli şerit, ortadan kesilmiş
    clips = [("#5b8def", 250, 330), ("#7fd18b", 250, 455), ("#b07be0", 250, 580)]
    for col, x0, y in clips:
        p.setBrush(QColor(col))
        p.drawRoundedRect(QRectF(x0 * s, y * s, 230 * s, 90 * s), 20 * s, 20 * s)
        p.drawRoundedRect(QRectF((x0 + 270) * s, y * s, 250 * s, 90 * s), 20 * s, 20 * s)
    # Kesme çizgisi (oynatma kafası / jilet)
    p.setPen(QPen(QColor("#ff5a4f"), 26 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(500 * s, 250 * s), QPointF(500 * s, 760 * s))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ff5a4f"))
    p.drawPolygon(QPolygonF([QPointF(440 * s, 215 * s), QPointF(560 * s, 215 * s),
                             QPointF(500 * s, 290 * s)]))
    p.end()
    return img


def main():
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    os.makedirs(OUT, exist_ok=True)
    png = os.path.join(OUT, "icon.png")
    draw(1024).save(png)
    draw(256).save(os.path.join(OUT, "icon-256.png"))
    try:
        from PIL import Image
        im = Image.open(png)
        im.save(os.path.join(OUT, "icon.ico"),
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        im.save(os.path.join(OUT, "icon.icns"))
    except Exception as e:  # Pillow yoksa yalnızca PNG
        print("ico/icns üretilemedi:", e)
    print("simge yazıldı:", OUT)


if __name__ == "__main__":
    main()
