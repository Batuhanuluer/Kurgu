"""Güncelleme denetimi: GitHub Releases'taki son sürümü kontrol eder.

Gizlilik: yalnızca herkese açık GitHub API'sine sürüm sorgusu yapılır, kişisel veri
gönderilmez. Yardım menüsünden kapatılabilir.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.request

REPO = "Batuhanuluer/Kurgu"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"


def parse_version(v: str) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", v or "")[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def fetch_latest(timeout: float = 6.0) -> dict | None:
    """{"version", "url", "notes", "setup_url"} ya da yayın yoksa None. Ağ hatasında istisna."""
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "Kurgu-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:  # henüz yayın yok
            return None
        raise
    setup = next((a["browser_download_url"] for a in data.get("assets", [])
                  if a.get("name", "").endswith("-setup.exe")), None)
    return {"version": data.get("tag_name", "").lstrip("v"),
            "url": data.get("html_url") or RELEASES_PAGE,
            "notes": data.get("body") or "", "setup_url": setup}


def can_self_update(info: dict) -> bool:
    """Windows'ta kurulum programıyla kurulmuş paket sürüm: indirip kendini güncelleyebilir."""
    return sys.platform == "win32" and getattr(sys, "frozen", False) and bool(info.get("setup_url"))


def download(url: str, progress=None, cancelled=None) -> str:
    dest = os.path.join(tempfile.gettempdir(), os.path.basename(url.split("?")[0]))
    req = urllib.request.Request(url, headers={"User-Agent": "Kurgu-updater"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done, total)
    os.replace(dest + ".part", dest)
    return dest


def run_installer(path: str):
    """Inno Setup kurulumunu sessiz başlat; Kurgu kapanınca üzerine kurar ve yeniden açar."""
    import subprocess
    subprocess.Popen([path, "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
                     close_fds=True)
