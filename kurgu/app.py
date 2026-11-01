"""Uygulama başlatma ve koyu tema."""
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_dark_theme(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    base, alt, text = QColor("#1f2125"), QColor("#2a2d33"), QColor("#e8eaed")
    accent = QColor("#4f8cff")
    p.setColor(QPalette.Window, alt)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.ToolTipBase, alt)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor("#33373e"))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor("#ff5555"))
    p.setColor(QPalette.Highlight, accent)
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Link, accent)
    p.setColor(QPalette.PlaceholderText, QColor("#80868b"))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor("#6b7075"))
    app.setPalette(p)
    app.setStyleSheet("""
        QDockWidget::title { background:#25282d; padding:5px 8px; font-weight:bold; }
        QGroupBox { border:1px solid #3a3e45; border-radius:6px; margin-top:12px; padding-top:6px; }
        QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 4px; color:#9ec1ff; }
        QToolTip { color:#e8eaed; background:#2a2d33; border:1px solid #444; }
        QMenu::item:selected { background:#4f8cff; }
    """)


def run(path=None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Kurgu")
    app.setOrganizationName("Kurgu")
    apply_dark_theme(app)
    from .ui.main_window import MainWindow
    w = MainWindow(path)
    w.show()
    return app.exec()
