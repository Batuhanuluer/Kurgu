"""Hata bildirme: sistem bilgisi + günlük ile GitHub'da hazır doldurulmuş bir hata kaydı.

Gizlilik: hiçbir şey otomatik gönderilmez. Kullanıcı metni görür, düzenler ve tarayıcıda
kendisi gönderir. Ev klasörü yolu ve kullanıcı adı "~" / "<kullanıcı>" ile gizlenir.
"""
from __future__ import annotations

import getpass
import os
import platform
import re
import sys
import urllib.parse

REPO = "Batuhanuluer/Kurgu"
NEW_ISSUE = f"https://github.com/{REPO}/issues/new"
MAX_URL = 7000  # tarayıcı/GitHub URL sınırına takılmasın


def redact(text: str) -> str:
    """Kişisel yolları ve kullanıcı adını gizler."""
    home = os.path.expanduser("~")
    out = text
    for h in {home, home.replace("\\", "/"), os.path.realpath(home)}:
        if h and len(h) > 2:
            out = out.replace(h, "~")
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if user and len(user) > 2:
        out = re.sub(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", "<kullanıcı>", out)
    # E-posta adresleri
    out = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<e-posta>", out)
    return out


def system_info() -> dict:
    from . import __version__
    info = {"Kurgu": __version__,
            "OS": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "Python": platform.python_version(),
            "Packaged": "yes" if getattr(sys, "frozen", False) else "no (source)"}
    try:
        import PySide6
        info["Qt / PySide6"] = PySide6.__version__
    except Exception:
        pass
    try:
        import av
        info["PyAV / FFmpeg"] = f"{av.__version__} / " + ".".join(
            str(x) for x in av.library_versions.get("libavcodec", ())[:3])
    except Exception:
        pass
    try:
        from . import hw
        s = hw.decoder_types()
        info["GPU decoders"] = ", ".join(s) or "-"
    except Exception:
        pass
    return info


def log_tail(path: str, lines: int = 60) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = f.readlines()[-lines:]
    except OSError:
        return ""
    return "".join(data)


def build_body(description: str, error: str = "", log: str = "") -> str:
    info = system_info()
    parts = ["## Ne oldu? / What happened?", description.strip() or "_(açıklama)_", "",
             "## Sistem / System", *(f"- **{k}:** {v}" for k, v in info.items()), ""]
    if error.strip():
        parts += ["## Hata / Error", "```", error.strip(), "```", ""]
    if log.strip():
        parts += ["<details><summary>Günlük / Log</summary>", "", "```", log.strip(), "```",
                  "", "</details>"]
    return redact("\n".join(parts))


def issue_url(title: str, body: str) -> str:
    """Hazır doldurulmuş 'yeni hata kaydı' bağlantısı (uzunsa gövde kısaltılır)."""
    def make(b):
        q = urllib.parse.urlencode({"title": title, "body": b, "labels": "bug"})
        return f"{NEW_ISSUE}?{q}"
    url = make(body)
    if len(url) <= MAX_URL:
        return url
    note = "\n\n_(Günlüğün tamamı sığmadı; panodaki metni yapıştırabilirsiniz.)_"
    lo, hi = 0, len(body)
    while lo < hi:  # sığan en uzun önek
        mid = (lo + hi + 1) // 2
        if len(make(body[:mid] + note)) <= MAX_URL:
            lo = mid
        else:
            hi = mid - 1
    return make(body[:lo] + note)
