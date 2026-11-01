"""Güncelleme imzalama anahtarı üretir (bir kez, KENDİ bilgisayarında çalıştır).

    python packaging/make_signing_key.py

- Gizli anahtar ~/.kurgu-signing/kurgu-release.key dosyasına yazılır. Bu dosyayı
  GitHub'a, depoya ya da başka bir yere YÜKLEME; yedeğini çevrim dışı bir yerde (USB,
  parola yöneticisi) sakla. Kaybedersen yeni anahtar üretip açık anahtarı güncellemen gerekir.
- Ekrana yazılan açık anahtarı kurgu/updater.py içindeki PUBLIC_KEYS'e ekle.
"""
import base64
import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEY_DIR = os.path.join(os.path.expanduser("~"), ".kurgu-signing")
KEY_PATH = os.path.join(KEY_DIR, "kurgu-release.key")


def main():
    if os.path.exists(KEY_PATH) and "--force" not in sys.argv:
        print(f"Anahtar zaten var: {KEY_PATH}  (yenisi için --force)")
        priv = load_private()
    else:
        os.makedirs(KEY_DIR, exist_ok=True)
        priv = Ed25519PrivateKey.generate()
        raw = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
        fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(base64.b64encode(raw).decode() + "\n")
        print(f"Gizli anahtar yazıldı: {KEY_PATH}")
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    print("\nkurgu/updater.py içine ekle:\n")
    print(f'PUBLIC_KEYS: tuple[str, ...] = ("{base64.b64encode(pub).decode()}",)')


def load_private(path: str = KEY_PATH) -> Ed25519PrivateKey:
    with open(path) as f:
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(f.read().strip()))


if __name__ == "__main__":
    main()
