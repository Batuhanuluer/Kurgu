"""Altyazılar: SRT/VTT, satır bölme, Whisper çıktısından bloklar, render, arayüz."""
import os
import subprocess
import sys
from types import SimpleNamespace as NS

import numpy as np

from kurgu import captions as cap
from kurgu.media import SourcePool
from kurgu.model import Project
from kurgu.render import Compositor

SRT = """1
00:00:01,000 --> 00:00:03,500
Merhaba <i>dünya</i>

2
00:00:04,250 --> 00:00:06,000
İkinci satır
devamı
"""


def test_srt_roundtrip():
    cues = cap.parse_srt(SRT)
    assert [(c.start, c.end) for c in cues] == [(1.0, 3.5), (4.25, 6.0)]
    assert cues[0].text == "Merhaba dünya" and cues[1].text == "İkinci satır\ndevamı"
    again = cap.parse_srt(cap.format_srt(cues))
    assert [(c.start, c.end, c.text) for c in again] == [(c.start, c.end, c.text) for c in cues]
    vtt = cap.format_vtt(cues)
    assert vtt.startswith("WEBVTT") and "00:00:04.250 --> 00:00:06.000" in vtt
    assert [(c.start, c.text) for c in cap.parse_srt(vtt)] == [(1.0, "Merhaba dünya"),
                                                               (4.25, "İkinci satır\ndevamı")]
    # Windows satır sonları ve BOM
    assert len(cap.parse_srt("﻿" + SRT.replace("\n", "\r\n"))) == 2


def test_wrap_and_words_to_cues():
    t = cap.wrap_text("bu cümle iki satıra dengeli biçimde bölünmesi gereken uzunca bir altyazı")
    a, b = t.split("\n")
    assert abs(len(a) - len(b)) < 12 and max(len(a), len(b)) <= 42
    words = []
    t0 = 0.0
    for sent in ["Merhaba arkadaşlar.", "Bugün yeni bir video düzenleyici deniyoruz ve "
                 "çok heyecanlıyız çünkü tamamen açık kaynak.", "Hadi başlayalım!"]:
        for w in sent.split():
            words.append((t0, t0 + 0.3, " " + w))
            t0 += 0.35
        t0 += 1.0  # cümle arası duraklama
    cues = cap.words_to_cues(words)
    assert cues[0].text == "Merhaba arkadaşlar."
    assert cues[-1].text == "Hadi başlayalım!"
    for c in cues:
        assert all(len(ln) <= 42 for ln in c.text.split("\n")) and c.text.count("\n") <= 1
        assert c.end - c.start <= 6.5 and c.end > c.start
    assert all(cues[i].end <= cues[i + 1].start for i in range(len(cues) - 1))


class FakeWhisper:
    """faster-whisper arayüzünü taklit eder (model indirmeden test)."""

    def __init__(self):
        self.audio = None

    def transcribe(self, audio, language=None, word_timestamps=True, **kw):
        self.audio = audio
        W = lambda s, e, w: NS(start=s, end=e, word=w)  # noqa: E731
        segs = [NS(start=0.5, end=1.6, text=" Merhaba dünya.",
                   words=[W(0.5, 0.9, " Merhaba"), W(1.0, 1.6, " dünya.")]),
                NS(start=2.5, end=3.4, text=" Nasılsınız?",
                   words=[W(2.5, 3.4, " Nasılsınız?")])]
        return iter(segs), NS(language=language or "tr")


def test_transcribe_pipeline_with_fake_engine(red_video):
    from kurgu.media import probe
    p = Project(320, 240, 25.0)
    p.place_media(p.add_media(probe(red_video)), 0.0)
    audio = cap.sequence_audio(p, 0.0, p.duration)
    assert abs(len(audio) / 16000 - p.duration) < 0.05 and np.abs(audio).max() > 0.1
    eng = FakeWhisper()
    segs, lang = cap.transcribe(audio, "tr", engine=eng)
    cues = cap.segments_to_cues(segs)
    assert [c.text for c in cues] == ["Merhaba dünya.", "Nasılsınız?"] and lang == "tr"
    ti = p.caption_track()
    made = p.add_captions(cues)
    assert all(c.track == ti and c.kind == "caption" for c in made)
    assert p.tracks[ti].captions and p.tracks[ti].kind == "video"
    assert ti == max(p.track_indices("video"))  # en üstteki video izi


def test_caption_renders_at_bottom(qapp):
    p = Project(320, 180, 25.0)
    p.add_captions([(0.0, 2.0, "Altyazı")])
    img = Compositor(p, SourcePool()).compose(1.0, 320, 180)
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(180, img.bytesPerLine())[:, :320 * 4]
    bright = arr.reshape(180, 320, 4)[..., :3].max(axis=-1) > 200
    rows = np.where(bright.any(axis=1))[0]
    assert len(rows) and rows.min() > 120 and rows.max() < 170  # altta, kenara yapışık değil
    ti = p.caption_track(create=False)
    p.tracks[ti].style.update(y=0.3)  # stil iz bazında: konum değişince yazı yukarı çıkar
    img = Compositor(p, SourcePool()).compose(1.0, 320, 180)
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(180, img.bytesPerLine())[:, :320 * 4]
    rows = np.where((arr.reshape(180, 320, 4)[..., :3].max(axis=-1) > 200).any(axis=1))[0]
    assert rows.max() < 60


def test_caption_track_insert_keeps_clip_tracks(red_video, qapp):
    from kurgu.media import probe
    p = Project(320, 240, 25.0)
    made = p.place_media(p.add_media(probe(red_video)), 0.0)
    kinds_before = [(c.id, p.tracks[c.track].kind, p.tracks[c.track].name) for c in made]
    p.caption_track()
    kinds_after = [(c.id, p.tracks[c.track].kind, p.tracks[c.track].name) for c in made]
    assert kinds_before == kinds_after  # araya iz eklenince klipler kendi izinde kalır


def test_captions_gui(tmp_path, red_video):
    srt = tmp_path / "a.srt"
    srt.write_text(SRT, encoding="utf-8")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r}); sys.path.insert(0, {os.path.join(root, 'tests')!r})
from PySide6.QtWidgets import QApplication; app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from PySide6.QtWidgets import QMessageBox, QFileDialog
QMessageBox.exec = lambda self: 0
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ({str(srt)!r}, ''))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ({str(tmp_path / 'out.vtt')!r}, 'WebVTT (*.vtt)'))
from kurgu.ui.main_window import MainWindow
from test_captions import FakeWhisper
w = MainWindow(None); w.resize(1500, 950); w.show(); app.processEvents()
(m,) = w.import_paths([{red_video!r}])
w.place_media(m, 0.0, None)
made = w.transcribe_dialog(engine=FakeWhisper(), auto_accept=True)
assert [c.text for c in made] == ['Merhaba dünya.', 'Nasılsınız?'], made
w.show_dock(w.captions_dock); app.processEvents()
cp = w.captions_panel
assert cp.table.rowCount() == 2
cp.table.item(1, 2).setText('Nasılsın?')          # listeden düzenle
assert sorted(c.text for c in w.project.clips if c.kind == 'caption') == ['Merhaba dünya.', 'Nasılsın?']
cp.size.spin.setValue(8)                            # stil
ti = w.project.caption_track(create=False)
assert abs(w.project.tracks[ti].style['size'] - 0.08) < 1e-6
w.export_captions()
txt = open({str(tmp_path / 'out.vtt')!r}, encoding='utf-8').read()
assert txt.startswith('WEBVTT') and 'Nasılsın?' in txt
w.import_captions()   # üzerine yazar: çakışan altyazılar kırpılır/kalkar
texts = [c.text for c in sorted(w.project.clips, key=lambda c: c.start) if c.kind == 'caption']
assert texts[-2:] == ['Merhaba dünya', 'İkinci satır' + chr(10) + 'devamı'], texts
w.undo()
assert sum(1 for c in w.project.clips if c.kind == 'caption') == 2
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_buttons_do_not_pass_checked_flag(tmp_path):
    """Düğme/menü tıklaması Qt'nin 'checked=False' değerini isteğe bağlı parametreye
    (engine, target) geçirmemeli: 'bool' object has no attribute 'transcribe' hatası."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication, QPushButton; app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from kurgu.ui.main_window import MainWindow
calls = []
MainWindow.transcribe_dialog = lambda self, *a, **k: calls.append(('t', a, k))
MainWindow.normalize_dialog = lambda self, *a, **k: calls.append(('n', a, k))
w = MainWindow(None); w.show(); app.processEvents()
btn = [b for b in w.captions_panel.findChildren(QPushButton) if '🎙' in b.text()][0]
btn.click()
nb = [b for b in w.inspector.afx_panel.findChildren(QPushButton) if '📢' in b.text()][0]
nb.click()
act = [a for a in w.findChildren(type(w.menuBar().actions()[0])) if a.text().startswith(('Normalize', 'Ses Yüksekliğini'))][0]
act.trigger()
assert calls == [('t', (), {{}}), ('n', (), {{}}), ('n', (), {{}})], calls
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


# ------------------------------------------------------------ kelime vurgusu (Reels)
def test_words_kept_and_active_word():
    from kurgu import captions as cap
    from kurgu.model import Project
    words = [(1.0, 1.3, " Merhaba"), (1.35, 1.6, " dünya,"), (1.7, 2.0, " bugün"),
             (2.05, 2.4, " hava"), (2.45, 2.9, " güzel.")]
    cues = cap.words_to_cues(words)
    assert cues[0].words and cues[0].words[0] == [0.0, 0.3, "Merhaba"]
    p = Project(640, 360, 25.0)
    c = p.add_captions(cues)[0]
    assert len(c.words) == 5
    assert cap.active_word(c, 0.9 + c.start - 1.0) == -1
    assert cap.active_word(c, c.start + 0.4) == 1
    assert cap.active_word(c, c.start + 1.5) == 4
    # Metin elle değişince kelimelere eşit dağıtılır
    c.text = "Selam herkese"
    ws = cap.clip_words(c)
    assert [w[2] for w in ws] == ["Selam", "herkese"] and abs(ws[-1][1] - c.out_point) < 1e-6
    # Kısa öbekler
    short = cap.words_by_count(words, 2)
    assert [s.text for s in short] == ["Merhaba dünya,", "bugün hava", "güzel."]
    assert short[1].words[0][0] == 0.0 and short[0].end <= short[1].start
    assert cap.upper("istanbul ışık") == "İSTANBUL IŞIK"


def test_rechunk_and_highlight_render(qapp):
    from kurgu import captions as cap
    from kurgu.media import SourcePool
    from kurgu.model import Project
    from kurgu.render import Compositor
    p = Project(640, 360, 25.0)
    words = [(0.0, 0.5, "AAAA"), (0.5, 1.0, "BBBB"), (1.0, 1.5, "CCCC"), (1.5, 2.0, "DDDD")]
    p.add_captions([cap.Cue(0.0, 2.0, "AAAA BBBB CCCC DDDD",
                            [[a, b, w] for a, b, w in words])])
    ti = p.caption_track(create=False)
    made = cap.rechunk(p, ti, 2)
    assert [c.text for c in made] == ["AAAA BBBB", "CCCC DDDD"]
    assert abs(made[1].start - 1.0) < 1e-6 and made[1].words[0][0] == 0.0
    p.tracks[ti].style.update(cap.REELS_STYLE)
    p.tracks[ti].style["highlight_box"] = False
    comp = Compositor(p, SourcePool())

    def yellow_x(t):
        img = comp.compose(t, 640, 360)
        xs = [x for x in range(0, 640, 2) for y in range(200, 260, 3)
              if (lambda c: c.red() > 200 and c.green() > 180 and c.blue() < 90)(
                  img.pixelColor(x, y))]
        return (min(xs) + max(xs)) / 2 if xs else None
    a, b = yellow_x(0.2), yellow_x(0.7)
    assert a is not None and b is not None and b > a + 40  # vurgu sağdaki kelimeye geçti
    p.tracks[ti].style["highlight"] = False
    assert yellow_x(0.7) is None


def test_reels_button_gui(tmp_path):
    import os, subprocess, sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication, QMessageBox; app = QApplication([])
QMessageBox.exec = lambda self: 0
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from kurgu import captions as cap
from kurgu.ui.main_window import MainWindow
w = MainWindow(None); w.resize(1400, 900); w.show(); app.processEvents()
ws = [[i * 0.4, i * 0.4 + 0.35, f'k{{i}}'] for i in range(7)]
w.project.add_captions([cap.Cue(0.0, 2.8, ' '.join(x[2] for x in ws), ws)])
w.on_edited()
cp = w.captions_panel; cp.refresh()
cp.apply_reels()
ti = w.project.caption_track(create=False)
st = cap.style_of(w.project.tracks[ti])
assert st['highlight'] and st['uppercase'] and cp.hl.isChecked() and cp.upper.isChecked()
cs = cp.clips()
assert [c.text for c in cs] == ['k0 k1 k2', 'k3 k4 k5', 'k6'], [c.text for c in cs]
cp.words.setValue(1); cp.split_phrases(1)
assert len(cp.clips()) == 7
w.undo(); w.undo()
assert len(w.captions_panel.clips()) == 1
assert not cap.style_of(w.project.tracks[ti])['highlight']
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
