from kurgu import updater


def test_version_compare():
    assert updater.is_newer("0.6.0", "0.5.1")
    assert updater.is_newer("v1.0", "0.9.9")
    assert not updater.is_newer("0.5.1", "0.5.1")
    assert not updater.is_newer("0.5.0", "0.5.1")
    assert updater.parse_version("v0.10.2-beta") == (0, 10, 2)


def test_self_update_only_on_frozen_windows(monkeypatch):
    info = {"setup_url": "https://x/Kurgu-0.6.0-setup.exe"}
    monkeypatch.setattr(updater.sys, "platform", "linux")
    assert not updater.can_self_update(info)


# ------------------------------------------------------------ imzalı güncelleme
import base64  # noqa: E402
import hashlib  # noqa: E402

import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

GH = "https://github.com/Batuhanuluer/Kurgu/releases/download/v9.9.9/"


def _keypair():
    k = Ed25519PrivateKey.generate()
    pub = k.public_key().public_bytes(serialization.Encoding.Raw,
                                      serialization.PublicFormat.Raw)
    return k, base64.b64encode(pub).decode()


def _release(setup_bytes, key, digest=True):
    h = hashlib.sha256(setup_bytes).hexdigest()
    sums = f"{h}  Kurgu-9.9.9-setup.exe\n{'0' * 64}  Kurgu-9.9.9-linux.tar.gz\n".encode()
    sig = base64.b64encode(key.sign(sums)).decode()
    data = {"tag_name": "v9.9.9", "assets": [
        {"name": "Kurgu-9.9.9-setup.exe", "browser_download_url": GH + "Kurgu-9.9.9-setup.exe",
         "digest": f"sha256:{h}" if digest else None},
        {"name": "SHA256SUMS.txt", "browser_download_url": GH + "SHA256SUMS.txt"},
        {"name": "SHA256SUMS.txt.sig", "browser_download_url": GH + "SHA256SUMS.txt.sig"}]}
    files = {GH + "SHA256SUMS.txt": sums, GH + "SHA256SUMS.txt.sig": sig.encode()}
    return updater.parse_release(data), files


def test_self_update_requires_signing_key(monkeypatch):
    k, pub = _keypair()
    info, _f = _release(b"x", k)
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "PUBLIC_KEYS", ())
    assert not updater.can_self_update(info)  # gömülü anahtar yok -> indirme sayfası
    monkeypatch.setattr(updater, "PUBLIC_KEYS", (pub,))
    assert updater.can_self_update(info)
    unsigned = dict(info, sig_url=None)
    assert not updater.can_self_update(unsigned)
    evil = dict(info, setup_url="https://evil.example/Kurgu-setup.exe")
    assert not updater.can_self_update(evil)
    assert not updater.can_self_update(dict(info, setup_url=GH.replace("https", "http") + "a"))


def test_signed_sums_verified(monkeypatch, tmp_path):
    k, pub = _keypair()
    payload = b"MZ fake installer"
    info, files = _release(payload, k)
    fetch = files.__getitem__
    assert updater.expected_hash(info, keys=(pub,), fetch=fetch) == \
        hashlib.sha256(payload).hexdigest()
    # Başka anahtarla imzalanmış (ör. ele geçirilmiş hesap) -> red
    _k2, pub2 = _keypair()
    with pytest.raises(updater.UpdateVerificationError):
        updater.expected_hash(info, keys=(pub2,), fetch=fetch)
    # Liste değiştirilmiş -> imza tutmaz
    bad = dict(files)
    bad[GH + "SHA256SUMS.txt"] = files[GH + "SHA256SUMS.txt"].replace(b"0000", b"1111", 1)
    with pytest.raises(updater.UpdateVerificationError):
        updater.expected_hash(info, keys=(pub,), fetch=bad.__getitem__)
    # GitHub özeti imzalı listeyle uyuşmuyor -> red
    with pytest.raises(updater.UpdateVerificationError):
        updater.expected_hash(dict(info, setup_digest="sha256:" + "f" * 64), keys=(pub,),
                              fetch=fetch)


def test_download_verified_rejects_tampered_file(monkeypatch, tmp_path):
    k, pub = _keypair()
    info, files = _release(b"good installer", k)

    def fake_download(url, progress=None, cancelled=None):
        p = tmp_path / "Kurgu-9.9.9-setup.exe"
        p.write_bytes(fake_download.content)
        return str(p)
    monkeypatch.setattr(updater, "download", fake_download)
    fake_download.content = b"good installer"
    assert updater.download_verified(info, keys=(pub,), fetch=files.__getitem__)
    fake_download.content = b"evil installer"
    with pytest.raises(updater.UpdateVerificationError):
        updater.download_verified(info, keys=(pub,), fetch=files.__getitem__)
    assert not (tmp_path / "Kurgu-9.9.9-setup.exe").exists()  # doğrulanmayan dosya silinir


def test_download_refuses_other_hosts():
    with pytest.raises(updater.UpdateVerificationError):
        updater.download("https://evil.example/x.exe")
    with pytest.raises(updater.UpdateVerificationError):
        updater.download("http://github.com/x.exe")


def test_sign_release_script_roundtrip(tmp_path, monkeypatch):
    import importlib.util
    import os
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "packaging"))
    try:
        spec = importlib.util.spec_from_file_location(
            "sign_release", os.path.join(root, "packaging", "sign_release.py"))
        sr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sr)
    finally:
        sys.path.pop(0)
    k, pub = _keypair()
    raw = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                          serialization.NoEncryption())
    keyf = tmp_path / "k.key"
    keyf.write_text(base64.b64encode(raw).decode())
    d = tmp_path / "dist"
    d.mkdir()
    (d / "Kurgu-9.9.9-setup.exe").write_bytes(b"abc")
    sr.sign_dir(str(d), str(keyf))
    sums = (d / "SHA256SUMS.txt").read_bytes()
    assert updater.verify_signature(sums, (d / "SHA256SUMS.txt.sig").read_text(), (pub,))
    assert updater.parse_sums(sums.decode())["Kurgu-9.9.9-setup.exe"] == \
        hashlib.sha256(b"abc").hexdigest()
