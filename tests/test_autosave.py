import json
import os
import subprocess
import sys
import time

from kurgu import autosave
from kurgu.media import probe
from kurgu.model import Project


def test_autosave_rotation_and_recover_path(tmp_path, red_video):
    p = Project()
    p.place_media(p.add_media(probe(red_video)), 0.0)
    p.path = str(tmp_path / "Düğün.kurgu")
    for i in range(autosave.KEEP + 3):
        autosave.write_autosave(p, folder=str(tmp_path / "as"))
        time.sleep(0.01)
    lst = autosave.list_autosaves(str(tmp_path / "as"))
    assert len(lst) == autosave.KEEP and lst[0]["uid"] == p.uid
    q = Project.load(lst[0]["path"], autosave_dir=str(tmp_path / "as"))
    assert q.recovered and q.path == p.path and q.uid == p.uid and len(q.clips) == 2


def test_crashed_session_detected(tmp_path):
    sess_dir = str(tmp_path / "s")
    # Ölü bir süreç: kısa ömürlü bir alt süreç başlatıp bitmesini bekle
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    os.makedirs(sess_dir)
    lock = os.path.join(sess_dir, f"{dead.pid}.json")
    with open(lock, "w") as f:
        json.dump({"pid": dead.pid, "uid": "abc", "project_path": None,
                   "last_autosave": None}, f)
    me = autosave.Session(sess_dir)
    me.start()
    crashed = me.crashed_sessions()
    assert [c["pid"] for c in crashed] == [dead.pid]  # kendi oturumumuz sayılmaz
    autosave.Session.discard(crashed[0])
    me.end()
    assert os.listdir(sess_dir) == []


def test_recovery_skips_when_original_is_newer(tmp_path, red_video):
    p = Project()
    p.path = str(tmp_path / "x.kurgu")
    a = autosave.write_autosave(p, folder=str(tmp_path / "as"))
    time.sleep(0.02)
    p.save(p.path)  # kullanıcı sonradan kaydetmiş
    info = {"uid": p.uid, "project_path": p.path, "last_autosave": a}
    assert autosave.recovery_candidate(info, str(tmp_path / "as")) is None
    os.utime(p.path, (time.time() - 100, time.time() - 100))
    assert autosave.recovery_candidate(info, str(tmp_path / "as"))["path"] == a


def test_main_window_opens_project_from_command_line(tmp_path, red_video):
    """.kurgu dosyasına çift tıklama: proje kurucuda açılır (oturum hazır olmalı).
    Pencere testi ayrı süreçte: diğer testler pencere içermeyen bir Qt uygulaması açıyor."""
    p = Project()
    p.place_media(p.add_media(probe(red_video)), 0.0)
    path = str(tmp_path / "x.kurgu")
    p.save(path)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from PySide6.QtWidgets import QApplication; app = QApplication([])\n"
        "import kurgu.ui.player as pl; pl.HAVE_AUDIO = False\n"
        "from kurgu.ui.main_window import MainWindow\n"
        "w = MainWindow(%r)\n"
        "assert len(w.project.clips) == 2 and w.project.path == %r\n"
        "w.dirty = False; w.close(); print('OK')\n" % (root, path, path))
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=120)
    assert "OK" in r.stdout, r.stderr[-2000:]
