"""Seslendirme kaydı (Premiere "Seslendirme Kaydı"): mikrofondan WAV dosyasına.

Kayıt QAudioSource ile alınır; örnekler bellekte float32 olarak toplanır ve durdurunca
16-bit PCM WAV olarak yazılır. Cihaz yoksa (ya da testte) `start(fake=True)` ile veri
`feed()` üzerinden verilebilir.
"""
from __future__ import annotations

import os
import time
import wave

import numpy as np
from PySide6.QtCore import QObject, Signal


def write_wav(path: str, samples: np.ndarray, rate: int) -> str:
    """samples: (n,) ya da (n, kanal) float32 -1..1 → 16-bit PCM WAV."""
    a = np.asarray(samples, np.float32)
    if a.ndim == 1:
        a = a[:, None]
    pcm = (np.clip(a, -1.0, 1.0) * 32767.0).round().astype("<i2")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(a.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())
    return path


def next_take_path(folder: str, stem: str = "Voiceover") -> str:
    """Çakışmayan dosya adı: Voiceover 001.wav, Voiceover 002.wav…"""
    os.makedirs(folder, exist_ok=True)
    i = 1
    while True:
        p = os.path.join(folder, f"{stem} {i:03d}.wav")
        if not os.path.exists(p):
            return p
        i += 1


def to_float(raw: bytes, fmt_name: str, channels: int) -> np.ndarray:
    """QAudioFormat örnek biçimindeki ham baytları (n, kanal) float32 diziye çevirir."""
    if fmt_name == "Int16":
        a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif fmt_name == "Int32":
        a = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    elif fmt_name == "UInt8":
        a = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    else:  # Float
        a = np.frombuffer(raw, "<f4").astype(np.float32)
    n = len(a) // max(channels, 1)
    return a[:n * channels].reshape(n, channels)


def input_devices() -> list:
    try:
        from PySide6.QtMultimedia import QMediaDevices
        return list(QMediaDevices.audioInputs())
    except Exception:
        return []


class VoiceRecorder(QObject):
    level = Signal(float, float)  # son parçanın tepe değeri (sol, sağ)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.io = None
        self.rate = 48000
        self.channels = 1
        self._fmt = "Int16"
        self._chunks: list[np.ndarray] = []
        self._pending = b""
        self.recording = False
        self.started_at = 0.0

    # ------------------------------------------------------------
    def start(self, device=None, fake: bool = False, rate: int = 48000,
              channels: int = 1) -> bool:
        self._chunks = []
        self._pending = b""
        if fake:
            self.rate, self.channels, self._fmt = rate, channels, "Float"
            self.recording = True
            self.started_at = time.monotonic()
            return True
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
            dev = device if device is not None else QMediaDevices.defaultAudioInput()
            if dev is None or dev.isNull():
                self.failed.emit("no-device")
                return False
            fmt = QAudioFormat()
            fmt.setSampleRate(48000)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.Int16)
            if not dev.isFormatSupported(fmt):
                fmt = dev.preferredFormat()
            names = {QAudioFormat.Int16: "Int16", QAudioFormat.Int32: "Int32",
                     QAudioFormat.UInt8: "UInt8", QAudioFormat.Float: "Float"}
            self._fmt = names.get(fmt.sampleFormat(), "Int16")
            self.rate, self.channels = fmt.sampleRate(), max(1, fmt.channelCount())
            self.source = QAudioSource(dev, fmt, self)
            self.io = self.source.start()
            if self.io is None:
                raise RuntimeError("start")
            self.io.readyRead.connect(self._read)
        except Exception as e:  # cihaz meşgul, izin yok…
            self._close()
            self.failed.emit(str(e) or "error")
            return False
        self.recording = True
        self.started_at = time.monotonic()
        return True

    def _read(self):
        if self.io is None:
            return
        self.feed_raw(bytes(self.io.readAll().data()))

    def feed_raw(self, raw: bytes):
        """Cihazdan gelen ham baytlar (yarım örnekler bir sonraki parçaya taşınır)."""
        width = {"Int16": 2, "Int32": 4, "UInt8": 1, "Float": 4}[self._fmt] * self.channels
        raw = self._pending + raw
        cut = len(raw) - len(raw) % width
        self._pending = raw[cut:]
        if cut:
            self.feed(to_float(raw[:cut], self._fmt, self.channels))

    def feed(self, block: np.ndarray):
        if not self.recording:
            return
        b = np.asarray(block, np.float32)
        if b.ndim == 1:
            b = b[:, None]
        self._chunks.append(b)
        pk = np.abs(b).max(axis=0) if len(b) else np.zeros(b.shape[1])
        self.level.emit(float(pk[0]), float(pk[-1]))

    @property
    def seconds(self) -> float:
        return sum(len(c) for c in self._chunks) / float(self.rate)

    def stop(self) -> np.ndarray:
        """Kaydı bitirir ve (n, kanal) float32 örnekleri döndürür."""
        if self.io is not None:
            try:
                self.feed_raw(bytes(self.io.readAll().data()))
            except Exception:
                pass
        self.recording = False
        self._close()
        if not self._chunks:
            return np.zeros((0, self.channels), np.float32)
        out = np.concatenate(self._chunks)
        self._chunks = []
        return out

    def _close(self):
        if self.source is not None:
            try:
                self.source.stop()
            except Exception:
                pass
            self.source.deleteLater()
        self.source = None
        self.io = None
