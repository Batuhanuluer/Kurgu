"""Kullanıcı gibi tıklama testi: her menü komutu ve panellerdeki her düğme gerçekten
tetiklenir (farklı seçimlerle). Açılan pencereler otomatik kapatılır; yakalanmamış her hata
(ör. Qt'nin 'checked' değerini yanlış parametreye geçirmesi) testi düşürür."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r'''
import sys, traceback
sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + "/tests")
from PySide6.QtWidgets import (QApplication, QDialog, QMessageBox, QFileDialog, QInputDialog,
                               QColorDialog, QMenu, QAbstractButton, QDockWidget, QComboBox)
from PySide6.QtGui import QColor, QAction
app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
errors = []
def hook(t, e, tb):
    errors.append("".join(traceback.format_exception(t, e, tb)))
sys.excepthook = hook
import kurgu.app as kapp
kapp._excepthook = hook
# Modal pencereler: hemen kapat. ACCEPT turunda "Tamam" denmiş gibi.
import re
ACCEPT = [False]
BAD = re.compile(r"object has no attribute|is not callable|unexpected keyword|positional argument|"
                 r"Traceback|TypeError|NameError|AttributeError|NoneType|IndexError|KeyError|"
                 r"ValueError|UnboundLocalError")
def seen_text(t):
    t = str(t or "")
    if BAD.search(t):
        errors.append("Hata penceresi: " + t)
QDialog.exec = lambda self: 1 if ACCEPT[0] else 0
def mb_exec(self):
    seen_text(self.text() + " " + self.informativeText())
    return 0
QMessageBox.exec = mb_exec
for n in ("question", "information", "warning", "critical", "about", "aboutQt"):
    setattr(QMessageBox, n, staticmethod(lambda *a, **k: (seen_text(a[2] if len(a) > 2 else ""),
                                                          QMessageBox.No)[1]))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
QInputDialog.getMultiLineText = staticmethod(lambda *a, **k: ("", False))
QInputDialog.getItem = staticmethod(lambda *a, **k: ("", False))
QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.0, False))
QInputDialog.getInt = staticmethod(lambda *a, **k: (0, False))
QColorDialog.getColor = staticmethod(lambda *a, **k: QColor())
QMenu.exec = lambda self, *a, **k: None
from kurgu import updater
updater.fetch_latest = lambda *a, **k: None   # ağa çıkma
from kurgu import captions as _cap
def fake_transcribe(audio, language="", model_size="small", progress=None, cancelled=None,
                    engine=None):
    if engine is not None:
        engine.transcribe  # yanlış nesne geldiyse AttributeError (bugünkü hata)
    return [], language
_cap.transcribe = fake_transcribe              # model indirmeden
from conftest import make_video
red = make_video(TMP + "/r.mp4", seconds=2, w=320, h=180, color=(220, 40, 40))
blue = make_video(TMP + "/b.mp4", seconds=2, w=320, h=180, color=(40, 40, 220))
from kurgu.ui.main_window import MainWindow
w = MainWindow(None); w.resize(1500, 950); w.show(); app.processEvents()

def setup():
    r, b = w.import_paths([red, blue])
    w.place_media(r, 0.0, None)
    v2 = w.project.track_indices("video")[1]
    w.place_media(b, 1.0, v2)
    w.project.add_text("Başlık", 0.5)
    w.project.add_captions([(0.2, 1.0, "Altyazı")])
    w.timeline.set_playhead(1.5)
    w.on_edited()
    return w.project

SKIP = {"Quit", "Çıkış", "Check for Updates…", "Güncellemeleri Denetle…"}

def all_actions():
    seen, out = set(), []
    def walk(menu):
        for a in menu.actions():
            if a.menu():
                walk(a.menu())
            elif not a.isSeparator() and id(a) not in seen:
                seen.add(id(a)); out.append(a)
    for a in w.menuBar().actions():
        if a.menu():
            walk(a.menu())
    for a in w.actions():
        if id(a) not in seen:
            seen.add(id(a)); out.append(a)
    return out

def select(kind):
    p = w.project
    cs = {"video": [c for c in p.clips if c.kind == "media" and p.track_is_video(c.track)],
          "audio": [c for c in p.clips if not p.track_is_video(c.track)],
          "text": [c for c in p.clips if c.kind == "text"],
          "caption": [c for c in p.clips if c.kind == "caption"],
          "none": []}[kind]
    w.timeline.selected = {cs[0].id} if cs else set()
    w._on_selection(); app.processEvents()

def click_buttons(where):
    for dock in w.findChildren(QDockWidget):
        dock.show(); dock.raise_(); app.processEvents()
        for b in dock.findChildren(QAbstractButton):
            if not b.isVisible() or not b.isEnabled():
                continue
            try:
                b.click()
            except RuntimeError:  # yeniden kurulan panelde silinmiş düğme
                continue
            app.processEvents()
            if errors:
                print("HATA:", where, dock.windowTitle(), repr(b.text()), b.toolTip())
                return
        for cb in dock.findChildren(QComboBox):
            try:
                if cb.isVisible() and cb.isEnabled() and cb.count() > 1:
                    cb.setCurrentIndex(cb.count() - 1); app.processEvents()
                    cb.setCurrentIndex(0); app.processEvents()
            except RuntimeError:
                continue
            if errors:
                print("HATA (combo):", where, dock.windowTitle(), cb.currentText())
                return

setup()
for accept in (False, True):
    ACCEPT[0] = accept
    for kind in ("video", "audio", "text", "caption", "none"):
        select(kind)
        click_buttons(f"{kind} accept={accept}")
        if errors: break
        if not w.project.clips:
            setup()
    if errors: break
for accept in (False, True):
    if errors: break
    ACCEPT[0] = accept
    for kind in ("video", "none"):
        for a in all_actions():
            if a.text().replace("&", "") in SKIP or not a.isEnabled():
                continue
            if not w.project.clips:
                setup()
            select(kind)
            a.trigger(); app.processEvents()
            if errors:
                print("HATA (menü):", kind, accept, repr(a.text())); break
        if errors: break
ew = getattr(w, "_export_worker", None)
try:
    if ew is not None and ew.isRunning():
        ew.wait(60000)  # "Tamam" turunda başlatılan dışa aktarma bitsin
        app.processEvents()
except RuntimeError:
    pass
for e in errors:
    print(e)
w.dirty = False
w.close()
print("ERRORS", len(errors))
'''


def test_click_everything(tmp_path):
    code = f"ROOT = {ROOT!r}\nTMP = {str(tmp_path)!r}\n" + SCRIPT
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=600)
    assert "ERRORS 0" in r.stdout, r.stdout[-6000:] + r.stderr[-3000:]
