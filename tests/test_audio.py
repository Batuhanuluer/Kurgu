"""Ses: efektler, kesintisiz parça işleme, iz düzeyi/pan/solo, LUFS, normalizasyon, mikser."""
import os
import subprocess
import sys

import av
import numpy as np
import pytest

from kurgu import audio as au
from kurgu.media import SourcePool, probe
from kurgu.model import Project
from kurgu.render import Compositor

SR = 48000


def write_audio(path, x, sr=SR):
    out = av.open(str(path), "w")
    s = out.add_stream("pcm_s16le", rate=sr)
    s.layout = "stereo"
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    for i in range(0, len(pcm), 1024):
        f = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm[i:i + 1024].reshape(1, -1)),
                                       format="s16", layout="stereo")
        f.sample_rate = sr
        f.pts = i
        for p in s.encode(f):
            out.mux(p)
    for p in s.encode():
        out.mux(p)
    out.close()
    return str(path)


def tone(freq, sec, amp=0.25):
    t = np.arange(int(sec * SR)) / SR
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([x, x], 1)


def band_energy(x, f0, f1):
    sp = np.abs(np.fft.rfft(x[:, 0]))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float((sp[(f >= f0) & (f < f1)] ** 2).sum())


def test_eq_and_hum():
    x = tone(100, 1) + tone(5000, 1)
    eq = au.new_audio_fx("eq")
    eq["params"].update(low=-12, high=0)
    y = au.process(x, [eq], SR)
    assert band_energy(y, 80, 120) < band_energy(x, 80, 120) * 0.15  # bas kısıldı
    assert abs(band_energy(y, 4900, 5100) / band_energy(x, 4900, 5100) - 1) < 0.25
    hum = au.new_audio_fx("hum")
    x = tone(50, 1) + tone(150, 1) + tone(440, 1)
    y = au.process(x, [hum], SR)[SR // 4:-SR // 4]
    xm = x[SR // 4:-SR // 4]
    assert band_energy(y, 45, 55) < band_energy(xm, 45, 55) * 0.05
    assert band_energy(y, 430, 450) > band_energy(xm, 430, 450) * 0.8


def test_compressor_and_gate():
    loud = tone(440, 1, 0.9)
    comp = au.new_audio_fx("compressor")
    comp["params"].update(threshold=-20, ratio=8, makeup=0)
    y = au.process(loud, [comp], SR)
    assert np.abs(y[SR // 2:]).max() < 0.35  # tepe ezildi
    noise = (np.random.default_rng(0).normal(0, 0.002, (SR, 2))).astype(np.float32)
    speech = np.vstack([noise, tone(300, 0.5, 0.3), noise])
    gate = au.new_audio_fx("gate")
    y = au.process(speech, [gate], SR)
    assert np.abs(y[SR // 2:SR - 2000]).max() < 0.002 * 0.2  # sessiz bölüm kapandı
    assert np.abs(y[SR + 6000:SR + 18000]).max() > 0.25     # konuşma geçti


def test_loudness_measure_and_normalize():
    x = tone(1000, 3, 0.1)
    l1 = au.integrated_loudness(x, SR)
    l2 = au.integrated_loudness(x * 2, SR)
    assert abs((l2 - l1) - 6.02) < 0.1
    # BS.1770: iki kanalda 1 kHz, -20 dBFS tepe sinüs ≈ -20 LUFS (+3 kanal toplamı, -3 RMS)
    assert abs(au.integrated_loudness(tone(1000, 3, 0.1), SR) - (-20.0)) < 0.7
    g = au.normalize_gain(x, SR, -14.0)
    assert abs(au.integrated_loudness(x * g, SR) + 14.0) < 0.2
    assert au.true_peak_db(x * au.normalize_gain(x, SR, -1.0, peak_mode=True)) == \
        pytest.approx(-1.0, abs=0.01)


@pytest.fixture(scope="module")
def music(tmp_path_factory):
    rng = np.random.default_rng(1)
    x = tone(220, 4, 0.2) + (rng.normal(0, 0.05, (4 * SR, 2))).astype(np.float32)
    return write_audio(tmp_path_factory.mktemp("au") / "m.wav", x)


def _proj(path, fx=()):
    p = Project(320, 180, 25.0)
    made = p.place_media(p.add_media(probe(path)), 0.0)
    a = [c for c in made if not p.track_is_video(c.track)][0]
    a.audio_fx = list(fx)
    return p, a


def test_chunked_mix_is_seamless(music):
    """Efektli ses parça parça karıştırılınca tek seferde karıştırmayla aynı olmalı."""
    eq = au.new_audio_fx("eq")
    eq["params"].update(low=6, presence=-6, low_cut=80)
    comp_fx = au.new_audio_fx("compressor")
    p, _a = _proj(music, [eq, comp_fx])
    whole = Compositor(p, SourcePool()).mix(1.0, SR)
    c2 = Compositor(p, SourcePool())
    parts = np.vstack([c2.mix(1.0 + i * 1024 / SR, 1024) for i in range(SR // 1024)])
    n = len(parts)
    assert np.abs(whole[:n] - parts).max() < 2e-3


def test_track_volume_pan_solo_master(music):
    p, a = _proj(music)
    comp = Compositor(p, SourcePool())
    ref = comp.mix(1.0, 4800)
    tr = p.tracks[a.track]
    tr.volume_db = -6
    assert np.abs(comp.mix(1.0, 4800)).max() == pytest.approx(np.abs(ref).max() * 0.501, rel=0.02)
    tr.volume_db, tr.pan = 0, -1
    out = comp.mix(1.0, 4800)
    assert np.abs(out[:, 1]).max() < 1e-6 and np.abs(out[:, 0]).max() > np.abs(ref[:, 0]).max()
    tr.pan = 0
    other = p.track_indices("audio")[1]
    p.tracks[other].solo = True  # başka iz solo: bu iz susar
    assert np.abs(comp.mix(1.0, 4800)).max() == 0
    assert comp.levels[a.track] == (0.0, 0.0)
    p.tracks[other].solo = False
    p.master_db = -12
    out = comp.mix(1.0, 4800)
    assert np.abs(out).max() == pytest.approx(np.abs(ref).max() * 0.251, rel=0.03)
    assert comp.levels["master"][0] > 0
    q = Project.from_dict(p.to_dict())
    assert q.master_db == -12 and q.tracks[a.track].pan == 0


def test_mixer_and_normalize_gui(tmp_path, music):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys; sys.path.insert(0, {root!r})
from PySide6.QtWidgets import QApplication; app = QApplication([])
import kurgu.ui.player as pl; pl.HAVE_AUDIO = False
from PySide6.QtWidgets import QMessageBox
QMessageBox.exec = lambda self: 0
from kurgu.ui.main_window import MainWindow
w = MainWindow(None); w.resize(1500, 950); w.show(); app.processEvents()
(m,) = w.import_paths([{music!r}])
w.place_media(m, 0.0, None)
w.show_dock(w.mixer_dock); app.processEvents()
strips = w.mixer.strips
assert len(strips) == 3 and strips[-1].master          # A1, A2, ana çıkış
strips[0].fader.setValue(-60)                           # -6 dB
a1 = w.project.track_indices('audio')[0]
assert abs(w.project.tracks[a1].volume_db + 6) < 1e-6
strips[0].pan.setValue(50); assert w.project.tracks[a1].pan == 0.5
strips[1].solo.setChecked(True); assert w.project.tracks[strips[1].ti].solo
w.undo(); w.mixer.rebuild(); assert not w.project.tracks[strips[1].ti].solo
a = [c for c in w.project.clips if not w.project.track_is_video(c.track)][0]
w.timeline.selected = {{a.id}}; w._on_selection(); app.processEvents()
afx = w.inspector.afx_panel
assert afx.isVisibleTo(w.inspector)
afx._add(afx.add.findData('eq')); afx._add(afx.add.findData('compressor'))
assert [e['type'] for e in a.audio_fx] == ['eq', 'compressor']
rep = w.normalize_dialog(target=-16.0)
assert rep and 'LUFS' in rep[0], rep
from kurgu import audio as au
from kurgu.render import Compositor
from kurgu.media import SourcePool
comp = Compositor(w.project, SourcePool())
w.project.tracks[a1].volume_db = 0; w.project.tracks[a1].pan = 0
x = comp.mix(0.5, 48000 * 3)
lu = au.integrated_loudness(x, 48000)
assert abs(lu + 16) < 1.5, lu
w.dirty = False; w.close(); print('OK')
"""
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen",
               USERPROFILE=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=180)
    assert "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
