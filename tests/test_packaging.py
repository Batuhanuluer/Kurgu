"""Dağıtım: AppImage klasör yapısı, Flatpak manifesti, AppStream ve masaüstü dosyaları."""
import importlib.util
import os
import stat
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _build_module():
    spec = importlib.util.spec_from_file_location("kbuild", os.path.join(ROOT, "packaging",
                                                                         "build.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.skipif(sys.platform == "win32", reason="AppImage yalnız Linux")
def test_appimage_layout(tmp_path, monkeypatch):
    b = _build_module()
    dist, build = tmp_path / "dist", tmp_path / "build"
    (dist / "Kurgu").mkdir(parents=True)
    (dist / "Kurgu" / "Kurgu").write_text("#!/bin/sh\necho kurgu\n")
    monkeypatch.setattr(b, "DIST", str(dist))
    monkeypatch.setattr(b, "BUILD", str(build))
    # Sahte appimagetool: aldığı AppDir'i kaydeder, çıktı dosyası oluşturur
    tool = tmp_path / "fake-appimagetool"
    log = tmp_path / "args.txt"
    tool.write_text(f"#!/bin/sh\necho \"$@\" > {log}\ntouch \"$3\"\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("APPIMAGETOOL", str(tool))
    out = b.appimage("9.9.9", "x86_64")
    assert out and out.endswith("Kurgu-9.9.9-x86_64.AppImage") and os.path.exists(out)
    appdir = build / "Kurgu.AppDir"
    assert os.access(appdir / "AppRun", os.X_OK)
    assert "usr/lib/kurgu/Kurgu" in (appdir / "AppRun").read_text()
    assert (appdir / "usr" / "lib" / "kurgu" / "Kurgu").exists()
    desk = (appdir / "org.kurgu.Kurgu.desktop").read_text()
    assert "Exec=Kurgu %f" in desk and "Icon=org.kurgu.Kurgu" in desk
    assert (appdir / "org.kurgu.Kurgu.png").exists() and (appdir / ".DirIcon").exists()
    assert (appdir / "usr/share/metainfo/org.kurgu.Kurgu.appdata.xml").exists()
    assert str(appdir) in log.read_text()


def test_appimage_skipped_without_tool(tmp_path, monkeypatch):
    b = _build_module()
    monkeypatch.delenv("APPIMAGETOOL", raising=False)
    monkeypatch.setattr(b.shutil, "which", lambda name: None)
    assert b.appimage("1.0", "x86_64") is None


def test_metainfo_and_desktop_files():
    root = ET.parse(os.path.join(ROOT, "packaging", "linux",
                                 "org.kurgu.Kurgu.metainfo.xml")).getroot()
    assert root.tag == "component" and root.findtext("id") == "org.kurgu.Kurgu"
    assert root.find("launchable").text == "org.kurgu.Kurgu.desktop"
    from kurgu import __version__
    versions = [r.get("version") for r in root.find("releases")]
    assert __version__ in versions, "metainfo sürüm listesine yeni sürümü ekle"
    desk = open(os.path.join(ROOT, "packaging", "flatpak", "org.kurgu.Kurgu.desktop"),
                encoding="utf-8").read()
    for key in ("Type=Application", "Exec=kurgu", "Icon=org.kurgu.Kurgu", "Categories="):
        assert key in desk


def test_flatpak_manifest():
    yaml = pytest.importorskip("yaml")
    m = yaml.safe_load(open(os.path.join(ROOT, "packaging", "flatpak", "org.kurgu.Kurgu.yml"),
                            encoding="utf-8"))
    assert m["app-id"] == "org.kurgu.Kurgu" and m["command"] == "kurgu"
    assert "--device=dri" in m["finish-args"]
    srcs = m["modules"][0]["sources"]
    arch = next(s for s in srcs if s["type"] == "archive")
    assert arch["url"].startswith("https://github.com/Batuhanuluer/Kurgu/releases/download/")
    assert len(arch["sha256"]) == 64
    for s in srcs:
        if s["type"] == "file":
            assert os.path.exists(os.path.join(ROOT, "packaging", "flatpak", s["path"]))


def test_macos_plist_patch(tmp_path):
    import plistlib
    sys.path.insert(0, os.path.join(ROOT, "packaging"))
    import build
    app = tmp_path / "Kurgu.app" / "Contents"
    app.mkdir(parents=True)
    with open(app / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "org.kurgu.Kurgu"}, f)
    build.patch_macos_plist(str(tmp_path / "Kurgu.app"), "1.2.3")
    with open(app / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    assert info["NSMicrophoneUsageDescription"] and info["CFBundleShortVersionString"] == "1.2.3"
    ent = open(os.path.join(ROOT, "packaging", "macos", "entitlements.plist")).read()
    assert "audio-input</key>\n    <true/>" in ent
