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
