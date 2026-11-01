"""Güncelleme denetimi: GitHub Releases'taki son sürümü kontrol eder.

Gizlilik: yalnızca herkese açık GitHub API'sine sürüm sorgusu yapılır, kişisel veri
gönderilmez. Yardım menüsünden kapatılabilir.

Güvenlik: Windows'ta kendini güncelleme (kurulumu indirip sessizce çalıştırma) YALNIZCA
sürümde geliştiricinin çevrim dışı anahtarıyla imzalanmış bir SHA256SUMS.txt varsa yapılır.
İmza, programın içine gömülü açık anahtarla (PUBLIC_KEYS) doğrulanır; indirilen kurulumun
SHA-256 özeti bu listedekiyle (ve GitHub'ın verdiği özetle) eşleşmezse dosya silinir ve
çalıştırılmaz. Böylece yalnızca GitHub hesabı ele geçirilse bile kullanıcılara sessizce
kod çalıştırılamaz. İmza yoksa kullanıcı indirme sayfasına yönlendirilir.
İmzalama: packaging/make_signing_key.py ve packaging/sign_release.py.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request

REPO = "Batuhanuluer/Kurgu"
SUMS_NAME = "SHA256SUMS.txt"
SIG_NAME = "SHA256SUMS.txt.sig"
# Ed25519 açık anahtar(lar)ı, base64 (32 bayt). packaging/make_signing_key.py üretir.
# Boşsa kendini güncelleme kapalıdır (kullanıcı indirme sayfasına yönlendirilir).
PUBLIC_KEYS: tuple[str, ...] = ()
# Kurulum dosyası yalnızca bu adreslerden indirilir (HTTPS)
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")


class UpdateVerificationError(Exception):
    pass
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
    return parse_release(data)


def parse_release(data: dict) -> dict:
    assets = {a.get("name", ""): a for a in data.get("assets", []) if isinstance(a, dict)}
    setup = next((a for n, a in assets.items() if n.endswith("-setup.exe")), None)

    def url(a):
        return a.get("browser_download_url") if a else None
    return {"version": str(data.get("tag_name", "")).lstrip("v"),
            "url": data.get("html_url") or RELEASES_PAGE,
            "notes": data.get("body") or "",
            "setup_url": url(setup),
            "setup_name": setup.get("name") if setup else None,
            # GitHub'ın kendi hesapladığı özet ("sha256:..."), varsa
            "setup_digest": (setup.get("digest") or "") if setup else "",
            "sums_url": url(assets.get(SUMS_NAME)),
            "sig_url": url(assets.get(SIG_NAME))}


def allowed_url(url) -> bool:
    try:
        u = urllib.parse.urlparse(url or "")
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "").lower() in ALLOWED_HOSTS


def have_crypto() -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa
        return True
    except Exception:
        return False


def can_self_update(info: dict) -> bool:
    """Windows'ta kurulum programıyla kurulmuş paket sürüm, ve sürüm imzalıysa: indirip
    kendini güncelleyebilir. İmzasız sürümlerde kullanıcı indirme sayfasına gider."""
    return (sys.platform == "win32" and getattr(sys, "frozen", False)
            and bool(PUBLIC_KEYS) and have_crypto()
            and all(allowed_url(info.get(k)) for k in ("setup_url", "sums_url", "sig_url")))


def verify_signature(data: bytes, sig_b64: str, keys=None) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        sig = base64.b64decode(sig_b64.strip(), validate=True)
    except Exception:
        return False
    for k in (PUBLIC_KEYS if keys is None else keys):
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(k)).verify(sig, data)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def parse_sums(text: str) -> dict:
    """'<sha256>  <dosya adı>' satırları -> {ad: özet}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(url: str, limit: int = 1 << 16) -> bytes:
    if not allowed_url(url):
        raise UpdateVerificationError(f"İzin verilmeyen adres: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Kurgu-updater"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UpdateVerificationError("Beklenenden büyük dosya")
    return data


def expected_hash(info: dict, keys=None, fetch=_get) -> str:
    """İmzalı SHA256SUMS.txt'yi indirir, imzayı doğrular, kurulumun özetini döndürür."""
    sums = fetch(info["sums_url"])
    sig = fetch(info["sig_url"]).decode("ascii", "replace")
    if not verify_signature(sums, sig, keys):
        raise UpdateVerificationError("Güncelleme imzası doğrulanamadı")
    h = parse_sums(sums.decode("utf-8", "replace")).get(info.get("setup_name") or "")
    if not h:
        raise UpdateVerificationError("Kurulum dosyası imzalı listede yok")
    gh = (info.get("setup_digest") or "").lower()
    if gh.startswith("sha256:") and gh[7:] != h:
        raise UpdateVerificationError("GitHub özeti imzalı listeyle uyuşmuyor")
    return h


def download_verified(info: dict, progress=None, cancelled=None, keys=None,
                      fetch=_get) -> str:
    """Kurulumu indirir ve imzalı özetle doğrular; uyuşmazsa siler ve hata verir."""
    want = expected_hash(info, keys, fetch)
    path = download(info["setup_url"], progress, cancelled)
    got = sha256_file(path)
    if got != want:
        try:
            os.remove(path)
        except OSError:
            pass
        raise UpdateVerificationError("İndirilen kurulum dosyası doğrulanamadı (özet uyuşmuyor)")
    return path


def download(url: str, progress=None, cancelled=None) -> str:
    if not allowed_url(url):
        raise UpdateVerificationError(f"İzin verilmeyen adres: {url}")
    name = os.path.basename(urllib.parse.urlparse(url).path) or "Kurgu-setup.exe"
    name = re.sub(r"[^\w.\-]", "_", name)
    folder = tempfile.mkdtemp(prefix="kurgu-update-")  # yalnızca bu kullanıcıya ait klasör
    dest = os.path.join(folder, name)
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
