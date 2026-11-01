"""Kurgu'yu tek tıkla çalışan bir uygulama olarak paketler (PyInstaller).

Kullanım (proje klasöründe):
    python packaging/build.py

Çıktılar dist/ klasörüne yazılır:
    Windows : Kurgu-<sürüm>-windows.zip  (+ Inno Setup kuruluysa Kurgu-<sürüm>-setup.exe)
    macOS   : Kurgu-<sürüm>-macos.zip    (içinde Kurgu.app)
    Linux   : Kurgu-<sürüm>-linux.tar.gz (içinde Kurgu/Kurgu çalıştırılabilir dosyası)
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile

# Windows CI/konsolda çıktı kodlaması cp1252 olabilir: Türkçe karakterler çökmesin
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "kurgu", "resources")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")


def version() -> str:
    sys.path.insert(0, ROOT)
    from kurgu import __version__
    return __version__


def ensure_icons():
    need = ["icon.png"] + (["icon.ico"] if sys.platform == "win32" else []) + \
        (["icon.icns"] if sys.platform == "darwin" else [])
    if not all(os.path.exists(os.path.join(RES, n)) for n in need):
        subprocess.check_call([sys.executable, os.path.join(ROOT, "packaging", "make_icon.py")])


def pyinstaller():
    icon = {"win32": "icon.ico", "darwin": "icon.icns"}.get(sys.platform, "icon.png")
    args = [
        sys.executable, "-m", "PyInstaller",
        os.path.join(ROOT, "packaging", "kurgu_launcher.py"),
        "--name", "Kurgu",
        "--windowed",  # konsol penceresi açılmasın
        "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", BUILD, "--specpath", BUILD,
        "--icon", os.path.join(RES, icon),
        "--add-data", f"{RES}{os.pathsep}kurgu/resources",
        "--paths", ROOT,
        # PyAV (FFmpeg) kütüphaneleri ve alt modülleri
        "--collect-submodules", "av",
        "--collect-binaries", "av",
        "--hidden-import", "kurgu.ui.main_window",
        # Güncelleme imzası doğrulaması (updater içinde tembel içe aktarılıyor)
        "--hidden-import", "cryptography.hazmat.primitives.asymmetric.ed25519",
        # Kullanılmayan büyük Qt modülleri (boyutu küçültür)
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtPdf",
        "--exclude-module", "tkinter",
    ]
    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", "org.kurgu.Kurgu"]
    print(" ".join(args))
    subprocess.check_call(args, cwd=ROOT)


def archive(ver: str) -> list[str]:
    out = []
    if sys.platform == "win32":
        z = os.path.join(DIST, f"Kurgu-{ver}-windows.zip")
        _zip_dir(os.path.join(DIST, "Kurgu"), z, "Kurgu")
        out.append(z)
        setup = inno_setup(ver)
        if setup:
            out.append(setup)
    elif sys.platform == "darwin":
        z = os.path.join(DIST, f"Kurgu-{ver}-macos.zip")
        # ditto, .app içindeki sembolik bağları ve izinleri korur
        subprocess.check_call(["ditto", "-c", "-k", "--keepParent",
                               os.path.join(DIST, "Kurgu.app"), z])
        out.append(z)
    else:
        t = os.path.join(DIST, f"Kurgu-{ver}-linux-{platform.machine()}.tar.gz")
        shutil.copy(os.path.join(ROOT, "packaging", "kurgu.desktop"),
                    os.path.join(DIST, "Kurgu", "kurgu.desktop"))
        shutil.copy(os.path.join(RES, "icon.png"), os.path.join(DIST, "Kurgu", "kurgu.png"))
        with tarfile.open(t, "w:gz") as tar:
            tar.add(os.path.join(DIST, "Kurgu"), arcname="Kurgu")
        out.append(t)
    return out


def _zip_dir(src, dest, arc_root):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(src):
            for f in files:
                full = os.path.join(base, f)
                z.write(full, os.path.join(arc_root, os.path.relpath(full, src)))


def inno_setup(ver: str) -> str | None:
    """Windows kurulum programı (Inno Setup 6 kuruluysa)."""
    candidates = [shutil.which("iscc"),
                  r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                  r"C:\Program Files\Inno Setup 6\ISCC.exe"]
    iscc = next((c for c in candidates if c and os.path.exists(c)), None)
    if not iscc:
        print("Inno Setup bulunamadı: yalnızca zip üretildi "
              "(kurulum programı için https://jrsoftware.org/isdl.php)")
        return None
    iss = os.path.join(ROOT, "packaging", "kurgu.iss")
    try:
        subprocess.check_call([iscc, f"/DMyAppVersion={ver}", f"/DSourceDir={DIST}\\Kurgu",
                               f"/DOutDir={DIST}", f"/DIconFile={RES}\\icon.ico", iss])
    except subprocess.CalledProcessError as e:
        # Kurulum programı üretilemese de taşınabilir zip yayınlansın
        print(f"UYARI: Inno Setup başarısız oldu ({e}); yalnızca zip üretildi.")
        return None
    return os.path.join(DIST, f"Kurgu-{ver}-setup.exe")


def main():
    ver = version()
    print(f"Kurgu {ver} paketleniyor ({sys.platform})…")
    ensure_icons()
    pyinstaller()
    files = archive(ver)
    print("\nHazır:")
    for f in files:
        print("  ", f, f"({os.path.getsize(f) / 2**20:.0f} MB)")


if __name__ == "__main__":
    main()
