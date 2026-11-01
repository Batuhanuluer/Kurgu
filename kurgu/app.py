"""Uygulama başlatma ve koyu tema."""
import os
import sys
import time
import traceback

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


EMERGENCY: list = []  # çökme anında çağrılacak acil kayıt fonksiyonları
LOG_PATH = os.path.join(os.path.expanduser("~"), ".kurgu", "kurgu.log")
_shown: set = set()


def log_error(text: str):
    """Hataları ~/.kurgu/kurgu.log dosyasına yazar (Windows'ta konsol olmadığı için)."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
    except OSError:
        pass
    try:
        if sys.stderr:
            print(text, file=sys.stderr)
    except Exception:  # konsol kodlaması Türkçe karakteri yazamazsa
        pass


def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log_error(text)
    for cb in list(EMERGENCY):
        try:
            cb()
        except Exception:
            pass
    key = f"{exc_type.__name__}: {exc}"
    if key in _shown or QApplication.instance() is None:
        return
    _shown.add(key)
    from PySide6.QtWidgets import QMessageBox
    from .i18n import _
    box = QMessageBox(QMessageBox.Warning, "Kurgu",
                      _("Something went wrong. Your project was saved to the auto-save "
                        "folder.") + f"\n\n{key}")
    from PySide6.QtCore import Qt
    box.setTextFormat(Qt.PlainText)
    rep = box.addButton(_("Report a Problem…"), QMessageBox.ActionRole)
    box.addButton(QMessageBox.Ok)
    box.exec()
    if box.clickedButton() is rep:
        from .ui.report_dialog import ReportDialog
        ReportDialog(None, text).exec()


def install_qt_translations(app):
    """Qt'nin kendi düğmeleri (İptal, Evet/Hayır, dosya pencereleri) arayüz dilinde olsun."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    from . import i18n
    lang = i18n.effective()
    if lang == "en":
        return
    tr = QTranslator(app)
    if tr.load(QLocale(lang), "qtbase", "_",
               QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(tr)
        app._kurgu_qt_tr = tr  # çöp toplayıcı silmesin


def run(path=None) -> int:
    sys.excepthook = _excepthook
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Kurgu")
    app.setOrganizationName("Kurgu")
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icon.png")
    if os.path.exists(icon):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon))
    if sys.platform == "win32":  # görev çubuğunda Python yerine Kurgu simgesi görünsün
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kurgu.VideoEditor")
        except Exception:
            pass
    apply_dark_theme(app)
    install_qt_translations(app)
    from .ui.main_window import MainWindow
    w = MainWindow(path)
    w.show()
    return app.exec()
