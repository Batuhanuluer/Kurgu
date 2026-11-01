"""Altyazılar: SRT/VTT okuma-yazma, konuşmadan altyazı (Whisper), satırlara bölme.

Altyazılar zaman çizelgesinde özel bir "altyazı izi"nde (Track.captions) "caption"
türünde kliplerdir; görünümleri (yazı boyutu, renk, kutu, konum) izin stilinden gelir,
böylece tüm altyazılar tek yerden biçimlenir.

Konuşma tanıma için faster-whisper kullanılır (isteğe bağlı paket). Model ilk kullanımda
indirilir ve ~/.kurgu/models altında saklanır; sonrası internetsiz çalışır.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import numpy as np

MODEL_DIR = os.path.join(os.path.expanduser("~"), ".kurgu", "models")
MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"]
LANGUAGES = [("", "Auto-detect"), ("tr", "Turkish"), ("en", "English"), ("de", "German"),
             ("fr", "French"), ("es", "Spanish"), ("it", "Italian"), ("ar", "Arabic"),
             ("ru", "Russian"), ("ja", "Japanese"), ("pt", "Portuguese"),
             ("nl", "Dutch"), ("az", "Azerbaijani")]

STYLE_DEFAULTS = {
    "size": 0.05,          # yazı yüksekliği / kare yüksekliği
    "color": "#ffffff",
    "outline": True,
    "box": True,           # arkasında yarı saydam kutu
    "box_color": "#000000",
    "box_opacity": 0.55,
    "y": 0.90,             # alt kenarın konumu (0 üst, 1 alt)
    "width": 0.86,         # en çok satır genişliği / kare genişliği
    "bold": True,
}


def style_of(track) -> dict:
    s = dict(STYLE_DEFAULTS)
    s.update(getattr(track, "style", None) or {})
    return s


@dataclass
class Cue:
    start: float
    end: float
    text: str


# ------------------------------------------------------------ SRT / VTT
_TS = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")


def _ts(s: str) -> float:
    m = _TS.search(s)
    if not m:
        m2 = re.search(r"(\d{1,2}):(\d{1,2})[,.](\d{1,3})", s)  # VTT: dk:sn.ms
        if not m2:
            raise ValueError(f"zaman okunamadı: {s!r}")
        mm, ss, ms = m2.groups()
        return int(mm) * 60 + int(ss) + int(ms.ljust(3, "0")) / 1000
    h, mm, ss, ms = m.groups()
    return int(h) * 3600 + int(mm) * 60 + int(ss) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[Cue]:
    """SRT ve WebVTT okur (biçim bozukluklarına toleranslı)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = [x for x in block.strip("\n").split("\n")]
        idx = next((i for i, x in enumerate(lines) if "-->" in x), None)
        if idx is None:
            continue
        a, b = lines[idx].split("-->", 1)
        try:
            start, end = _ts(a), _ts(b)
        except ValueError:
            continue
        body = "\n".join(x.strip() for x in lines[idx + 1:] if x.strip())
        body = re.sub(r"</?[^>]{1,20}>", "", body)  # <i>, <b>, <font ...> etiketleri
        if body and end > start:
            cues.append(Cue(start, end, body))
    return sorted(cues, key=lambda c: c.start)


def _fmt(t: float, sep: str = ",") -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def format_srt(cues: list[Cue]) -> str:
    out = []
    for i, c in enumerate(sorted(cues, key=lambda c: c.start), 1):
        out.append(f"{i}\n{_fmt(c.start)} --> {_fmt(c.end)}\n{c.text.strip()}\n")
    return "\n".join(out)


def format_vtt(cues: list[Cue]) -> str:
    out = ["WEBVTT", ""]
    for c in sorted(cues, key=lambda c: c.start):
        out.append(f"{_fmt(c.start, '.')} --> {_fmt(c.end, '.')}\n{c.text.strip()}\n")
    return "\n".join(out)


# ------------------------------------------------------------ satırlara bölme
def wrap_text(text: str, max_chars: int = 42, max_lines: int = 2) -> str:
    """Okunaklı altyazı satırları: en fazla max_chars karakter, dengeli iki satır."""
    words = text.split()
    if not words:
        return ""
    if len(text) <= max_chars or max_lines < 2:
        return " ".join(words)
    # İki satır: toplam uzunluğu en dengeli bölen kelime sınırı
    best, best_cost = 1, 1e9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        cost = abs(len(a) - len(b)) + (1000 if max(len(a), len(b)) > max_chars else 0)
        if cost < best_cost:
            best, best_cost = i, cost
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def words_to_cues(words, max_chars: int = 42, max_lines: int = 2,
                  max_dur: float = 6.0, min_gap: float = 0.6) -> list[Cue]:
    """Kelime zamanlarından altyazı blokları: cümle sonunda, uzun duraklamada ya da
    karakter/süre sınırında yeni blok başlar. words: [(start, end, kelime), ...]"""
    cues: list[Cue] = []
    cur: list = []
    limit = max_chars * max_lines

    def flush():
        if cur:
            text = " ".join(w[2].strip() for w in cur).strip()
            text = re.sub(r"\s+([,.!?;:])", r"\1", text)
            if text:
                cues.append(Cue(cur[0][0], cur[-1][1], wrap_text(text, max_chars, max_lines)))
        cur.clear()

    for w in words:
        s, e, t = float(w[0]), float(w[1]), str(w[2])
        if not t.strip():
            continue
        if cur:
            length = len(" ".join(x[2].strip() for x in cur)) + 1 + len(t.strip())
            if (s - cur[-1][1] > min_gap or length > limit or e - cur[0][0] > max_dur):
                flush()
        cur.append((s, e, t))
        if t.strip().endswith((".", "?", "!", "…")) and e - cur[0][0] > 1.2:
            flush()
    flush()
    # Okuma süresi: çok kısa blokları bir sonrakine çarpmadan uzat
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else c.end + 2
        c.end = min(max(c.end, c.start + 0.8), nxt - 0.04) if nxt > c.start + 0.1 else c.end
    return cues


def segments_to_cues(segments, max_chars=42, max_lines=2) -> list[Cue]:
    """Whisper bölümleri (kelime zamanlı ya da değil) -> altyazı blokları."""
    words = []
    plain = []
    for seg in segments:
        ws = getattr(seg, "words", None)
        if ws:
            words += [(w.start, w.end, w.word) for w in ws]
        else:
            plain.append(Cue(seg.start, seg.end, seg.text.strip()))
    cues = words_to_cues(words, max_chars, max_lines) if words else []
    for c in plain:  # kelime zamanı yoksa bölümü olduğu gibi (gerekirse ikiye) kullan
        c.text = wrap_text(c.text, max_chars, max_lines)
        cues.append(c)
    return sorted(cues, key=lambda c: c.start)


# ------------------------------------------------------------ konuşma tanıma
class TranscriberMissing(RuntimeError):
    pass


def have_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def sequence_audio(project, start: float, end: float, rate: int = 16000,
                   progress=None, cancelled=None) -> np.ndarray:
    """Sekansın [start, end) aralığındaki miksi (duyulan ses) mono 16 kHz olarak."""
    from .media import SourcePool
    from .render import Compositor
    pool = SourcePool(project.sample_rate)
    comp = Compositor(project, pool)
    sr = project.sample_rate
    chunk = sr * 5
    total = max(0, int((end - start) * sr))
    parts = []
    try:
        done = 0
        while done < total:
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            n = min(chunk, total - done)
            st = comp.mix(start + done / sr, n).mean(axis=1)
            parts.append(st.astype(np.float32))
            done += n
            if progress:
                progress(done / max(total, 1))
    finally:
        pool.close()
    if not parts:
        return np.zeros(0, np.float32)
    x = np.concatenate(parts)
    # Basit yeniden örnekleme (konuşma için yeterli; önce kutu süzgeci)
    k = sr / rate
    n = int(len(x) / k)
    if abs(k - round(k)) < 1e-9 and k >= 1:
        k = int(round(k))
        x = x[:n * k].reshape(n, k).mean(axis=1)
    else:
        x = np.interp(np.arange(n) * k, np.arange(len(x)), x).astype(np.float32)
    return x.astype(np.float32)


def transcribe(audio: np.ndarray, language: str = "", model_size: str = "small",
               progress=None, cancelled=None, engine=None):
    """16 kHz mono ses -> Whisper bölümleri (kelime zamanlı).
    engine verilirse (testler) onun transcribe(audio, ...) yöntemi kullanılır."""
    if engine is None:
        try:
            from faster_whisper import WhisperModel
        except Exception as e:  # noqa: BLE001
            raise TranscriberMissing(str(e))
        os.makedirs(MODEL_DIR, exist_ok=True)
        engine = WhisperModel(model_size, device="auto", compute_type="int8",
                              download_root=MODEL_DIR)
    segments, info = engine.transcribe(audio, language=language or None, word_timestamps=True,
                                       vad_filter=True, beam_size=5)
    dur = max(len(audio) / 16000.0, 1e-6)
    out = []
    for seg in segments:  # üreteç: ilerledikçe çözülür
        out.append(seg)
        if progress:
            progress(min(seg.end / dur, 1.0))
        if cancelled and cancelled():
            raise RuntimeError("cancelled")
    return out, getattr(info, "language", language)
