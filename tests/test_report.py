"""Hata bildirme: kişisel bilgilerin gizlenmesi, GitHub bağlantısı, çökme penceresi."""
import getpass
import os
import urllib.parse

from kurgu import report


def test_redacts_personal_paths():
    home = os.path.expanduser("~")
    txt = f'File "{home}/Videos/düğün.mp4" by ali@example.com'
    out = report.redact(txt)
    assert home not in out and "~/Videos" in out and "<e-posta>" in out
    user = getpass.getuser()
    if len(user) > 2:
        assert user not in report.redact(f"C:\\Users\\{user}\\x")


def test_body_and_url():
    body = report.build_body("Dışa aktarma takıldı", "Traceback...\nValueError: x",
                             "log satırı\n" * 5)
    assert "Kurgu" in body and "ValueError: x" in body and "Sistem" in body
    url = report.issue_url("Başlık", body)
    assert url.startswith("https://github.com/Batuhanuluer/Kurgu/issues/new?")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["title"] == ["Başlık"] and "ValueError: x" in q["body"][0] and q["labels"] == ["bug"]
    long = report.issue_url("T", "x" * 50000)
    assert len(long) <= report.MAX_URL
    assert "sığmadı" in urllib.parse.parse_qs(urllib.parse.urlparse(long).query)["body"][0]


def test_report_dialog_and_crash_box(tmp_path):
    """Pencere testleri ayrı süreçte (diğer testler pencere içermeyen Qt uygulaması açar)."""
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys, os; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication, QMessageBox; app = QApplication([])
from kurgu.ui import report_dialog as rd
opened = []
rd.QDesktopServices.openUrl = staticmethod(lambda u: opened.append(u.toString()))
d = rd.ReportDialog(None, "Traceback" + chr(10) + "KeyError: 'x'")
d.desc.setPlainText("Altyazı düğmesine bastım")
assert "Altyazı düğmesine bastım" in d.body.toPlainText()
assert d.title.text() == "KeyError: 'x'", d.title.text()
d.open_github()
assert opened and "issues/new" in opened[0]
import kurgu.app as kapp
kapp.LOG_PATH = {str(tmp_path / 'k.log')!r}
shown = []
def fake_exec(box):
    btn = [b for b in box.buttons() if "…" in b.text()][0]
    shown.append(box.text()); btn.click(); return 0
QMessageBox.exec = fake_exec
rd.ReportDialog.exec = lambda self: shown.append(self.error) or 0
try:
    raise ValueError("deneme")
except ValueError as e:
    kapp._excepthook(type(e), e, e.__traceback__)
assert "ValueError: deneme" in shown[0] and "ValueError: deneme" in shown[1], shown
assert os.path.exists({str(tmp_path / 'k.log')!r})
print("OK")
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=120)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
