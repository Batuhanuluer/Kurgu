"""Flatpak manifestindeki sürüm URL'sini ve sha256'yı GitHub sürümüne göre günceller.

    python packaging/flatpak/update_manifest.py 0.8.1
"""
import hashlib
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "org.kurgu.Kurgu.yml")
URL = "https://github.com/Batuhanuluer/Kurgu/releases/download/v{v}/Kurgu-{v}-linux-x86_64.tar.gz"


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    v = sys.argv[1].lstrip("v")
    url = URL.format(v=v)
    print("İndiriliyor:", url)
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as r:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
    text = open(MANIFEST, encoding="utf-8").read()
    text = re.sub(r"url: https://github\.com/Batuhanuluer/Kurgu/releases/download/\S+",
                  "url: " + url, text)
    text = re.sub(r"sha256: '?[0-9a-f]{64}'?", f"sha256: '{h.hexdigest()}'", text, count=1)
    open(MANIFEST, "w", encoding="utf-8").write(text)
    print("Güncellendi:", h.hexdigest())


if __name__ == "__main__":
    main()
