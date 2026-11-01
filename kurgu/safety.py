"""Güvenlik yardımcıları: güvenilmeyen proje dosyalarından gelen yolların denetimi.

Bir .kurgu dosyası başkasından gelebilir. İçindeki yollar Windows'ta \\\\sunucu\\paylaşım
biçiminde (UNC) olursa, dosyaya yalnızca bakmak (os.path.exists) bile Windows'un o sunucuya
kullanıcının NTLM kimliğiyle bağlanmasına yol açar. Bu yüzden ağ yolları kullanıcı onay
vermeden hiçbir şekilde açılmaz ya da yoklanmaz.
"""
from __future__ import annotations

import html
import os
import re

PROJECT_EXT = ".kurgu"
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def is_remote_path(path) -> bool:
    """UNC (\\\\host\\share, //host/share, \\\\?\\UNC\\…, WebDAV \\\\host@80\\…) ya da URL mi?"""
    if not isinstance(path, str) or not path:
        return False
    p = path.strip().replace("\\", "/")
    return p.startswith("//") or bool(_URL.match(p))


def _effect_paths(effects):
    for fx in effects or []:
        params = fx.get("params") if isinstance(fx, dict) else None
        if isinstance(params, dict):
            for k, v in params.items():
                if isinstance(v, str):
                    yield params, k


def remote_paths(data: dict) -> list[str]:
    """Proje verisindeki tüm ağ/uzak yolları listeler (dosya sistemine dokunmadan)."""
    found = []
    for m in data.get("media", []) or []:
        if isinstance(m, dict):
            for k in ("path", "relpath", "proxy"):
                if is_remote_path(m.get(k)):
                    found.append(m[k])
    for c in data.get("clips", []) or []:
        if isinstance(c, dict):
            for params, k in _effect_paths(c.get("effects")):
                if is_remote_path(params[k]):
                    found.append(params[k])
    return list(dict.fromkeys(found))


def strip_remote_paths(data: dict) -> None:
    """Ağ yollarını boşaltır: medya "eksik" görünür, LUT efekti devre dışı kalır."""
    for m in data.get("media", []) or []:
        if isinstance(m, dict):
            for k in ("path", "relpath", "proxy"):
                if is_remote_path(m.get(k)):
                    m[k] = ""
    for c in data.get("clips", []) or []:
        if isinstance(c, dict):
            for params, k in list(_effect_paths(c.get("effects"))):
                if is_remote_path(params[k]):
                    params[k] = ""


def is_inside(path: str, folder: str) -> bool:
    try:
        p = os.path.normcase(os.path.realpath(path))
        f = os.path.normcase(os.path.realpath(folder))
        return os.path.commonpath([p, f]) == f
    except (ValueError, OSError):
        return False


def valid_project_target(path) -> bool:
    """Kaydetme hedefi: yerel, mutlak ve .kurgu uzantılı olmalı."""
    return (isinstance(path, str) and path.lower().endswith(PROJECT_EXT)
            and os.path.isabs(path) and not is_remote_path(path))


def plain(text) -> str:
    """Qt'nin zengin metin (HTML) olarak yorumlayabileceği kutularda güvenilmeyen metin için."""
    return html.escape(str(text), quote=False)


def plain_tip(text) -> str:
    """Araç ipucu: Qt ipuçlarını HTML olarak yorumlayabilir; içerik kaçışlı gösterilir."""
    if not text:
        return ""
    return "<p style='white-space:pre'>" + html.escape(str(text)) + "</p>"


class RemotePathsError(Exception):
    def __init__(self, paths):
        super().__init__("network paths in project")
        self.paths = paths
