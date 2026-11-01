"""Güvenilmeyen proje dosyalarına karşı korumalar."""
import json
import os

import pytest

from kurgu import safety
from kurgu.media import probe
from kurgu.model import Project


def test_remote_path_detection():
    for p in [r"\\evil.com\share\a.mp4", "//evil.com/share/a.mp4", r"\\evil@80\x\a.cube",
              r"\\?\UNC\evil\share\a.mp4", "file://evil/x.mp4", "smb://evil/x",
              "https://evil/x.mp4", "  \\\\evil\\x"]:
        assert safety.is_remote_path(p), p
    for p in [r"C:\Users\me\a.mp4", "/home/me/a.mp4", "a.mp4", r"..\clips\a.mp4", "", None]:
        assert not safety.is_remote_path(p), p


def _write(tmp_path, data, name="p.kurgu"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _project_with(red_video, tmp_path):
    p = Project()
    p.place_media(p.add_media(probe(red_video)), 0.0)
    return p.to_dict()


def test_project_with_network_paths_is_not_touched(tmp_path, red_video, monkeypatch):
    data = _project_with(red_video, tmp_path)
    data["media"][0]["path"] = r"\\attacker.example\share\a.mp4"
    data["media"][0]["relpath"] = r"\\attacker.example\share\b.mp4"
    data["clips"][0]["effects"] = [{"type": "lut", "enabled": True,
                                    "params": {"path": r"\\attacker.example@80\x\l.cube"}}]
    path = _write(tmp_path, data)
    touched = []
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists",
                        lambda p: touched.append(p) or real_exists(p))
    with pytest.raises(safety.RemotePathsError) as e:
        Project.load(path)
    assert len(e.value.paths) == 3
    assert not any(safety.is_remote_path(t) for t in touched)  # hiçbirine bakılmadı
    q = Project.load(path, remote="strip")
    m = next(iter(q.media.values()))
    assert m.path == "" and q.clips[0].effects[0]["params"]["path"] == ""
    assert not any(safety.is_remote_path(t) for t in touched)
    q = Project.load(path, remote="allow")  # kullanıcı onayladı
    assert next(iter(q.media.values())).path.startswith("\\\\")


def test_absolute_relpath_ignored(tmp_path, red_video):
    data = _project_with(red_video, tmp_path)
    data["media"][0]["path"] = str(tmp_path / "missing.mp4")
    data["media"][0]["relpath"] = os.path.abspath(red_video)  # göreli olmalıydı
    q = Project.load(_write(tmp_path, data))
    assert next(iter(q.media.values())).path == str(tmp_path / "missing.mp4")


def test_fake_autosave_cannot_redirect_save_path(tmp_path, red_video):
    """Kötü niyetli dosya: "autosave.original_path" ile kaydetme yolunu başka yere çeviremez."""
    data = _project_with(red_video, tmp_path)
    startup = tmp_path / "Startup" / "evil.bat"
    data["autosave"] = {"original_path": str(startup), "time": 0, "tag": "auto"}
    path = _write(tmp_path, data, "gelen-proje.kurgu")
    q = Project.load(path, autosave_dir=str(tmp_path / "autosave"))
    assert q.path == path and not q.recovered
    # Gerçek otomatik kayıt klasöründe bile .kurgu olmayan hedef kabul edilmez
    asdir = tmp_path / "autosave"
    asdir.mkdir()
    path2 = _write(asdir, data, "x--uid--1-auto.kurgu")
    q = Project.load(path2, autosave_dir=str(asdir))
    assert q.recovered and q.path is None  # -> Farklı Kaydet sorulur
    data["autosave"]["original_path"] = str(tmp_path / "Düğün.kurgu")
    q = Project.load(_write(asdir, data, "y--uid--2-auto.kurgu"), autosave_dir=str(asdir))
    assert q.recovered and q.path == str(tmp_path / "Düğün.kurgu")


def test_save_requires_kurgu_extension(tmp_path):
    p = Project()
    with pytest.raises(ValueError):
        p.save(str(tmp_path / "evil.bat"))
    assert not (tmp_path / "evil.bat").exists()
    p.save(str(tmp_path / "ok.kurgu"))


def test_non_kurgu_file_is_not_saved_back(tmp_path, red_video):
    path = _write(tmp_path, _project_with(red_video, tmp_path), "proje.json")
    assert Project.load(path).path is None


def test_lut_size_limit(tmp_path):
    from kurgu.effects import load_cube
    f = tmp_path / "big.cube"
    f.write_text("LUT_3D_SIZE 100000\n0 0 0\n")
    with pytest.raises(ValueError):
        load_cube(str(f))


def test_plain_tip_escapes_html():
    t = safety.plain_tip('<img src="\\\\evil\\x.png">')
    assert "<img" not in t and "&lt;img" in t


def test_gui_recovered_and_network_project(tmp_path, red_video):
    """Arayüz: ağ yolu sorusu varsayılan 'onlarsız aç'; kurtarılan proje hep Farklı Kaydet."""
    import subprocess
    import sys
    data = _project_with(red_video, tmp_path)
    data["media"][0]["path"] = r"\\attacker.example\share\a.mp4"
    evil = _write(tmp_path, data, "evil.kurgu")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys, os; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog
app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
asked = []
QMessageBox.exec = lambda self: asked.append(self.text()) or 0
from kurgu.ui.main_window import MainWindow
from kurgu import autosave
w = MainWindow(None)
w.open_project({evil!r})
assert asked, 'ağ yolu sorulmadı'
m = next(iter(w.project.media.values()))
assert m.path == '', m.path   # varsayılan düğme: ağ dosyaları olmadan aç
# Kurtarılan proje: Ctrl+S doğrudan yazmaz, Farklı Kaydet sorar
w.project.recovered = True
w.project.path = os.path.join({str(tmp_path)!r}, 'orig.kurgu')
dialogs = []
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (dialogs.append(a) or ('', '')))
assert w.save() is False and dialogs
assert not os.path.exists(os.path.join({str(tmp_path)!r}, 'orig.kurgu'))
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
