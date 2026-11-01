"""Çeviriler: her dil aynı yer tutucuları korur, koddaki metinler Türkçe'de eksiksiz,
dil seçimi kaydedilir."""
import ast
import glob
import os
import re
import subprocess
import sys

import pytest

from kurgu import i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = re.compile(r"\{\w*\}")


def code_strings() -> set:
    """Kodda _("…") ile çevrilen tüm sabit metinler."""
    out = set()
    for f in glob.glob(os.path.join(ROOT, "kurgu", "**", "*.py"), recursive=True):
        tree = ast.parse(open(f, encoding="utf-8").read())
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_" and n.args
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                out.add(n.args[0].value)
    return out


@pytest.mark.parametrize("code", sorted(i18n.LANGS))
def test_placeholders_and_breaks_match(code):
    for k, v in i18n.LANGS[code].items():
        assert sorted(PH.findall(k)) == sorted(PH.findall(v)), (code, k, v)
        assert k.count("\n") == v.count("\n"), (code, k)
        assert v.strip(), (code, k)


@pytest.mark.parametrize("code", sorted(i18n.LANGS))
def test_every_ui_string_translated(code):
    missing = sorted(s for s in code_strings() if s not in i18n.LANGS[code])
    assert not missing, f"{code}: {len(missing)} eksik çeviri: {missing[:20]}"


def test_languages_listed():
    codes = [c for c, _n in i18n.LANGUAGES]
    assert "en" in codes and all(c in codes for c in i18n.LANGS)


def test_set_language():
    old = i18n.LANG
    try:
        i18n.set_language("es")
        assert i18n._("Save") == "Guardar" and i18n.effective() == "es"
        i18n.set_language("xx")
        assert i18n._("Save") == "Save" and i18n.effective() == "en"
    finally:
        i18n.set_language(old)


def test_saved_language_used_at_startup(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               XDG_CONFIG_HOME=str(tmp_path / "cfg"), QT_QPA_PLATFORM="offscreen")
    env.pop("KURGU_LANG", None)
    code = (f"import sys; sys.path.insert(0, {ROOT!r})\n"
            "from PySide6.QtCore import QSettings\n"
            "s = QSettings('Kurgu', 'Kurgu'); s.setValue('language', 'es'); s.sync()\n")
    subprocess.run([sys.executable, "-c", code], env=env, check=True)
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {ROOT!r})\n"
                        "from kurgu.i18n import _, LANG; print(LANG, _('Save'))"],
                       env=env, capture_output=True, text=True)
    assert r.stdout.split() == ["es", "Guardar"], r.stdout + r.stderr
