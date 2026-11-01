"""Paketlenmiş Kurgu'nun gerçekten çalıştığını denetler (CI'da paketlemeden sonra).

    python packaging/smoke_test.py

1) --version ile açılır, 2) renk düzeltmesi, maske ve altyazı içeren küçük bir projeyi
komut satırından dışa aktarır ve çıktıyı doğrular. Eksik modül (ör. PyInstaller'ın
kaçırdığı bir bağımlılık) yayından önce yakalanır.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))


def exe() -> str:
    d = os.path.join(ROOT, "dist")
    if sys.platform == "win32":
        return os.path.join(d, "Kurgu", "Kurgu.exe")
    if sys.platform == "darwin":
        return os.path.join(d, "Kurgu.app", "Contents", "MacOS", "Kurgu")
    return os.path.join(d, "Kurgu", "Kurgu")


def main():
    import av
    from conftest import make_video
    from kurgu import effects as fx
    from kurgu import masks as mk
    from kurgu.media import probe
    from kurgu.model import Project
    tmp = tempfile.mkdtemp(prefix="kurgu-smoke-")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=tmp, USERPROFILE=tmp)
    r = subprocess.run([exe(), "--version"], env=env, capture_output=True, text=True,
                       timeout=180)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit("Paket açılmadı (--version)")
    print("Sürüm:", (r.stdout or "").strip() or "(pencereli uygulama: çıktı yok)")
    video = make_video(os.path.join(tmp, "a.mp4"), seconds=1, w=320, h=180)
    p = Project(320, 180, 25.0)
    made = p.place_media(p.add_media(probe(video)), 0.0)
    clip = [c for c in made if p.track_is_video(c.track)][0]
    e = fx.new_effect("lumetri")
    e["params"]["exposure"] = 0.5
    clip.effects.append(e)
    clip.masks = [mk.new_mask("ellipse")]
    p.add_captions([(0.1, 0.9, "Merhaba")])
    proj = os.path.join(tmp, "p.kurgu")
    p.save(proj)
    out = os.path.join(tmp, "out.mp4")
    r = subprocess.run([exe(), "--export", out, proj], env=env, capture_output=True,
                       text=True, timeout=600)
    if r.returncode != 0 or not os.path.exists(out):
        print(r.stdout, r.stderr)
        raise SystemExit("Paketle dışa aktarma başarısız")
    with av.open(out) as c:
        n = sum(1 for _ in c.decode(video=0))
    if n < 20:
        raise SystemExit(f"Dışa aktarılan videoda kare eksik ({n})")
    print(f"Tamam: {n} kare dışa aktarıldı")


if __name__ == "__main__":
    main()
