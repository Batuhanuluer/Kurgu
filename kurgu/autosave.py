"""Otomatik kaydetme ve çökme kurtarma.

- Proje belirli aralıklarla ~/.kurgu/autosave/ klasörüne yedeklenir (proje başına son 5 kopya).
- Her çalışan Kurgu bir oturum kilidi (~/.kurgu/sessions/<pid>.json) tutar; düzgün
  kapanışta silinir. Açılışta sahibi artık çalışmayan bir kilit bulunursa program
  çökmüş demektir ve son otomatik kayıt kurtarma için önerilir.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

APP_DIR = os.path.join(os.path.expanduser("~"), ".kurgu")
AUTOSAVE_DIR = os.path.join(APP_DIR, "autosave")
SESSION_DIR = os.path.join(APP_DIR, "sessions")
KEEP = 5


def _safe(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip() or "Untitled"
    return name[:60]


def _atomic_write(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# ------------------------------------------------------------ otomatik kayıt
def write_autosave(project, tag: str = "auto", folder: str | None = None) -> str:
    """Projenin bir yedeğini yazar ve aynı projenin eski yedeklerinden KEEP tanesini tutar."""
    folder = folder or AUTOSAVE_DIR
    base = _safe(os.path.splitext(os.path.basename(project.path))[0]) if project.path \
        else "Untitled"
    now = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"{int(now * 1000) % 1000:03d}"
    path = os.path.join(folder, f"{base}--{project.uid}--{stamp}-{tag}.kurgu")
    data = project.to_dict()
    data["autosave"] = {"original_path": project.path, "time": time.time(), "tag": tag}
    _atomic_write(path, data)
    old = [e for e in list_autosaves(folder) if e["uid"] == project.uid]
    for e in old[KEEP:]:
        try:
            os.remove(e["path"])
        except OSError:
            pass
    return path


def list_autosaves(folder: str | None = None) -> list[dict]:
    """En yeniden eskiye otomatik kayıtlar."""
    folder = folder or AUTOSAVE_DIR
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".kurgu"):
            continue
        parts = n[:-6].split("--")
        if len(parts) < 3:
            continue
        p = os.path.join(folder, n)
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        out.append({"path": p, "name": parts[0], "uid": parts[1], "mtime": mtime})
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


# ------------------------------------------------------------ oturum kilitleri
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # Windows'ta os.kill(pid, 0) süreci SONLANDIRIR; bu yüzden Win32 API kullanılır.
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Session:
    """Bu sürecin oturum kilidi."""

    def __init__(self, folder: str | None = None):
        self.folder = folder or SESSION_DIR
        self.path = os.path.join(self.folder, f"{os.getpid()}.json")
        self.info = {"pid": os.getpid(), "started": time.time(), "uid": None,
                     "project_path": None, "last_autosave": None}

    def crashed_sessions(self) -> list[dict]:
        """Sahibi artık çalışmayan (düzgün kapanmamış) oturumlar."""
        out = []
        try:
            names = os.listdir(self.folder)
        except OSError:
            return out
        for n in names:
            if not n.endswith(".json"):
                continue
            p = os.path.join(self.folder, n)
            try:
                with open(p, encoding="utf-8") as f:
                    info = json.load(f)
            except (OSError, ValueError):
                info = {"pid": -1}
            pid = int(info.get("pid", -1))
            if pid == os.getpid() or _pid_alive(pid):
                continue
            info["_lock"] = p
            out.append(info)
        return out

    def start(self):
        _atomic_write(self.path, self.info)

    def update(self, **kw):
        self.info.update(kw)
        try:
            _atomic_write(self.path, self.info)
        except OSError:
            pass

    def end(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    @staticmethod
    def discard(info: dict):
        try:
            os.remove(info["_lock"])
        except (OSError, KeyError):
            pass


def recovery_candidate(info: dict, folder: str | None = None) -> dict | None:
    """Çöken oturum için açılabilecek en yeni otomatik kayıt (asıl dosyadan daha yeniyse)."""
    path = info.get("last_autosave")
    if not path or not os.path.exists(path):
        uid = info.get("uid")
        cands = [e for e in list_autosaves(folder) if uid and e["uid"] == uid]
        if not cands:
            return None
        path = cands[0]["path"]
    mtime = os.path.getmtime(path)
    orig = info.get("project_path")
    if orig and os.path.exists(orig) and os.path.getmtime(orig) >= mtime:
        return None  # asıl proje zaten daha yeni kaydedilmiş
    return {"path": path, "mtime": mtime, "original": orig}
