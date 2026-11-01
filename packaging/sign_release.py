"""Bir sürümün dosyalarını imzalar ve SHA256SUMS.txt + .sig olarak sürüme yükler.

GitHub Actions paketleri yayınladıktan SONRA, kendi bilgisayarında çalıştır:

    python packaging/sign_release.py v0.8.1

Gereken: GitHub CLI (gh, `gh auth login` yapılmış) ve make_signing_key.py ile üretilmiş
anahtar. Anahtar yalnızca senin bilgisayarında durduğu için, GitHub hesabı ele geçirilse
bile kullanıcılara sahte bir güncelleme sessizce kurdurulamaz.

Yerel bir klasördeki dosyaları imzalamak için (yüklemeden):
    python packaging/sign_release.py --dir dist
"""
import argparse
import base64
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_signing_key import KEY_PATH, load_private  # noqa: E402

REPO = "Batuhanuluer/Kurgu"
SUMS, SIG = "SHA256SUMS.txt", "SHA256SUMS.txt.sig"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sign_dir(folder, key_path=KEY_PATH):
    names = sorted(n for n in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, n)) and n not in (SUMS, SIG))
    if not names:
        raise SystemExit("İmzalanacak dosya yok")
    text = "".join(f"{sha256(os.path.join(folder, n))}  {n}\n" for n in names)
    data = text.encode()
    sig = base64.b64encode(load_private(key_path).sign(data)).decode() + "\n"
    with open(os.path.join(folder, SUMS), "wb") as f:
        f.write(data)
    with open(os.path.join(folder, SIG), "w") as f:
        f.write(sig)
    print(text)
    return [os.path.join(folder, SUMS), os.path.join(folder, SIG)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", nargs="?")
    ap.add_argument("--dir")
    ap.add_argument("--key", default=KEY_PATH)
    a = ap.parse_args()
    if a.dir:
        sign_dir(a.dir, a.key)
        return
    if not a.tag:
        ap.error("sürüm etiketi (ör. v0.8.1) ya da --dir gerekli")
    if not shutil.which("gh"):
        raise SystemExit("GitHub CLI (gh) bulunamadı: https://cli.github.com")
    tmp = tempfile.mkdtemp(prefix="kurgu-sign-")
    subprocess.run(["gh", "release", "download", a.tag, "-R", REPO, "-D", tmp,
                    "--skip-existing"], check=True)
    for n in (SUMS, SIG):  # eski imza dosyaları imzalanmasın
        if os.path.exists(os.path.join(tmp, n)):
            os.remove(os.path.join(tmp, n))
    files = sign_dir(tmp, a.key)
    subprocess.run(["gh", "release", "upload", a.tag, "-R", REPO, "--clobber", *files],
                   check=True)
    print(f"İmzalandı ve yüklendi: {a.tag}")


if __name__ == "__main__":
    main()
