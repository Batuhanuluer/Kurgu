"""Medya motoru: dosya inceleme, kare ve ses çözme (PyAV / FFmpeg).

PyAV, FFmpeg kütüphanelerini kendi içinde getirdiği için Windows, macOS
ve Linux'ta ayrıca FFmpeg kurmaya gerek yoktur.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict

import av
import numpy as np

from .model import MediaItem

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"}


def probe(path: str) -> MediaItem:
    """Bir dosyanın türünü, süresini ve boyutlarını bulur."""
    ext = os.path.splitext(path)[1].lower()
    with av.open(path) as c:
        v = c.streams.video[0] if c.streams.video else None
        a = c.streams.audio[0] if c.streams.audio else None
        if v is None and a is None:
            raise ValueError("Dosyada video veya ses akışı yok")
        if ext in IMAGE_EXT or (v is not None and a is None and (v.frames or 0) <= 1
                                and not c.duration):
            return MediaItem(path=path, kind="image", width=v.codec_context.width,
                             height=v.codec_context.height, has_video=True)
        duration = 0.0
        if c.duration:
            duration = c.duration / av.time_base
        else:
            for s in (v, a):
                if s is not None and s.duration:
                    duration = max(duration, float(s.duration * s.time_base))
        if v is None:
            return MediaItem(path=path, kind="audio", duration=duration, has_audio=True)
        fps = float(v.average_rate or v.guessed_rate or 30)
        return MediaItem(path=path, kind="video", duration=duration,
                         width=v.codec_context.width, height=v.codec_context.height,
                         fps=fps, has_video=True, has_audio=a is not None)


class VideoSource:
    """Bir video dosyasından istenen zamandaki kareyi verir.

    Ardışık okumada (oynatma, dışa aktarma) baştan arama yapmadan ileri
    doğru çözer; atlamalarda en yakın anahtar kareye gidip oradan ilerler.
    """

    def __init__(self, path: str):
        self.path = path
        self.container = av.open(path)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        self.tb = float(self.stream.time_base)
        self.fps = float(self.stream.average_rate or 30)
        self.start = float(self.stream.start_time * self.tb) if self.stream.start_time else 0.0
        self._decoder = None
        self._cur = None  # (zaman, av.VideoFrame)
        self._next = None
        self.cache: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def _seek(self, t: float):
        target = int((t + self.start) / self.tb)
        self.container.seek(max(target, 0), stream=self.stream, backward=True, any_frame=False)
        self._decoder = self.container.decode(self.stream)
        self._cur = None
        self._next = None

    def _pull(self):
        try:
            f = next(self._decoder)
        except (StopIteration, av.error.EOFError):
            return None
        t = (f.time if f.time is not None else 0.0) - self.start
        return (t, f)

    def frame_at(self, t: float) -> av.VideoFrame | None:
        with self.lock:
            key = round(t * self.fps)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            half = 0.5 / self.fps
            need_seek = (self._decoder is None or self._cur is None
                         or t < self._cur[0] - half or t > self._cur[0] + 2.0)
            if need_seek:
                self._seek(t)
                self._next = self._pull()
            # cur: t'den önceki son kare, next: sonraki kare
            while self._next is not None and self._next[0] <= t + half:
                self._cur = self._next
                self._next = self._pull()
            if self._cur is None:
                self._cur = self._next
            if self._cur is None:
                return None
            frame = self._cur[1]
            self.cache[key] = frame
            if len(self.cache) > 48:
                self.cache.popitem(last=False)
            return frame

    def close(self):
        try:
            self.container.close()
        except Exception:
            pass


class ImageSource:
    def __init__(self, path: str):
        with av.open(path) as c:
            self.frame = next(c.decode(video=0))

    def frame_at(self, t: float):
        return self.frame

    def close(self):
        pass


class AudioSource:
    """Ses dosyasını proje örnekleme hızında stereo float32 olarak okur."""

    def __init__(self, path: str, sample_rate: int):
        self.path = path
        self.sr = sample_rate
        self.container = av.open(path)
        self.stream = self.container.streams.audio[0]
        self.tb = float(self.stream.time_base)
        self.start = float(self.stream.start_time * self.tb) if self.stream.start_time else 0.0
        self._buf = np.zeros((0, 2), np.float32)
        self._buf_t = -1.0  # tamponun ilk örneğinin zamanı
        self._decoder = None
        self._eof = False
        self.lock = threading.Lock()

    def _seek(self, t: float):
        target = int(max(t + self.start - 0.05, 0) / self.tb)
        self.container.seek(target, stream=self.stream, backward=True)
        self._decoder = self.container.decode(self.stream)
        self._resampler = av.AudioResampler(format="flt", layout="stereo", rate=self.sr)
        self._buf = np.zeros((0, 2), np.float32)
        self._buf_t = -1.0
        self._eof = False

    def _decode_more(self) -> bool:
        try:
            frame = next(self._decoder)
        except (StopIteration, av.error.EOFError):
            self._eof = True
            return False
        ft = (frame.time if frame.time is not None else 0.0) - self.start
        frame.pts = None
        for rf in self._resampler.resample(frame):
            arr = rf.to_ndarray().reshape(-1, 2).astype(np.float32)
            if self._buf_t < 0:
                self._buf_t = ft
            self._buf = np.concatenate([self._buf, arr])
        return True

    def read(self, t: float, n: int) -> np.ndarray:
        """t saniyesinden başlayarak n örnek (n, 2) döndürür."""
        with self.lock:
            out = np.zeros((n, 2), np.float32)
            if t < 0:
                skip = min(n, int(round(-t * self.sr)))
                if skip >= n:
                    return out
                out[skip:] = self.read_unlocked(0.0, n - skip)
                return out
            return self.read_unlocked(t, n)

    def read_unlocked(self, t: float, n: int) -> np.ndarray:
        out = np.zeros((n, 2), np.float32)
        buf_end = self._buf_t + len(self._buf) / self.sr
        if self._decoder is None or self._buf_t < 0 or t < self._buf_t - 1e-3 or t > buf_end + 0.5:
            self._seek(t)
        while not self._eof and (self._buf_t < 0 or
                                 self._buf_t + len(self._buf) / self.sr < t + n / self.sr):
            self._decode_more()
        if self._buf_t < 0:
            return out
        offset = int(round((t - self._buf_t) * self.sr))
        if offset < 0:
            dst = -offset
            chunk = self._buf[:max(0, n - dst)]
            out[dst:dst + len(chunk)] = chunk
        else:
            chunk = self._buf[offset:offset + n]
            out[:len(chunk)] = chunk
            # Okunan kısmı at ama biraz geri tampon bırak.
            drop = max(0, offset - self.sr)
            if drop:
                self._buf = self._buf[drop:]
                self._buf_t += drop / self.sr
        return out

    def close(self):
        try:
            self.container.close()
        except Exception:
            pass


class SourcePool:
    """Her medya dosyası için açık çözücüleri tutar (her iş parçacığı kendi havuzunu kullanır)."""

    def __init__(self, sample_rate: int = 48000):
        self.sr = sample_rate
        self.video: dict[str, object] = {}
        self.audio: dict[str, AudioSource] = {}
        self.failed: set[str] = set()

    def video_source(self, media: MediaItem):
        src = self.video.get(media.id)
        if src is None and media.id not in self.failed:
            try:
                src = ImageSource(media.path) if media.kind == "image" else VideoSource(media.path)
            except Exception:
                self.failed.add(media.id)
                return None
            self.video[media.id] = src
        return src

    def audio_source(self, media: MediaItem) -> AudioSource | None:
        key = media.id + "/a"
        src = self.audio.get(key)
        if src is None and key not in self.failed:
            try:
                src = AudioSource(media.path, self.sr)
            except Exception:
                self.failed.add(key)
                return None
            self.audio[key] = src
        return src

    def close(self):
        for s in list(self.video.values()) + list(self.audio.values()):
            s.close()
        self.video.clear()
        self.audio.clear()


def thumbnail(path: str, kind: str, width: int = 160) -> np.ndarray | None:
    """Medya kutusu için küçük RGB önizleme."""
    if kind == "audio":
        return None
    try:
        with av.open(path) as c:
            s = c.streams.video[0]
            if kind == "video" and c.duration:
                c.seek(int(min(1.0, c.duration / av.time_base / 3) * av.time_base))
            f = next(c.decode(s))
            h = max(2, int(f.height * width / max(f.width, 1)) // 2 * 2)
            return f.to_ndarray(width=width, height=h, format="rgb24")
    except Exception:
        return None


def waveform(path: str, buckets_per_sec: int = 50) -> np.ndarray | None:
    """Zaman çizelgesi için ses dalga formu (saniyede buckets_per_sec tepe değeri)."""
    try:
        with av.open(path) as c:
            s = c.streams.audio[0]
            rs = av.AudioResampler(format="flt", layout="mono", rate=8000)
            parts = []
            for frame in c.decode(s):
                frame.pts = None
                for rf in rs.resample(frame):
                    parts.append(rf.to_ndarray().reshape(-1))
            if not parts:
                return None
            data = np.abs(np.concatenate(parts))
            step = 8000 // buckets_per_sec
            n = len(data) // step
            if n == 0:
                return None
            return data[:n * step].reshape(n, step).max(axis=1).astype(np.float32)
    except Exception:
        return None
