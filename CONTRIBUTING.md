# Contributing to Kurgu · Kurgu'ya katkı

Thanks for helping! Kurgu is plain Python (PySide6 + PyAV), so if you can read Python you
can fix a bug, add an effect or translate the interface.
*Türkçe özet en altta.*

## Quick start

```bash
git clone https://github.com/Batuhanuluer/Kurgu.git
cd Kurgu
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m kurgu            # run the editor
pytest -q                  # ~160 tests, about a minute, no GPU or screen needed
```

Tests create their own tiny synthetic videos, so you don't need any media files.
GUI tests run with `QT_QPA_PLATFORM=offscreen` (set automatically in `tests/conftest.py`).

## Where things live

| You want to… | Look at |
|---|---|
| Change editing behaviour (trim, ripple, nest…) | `kurgu/model.py` (no Qt, easy to test) |
| Add a video effect | `kurgu/effects.py`: write one function and decorate it with `@register` |
| Add a transition | `kurgu/transitions.py` |
| Add an audio effect | `kurgu/audio.py` → `AUDIO_FX` |
| Change how frames are drawn or mixed | `kurgu/render.py` (`Compositor`, also used by export) |
| Change a panel or menu | `kurgu/ui/…` |
| Add an export preset | `kurgu/exporter.py` → `PRESETS` |

The preview and the export use the same `Compositor`, so what you see is what you get.
A longer map of the code is in the README under "Mimari" (Architecture).

## Pull requests

1. Fork, then create a branch: `git checkout -b fix-gif-palette`.
2. Keep the change focused. One bug or one feature per PR is easiest to review.
3. Add a test when you can. Model changes go in plain pytest tests. UI changes can go in
   the subprocess GUI tests (see `tests/test_nest.py` for a short example).
4. Run `pytest -q`. `tests/test_click_all.py` clicks every menu item and panel button,
   so it catches most crashes in new UI code.
5. Describe *what* changed and *why* in the PR. Screenshots help for UI changes.

Code style: standard Python, 100-column lines, type hints where they help. Comments may
be in English or Turkish.

## Translations

Interface strings are English keys wrapped in `_()`. Each language is one dictionary:

- Turkish: `TR` in `kurgu/i18n.py`
- Spanish: `ES` in `kurgu/locales/es.py`

To add a language (for example German, `de`):

1. Copy `kurgu/locales/es.py` to `kurgu/locales/de.py`, rename the dict to `DE` and
   translate the values. Leave the English keys unchanged.
2. Keep every `{placeholder}` and line break (`\n`) exactly as in the key.
3. In `kurgu/i18n.py`, import it and add it to `LANGS` and `LANGUAGES`:
   ```python
   from .locales.de import DE
   LANGS = {"tr": TR, "es": ES, "de": DE}
   LANGUAGES = [..., ("de", "Deutsch")]
   ```
4. Run `pytest tests/test_i18n.py`. It lists any missing strings or broken placeholders.
5. Try it: `KURGU_LANG=de python -m kurgu`, or pick it in **Edit → Preferences →
   Interface language**.

Partial translations are welcome. Missing strings simply show in English.

## Reporting bugs

The easiest way is inside the app: **Help → Report a Problem…**. It fills in your
Kurgu, OS and FFmpeg versions and hides your personal folder names. You can edit
everything before it opens on GitHub.

Please don't attach private videos. If a specific file causes the problem, describe its
format (resolution, codec, frame rate, phone model) or share a short clip you are happy
to make public.

Security problems: please follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Releases (maintainers)

Releases are built by GitHub Actions from a `v*` tag. See "Yeni sürüm yayınlamak" in the
README.

---

### Türkçe özet

- Kurulum: `pip install -e ".[dev]"`, çalıştırma: `python -m kurgu`, test: `pytest -q`.
- Düzenleme mantığı `kurgu/model.py`'de; efekt eklemek için `kurgu/effects.py`'de tek
  fonksiyon ve `@register` yeterli.
- Her PR tek konu olsun, mümkünse test ekleyin; `tests/test_click_all.py` tüm menü ve
  düğmeleri tıklayarak çökmeleri yakalar.
- Çeviri: `kurgu/locales/es.py`'yi örnek alın, `{yer_tutucu}` ve `\n`'leri koruyun,
  `pytest tests/test_i18n.py` ile kontrol edin.
- Hata bildirimi: uygulamada **Yardım → Sorun Bildir…**.
