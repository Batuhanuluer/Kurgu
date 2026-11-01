"""Kurgu'yu tek tıkla çalışan bir uygulama olarak paketler (PyInstaller).

Kullanım (proje klasöründe):
    python packaging/build.py

Çıktılar dist/ klasörüne yazılır:
    Windows : Kurgu-<sürüm>-windows.zip  (+ Inno Setup kuruluysa Kurgu-<sürüm>-setup.exe)
    macOS   : Kurgu-<sürüm>-macos.zip ve Kurgu-<sürüm>-macos.dmg (içinde Kurgu.app)
    Linux   : Kurgu-<sürüm>-linux-<mimari>.tar.gz
              (+ appimagetool varsa Kurgu-<sürüm>-<mimari>.AppImage)

İsteğe bağlı imzalama (ortam değişkenleri; yoksa imzasız paketlenir):
    macOS   : MACOS_SIGN_IDENTITY="Developer ID Application: ..."; noter onayı için ayrıca
              APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD
    Windows : WINDOWS_SIGN_PFX (sertifika dosyası yolu), WINDOWS_SIGN_PASSWORD
    Linux   : APPIMAGETOOL (appimagetool yolu; PATH'te de aranır)
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
        *WHISPER_ARGS,
        # Kullanılmayan büyük Qt modülleri (boyutu küçültür)
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtPdf",
        "--exclude-module", "tkinter",
        # Kurgu'nun kullanmadığı, ortamda kuruluysa yanlışlıkla pakete giren büyük kütüphaneler
        # (pkg_resources: onnxruntime.transformers üzerinden gelir ve açılışı bozar)
        *[a for m in ("matplotlib", "pandas", "scipy", "IPython", "jupyter", "notebook",
                      "pytest", "sympy", "torch", "tensorflow", "cv2", "PIL.ImageQt",
                      "pkg_resources", "onnxruntime.transformers", "onnxruntime.tools",
                      "onnxruntime.quantization")
          for a in ("--exclude-module", m)],
    ]
    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", "org.kurgu.Kurgu"]
    print(" ".join(args))
    subprocess.check_call(args, cwd=ROOT)


def archive(ver: str) -> list[str]:
    out = []
    if sys.platform == "win32":
        sign_windows(os.path.join(DIST, "Kurgu", "Kurgu.exe"))
        z = os.path.join(DIST, f"Kurgu-{ver}-windows.zip")
        _zip_dir(os.path.join(DIST, "Kurgu"), z, "Kurgu")
        out.append(z)
        setup = inno_setup(ver)
        if setup:
            sign_windows(setup)
            out.append(setup)
    elif sys.platform == "darwin":
        app = os.path.join(DIST, "Kurgu.app")
        sign_macos(app)
        z = os.path.join(DIST, f"Kurgu-{ver}-macos.zip")
        # ditto, .app içindeki sembolik bağları ve izinleri korur
        subprocess.check_call(["ditto", "-c", "-k", "--keepParent", app, z])
        out.append(z)
        dmg = make_dmg(ver, app)
        if dmg:
            out.append(dmg)
    else:
        arch = platform.machine()
        t = os.path.join(DIST, f"Kurgu-{ver}-linux-{arch}.tar.gz")
        shutil.copy(os.path.join(ROOT, "packaging", "kurgu.desktop"),
                    os.path.join(DIST, "Kurgu", "kurgu.desktop"))
        shutil.copy(os.path.join(RES, "icon.png"), os.path.join(DIST, "Kurgu", "kurgu.png"))
        with tarfile.open(t, "w:gz") as tar:
            tar.add(os.path.join(DIST, "Kurgu"), arcname="Kurgu")
        out.append(t)
        img = appimage(ver, arch)
        if img:
            out.append(img)
    return out


# ------------------------------------------------------------ Linux: AppImage
APPRUN = """#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
exec "$HERE/usr/lib/kurgu/Kurgu" "$@"
"""


def appimage(ver: str, arch: str) -> str | None:
    """Tek dosyalık, kurulum gerektirmeyen Linux paketi (appimagetool varsa)."""
    tool = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool")
    if not tool or not os.path.exists(tool):
        print("appimagetool bulunamadı: AppImage atlandı "
              "(https://github.com/AppImage/appimagetool/releases)")
        return None
    appdir = os.path.join(BUILD, "Kurgu.AppDir")
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(os.path.join(DIST, "Kurgu"), os.path.join(appdir, "usr", "lib", "kurgu"),
                    symlinks=True)
    with open(os.path.join(appdir, "AppRun"), "w", encoding="utf-8", newline="\n") as f:
        f.write(APPRUN)
    os.chmod(os.path.join(appdir, "AppRun"), 0o755)
    desk = open(os.path.join(ROOT, "packaging", "flatpak", "org.kurgu.Kurgu.desktop"),
                encoding="utf-8").read().replace("Exec=kurgu %f", "Exec=Kurgu %f")
    with open(os.path.join(appdir, "org.kurgu.Kurgu.desktop"), "w", encoding="utf-8") as f:
        f.write(desk)
    shutil.copy(os.path.join(RES, "icon.png"), os.path.join(appdir, "org.kurgu.Kurgu.png"))
    shutil.copy(os.path.join(RES, "icon.png"), os.path.join(appdir, ".DirIcon"))
    meta = os.path.join(appdir, "usr", "share", "metainfo")
    os.makedirs(meta, exist_ok=True)
    shutil.copy(os.path.join(ROOT, "packaging", "linux", "org.kurgu.Kurgu.metainfo.xml"),
                os.path.join(meta, "org.kurgu.Kurgu.appdata.xml"))
    out = os.path.join(DIST, f"Kurgu-{ver}-{arch}.AppImage")
    env = dict(os.environ, ARCH=arch, APPIMAGE_EXTRACT_AND_RUN="1", VERSION=ver)
    try:
        subprocess.check_call([tool, "--no-appstream", appdir, out], env=env)
    except subprocess.CalledProcessError as e:
        print(f"UYARI: AppImage üretilemedi ({e})")
        return None
    return out


# ------------------------------------------------------------ macOS: imza, noter, dmg
def sign_macos(app: str):
    ident = os.environ.get("MACOS_SIGN_IDENTITY")
    if not ident:
        print("macOS imza kimliği yok: imzasız paketleniyor (Gatekeeper uyarısı görülebilir)")
        return
    ent = os.path.join(ROOT, "packaging", "macos", "entitlements.plist")
    subprocess.check_call(["codesign", "--force", "--deep", "--options", "runtime",
                           "--timestamp", "--entitlements", ent, "--sign", ident, app])
    subprocess.check_call(["codesign", "--verify", "--deep", "--strict", app])


def make_dmg(ver: str, app: str) -> str | None:
    if not shutil.which("hdiutil"):
        return None
    dmg = os.path.join(DIST, f"Kurgu-{ver}-macos.dmg")
    stage = os.path.join(BUILD, "dmg")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    subprocess.check_call(["ditto", app, os.path.join(stage, "Kurgu.app")])
    os.symlink("/Applications", os.path.join(stage, "Applications"))  # sürükle-kur
    if os.path.exists(dmg):
        os.remove(dmg)
    subprocess.check_call(["hdiutil", "create", "-volname", "Kurgu", "-srcfolder", stage,
                           "-ov", "-format", "UDZO", dmg])
    ident = os.environ.get("MACOS_SIGN_IDENTITY")
    if ident:
        subprocess.check_call(["codesign", "--force", "--timestamp", "--sign", ident, dmg])
        if all(os.environ.get(k) for k in ("APPLE_ID", "APPLE_TEAM_ID", "APPLE_APP_PASSWORD")):
            subprocess.check_call(["xcrun", "notarytool", "submit", dmg, "--apple-id",
                                   os.environ["APPLE_ID"], "--team-id",
                                   os.environ["APPLE_TEAM_ID"], "--password",
                                   os.environ["APPLE_APP_PASSWORD"], "--wait"])
            subprocess.check_call(["xcrun", "stapler", "staple", dmg])
    return dmg


# ------------------------------------------------------------ Windows: Authenticode
def sign_windows(path: str):
    pfx = os.environ.get("WINDOWS_SIGN_PFX")
    if not pfx or not os.path.exists(path):
        return
    signtool = shutil.which("signtool")
    if not signtool:
        import glob
        found = sorted(glob.glob(r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe"))
        signtool = found[-1] if found else None
    if not signtool:
        print("UYARI: signtool bulunamadı; imzalanmadı")
        return
    subprocess.check_call([signtool, "sign", "/f", pfx, "/p",
                           os.environ.get("WINDOWS_SIGN_PASSWORD", ""), "/fd", "SHA256",
                           "/tr", "http://timestamp.digicert.com", "/td", "SHA256", path])


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


def _whisper_args():
    """Konuşma tanıma (faster-whisper) kuruluysa pakete dahil et: VAD modeli ve
    CTranslate2/ONNX Runtime kütüphaneleri veri dosyası olarak gelir."""
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return []
    return ["--collect-all", "faster_whisper", "--collect-binaries", "ctranslate2",
            "--collect-binaries", "onnxruntime", "--collect-data", "tokenizers"]


WHISPER_ARGS = _whisper_args()


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
