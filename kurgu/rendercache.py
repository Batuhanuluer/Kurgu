"""Önizleme render önbelleği (Premiere'deki "Render Effects In to Out" ve kırmızı/yeşil çubuk).

- Her kare bir *imza* ile saklanır: o anda görünen kliplerin içeriğinden üretilir.
  Bir klibi değiştirince yalnızca o klibin göründüğü kareler geçersiz olur.
- Arka plan işçisi, oynatma sırasında oynatma kafasının önündeki kareleri hazırlar
  ve "Render" komutuyla efektli bölümleri baştan hesaplar.
- Arka plan işçisi projenin bir kopyası üzerinde ve kendi çözücüleriyle çalışır;
  arayüz iş parçacığıyla veri paylaşmaz.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections import OrderedDict
from dataclasses import asdict

from .media import SourcePool
from .model import Project
from .render import Compositor


# ------------------------------------------------------------ kare imzası
class FrameSig:
    """t anındaki karenin içeriğini belirleyen değerlerin özeti."""

    def __init__(self, project: Project, proxy: bool = False):
        self.p = project
        self.proxy = proxy  # önizleme proxy'den mi okunuyor
        self.comp = Compositor(project, pool=_NullPool())
        self._h: dict[str, str] = {}

    def clip_hash(self, c) -> str:
        h = self._h.get(c.id)
        if h is None:
            m = self.p.media.get(c.media_id)
            px = m.proxy if (m and self.proxy and m.proxy and os.path.exists(m.proxy)) else ""
            style = self.p.tracks[c.track].style if (c.kind == "caption" and
                                                     c.track < len(self.p.tracks)) else None
            data = json.dumps([asdict(c), m.path if m else "", _file_id(m.path) if m else "",
                               px, self.p.media_offset(c), style], sort_keys=True, default=str)
            h = hashlib.blake2b(data.encode(), digest_size=10).hexdigest()
            self._h[c.id] = h
        return h

    def __call__(self, t: float) -> str:
        p = self.p
        parts = [p.width, p.height]
        for ti in p.track_indices("video"):
            if p.tracks[ti].muted:
                continue
            st = self.comp.track_state(ti, t)
            if st is None:
                continue
            if st[0] == "clip":
                parts.append((ti, self.clip_hash(st[1])))
                mt = st[1].matte or {}
                if isinstance(mt.get("track"), int) and 0 <= mt["track"] < len(p.tracks):
                    ms = self.comp.track_state(mt["track"], t)  # mat izinin içeriği de etkiler
                    if ms is not None and ms[0] == "clip":
                        parts.append(("matte", self.clip_hash(ms[1])))
                    elif ms is not None:
                        parts.append(("matte", ms[4], ms[1] and self.clip_hash(ms[1]),
                                      ms[2] and self.clip_hash(ms[2]), round(ms[3], 3)))
            else:
                _k, a, b, _prog, kind = st
                parts.append((ti, kind, a and self.clip_hash(a), b and self.clip_hash(b)))
        return hashlib.blake2b(repr(parts).encode(), digest_size=12).hexdigest()


_FILE_IDS: dict = {}


def _file_id(path: str) -> str:
    """Dosya değişirse (aynı adla üzerine yazılırsa) eski render dosyaları kullanılmasın."""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    return f"{st.st_size}:{int(st.st_mtime)}"


class _NullPool:
    """İmza hesabı medya açmaz."""
    sr = 48000

    def video_source(self, m):
        return None

    def audio_source(self, m):
        return None

    def close(self):
        pass


def is_heavy(project: Project, c) -> bool:
    if not project.track_is_video(c.track) or project.tracks[c.track].muted:
        return False
    return bool(any(e.get("enabled", True) for e in c.effects) or c.keyframes
                or c.trans_in or c.trans_out)


def heavy_intervals(project: Project) -> list[tuple[float, float]]:
    """Efekt, anahtar kare ya da geçiş içeren bölümler (render gerektirenler)."""
    spans = []
    for c in project.clips:
        if not is_heavy(project, c):
            continue
        lo, hi = c.start, c.end
        info = project.trans_in_info(c)
        if info is not None:
            lo = min(lo, info[1])
        spans.append((lo, hi))
    spans.sort()
    out: list[list[float]] = []
    for a, b in spans:
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def heavy_frames(project: Project) -> list[int]:
    fps = project.fps
    frames = []
    for a, b in heavy_intervals(project):
        frames.extend(range(int(math.floor(a * fps + 1e-6)), int(math.ceil(b * fps - 1e-6))))
    return sorted(set(frames))


# ------------------------------------------------------------ önbellek
class FrameCache:
    """İş parçacığı güvenli LRU kare önbelleği (bayt bütçeli) + isteğe bağlı kalıcı disk
    katmanı (Premiere'deki "önizleme dosyaları"): render edilen kareler diske JPEG olarak
    yazılır, program yeniden açılınca tekrar render gerekmez. Anahtar içerik özetidir;
    proje değişse de aynı içerik aynı dosyayı bulur."""

    def __init__(self, budget_mb: int | None = None, disk_dir: str | None = None,
                 disk_mb: int | None = None):
        mb = budget_mb or int(os.environ.get("KURGU_CACHE_MB", "1024"))
        self.budget = mb * 1024 * 1024
        self.items: OrderedDict = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()
        self.generation = 0  # değişince zaman çizelgesi çubuğu yeniden hesaplanır
        self.disk_dir = disk_dir
        self.disk_budget = (disk_mb if disk_mb is not None else
                            int(os.environ.get("KURGU_DISK_CACHE_MB", "10240"))) * 1024 * 1024
        self.disk_index: set[str] = set()
        self.disk_bytes = 0
        if disk_dir:
            try:
                os.makedirs(disk_dir, exist_ok=True)
                for n in os.listdir(disk_dir):
                    if n.endswith(".jpg"):
                        self.disk_index.add(n)
                        try:
                            self.disk_bytes += os.path.getsize(os.path.join(disk_dir, n))
                        except OSError:
                            pass
            except OSError:
                self.disk_dir = None

    @staticmethod
    def _name(key) -> str:
        fi, sig, w, h = key
        return f"{sig}_{fi}_{w}x{h}.jpg"

    def get(self, key):
        with self.lock:
            img = self.items.get(key)
            if img is not None:
                self.items.move_to_end(key)
                return img
            name = self._name(key) if self.disk_dir else None
            on_disk = name in self.disk_index if name else False
        if on_disk:
            from PySide6.QtGui import QImage
            img = QImage(os.path.join(self.disk_dir, name))
            if img.isNull():
                with self.lock:
                    self.disk_index.discard(name)
                return None
            img = img.convertToFormat(QImage.Format_RGB32)
            self._put_mem(key, img)
            return img
        return None

    def has(self, key) -> bool:
        with self.lock:
            return key in self.items or (self.disk_dir is not None
                                         and self._name(key) in self.disk_index)

    def _put_mem(self, key, img):
        size = img.sizeInBytes()
        with self.lock:
            if key in self.items:
                return
            self.items[key] = img
            self.bytes += size
            while self.bytes > self.budget and self.items:
                _k, old = self.items.popitem(last=False)
                self.bytes -= old.sizeInBytes()
            self.generation += 1

    def put(self, key, img, persist: bool = False):
        self._put_mem(key, img)
        if persist and self.disk_dir:
            name = self._name(key)
            with self.lock:
                if name in self.disk_index:
                    return
            path = os.path.join(self.disk_dir, name)
            tmp = path + ".tmp.jpg"
            if img.save(tmp, "JPG", 93):
                try:
                    os.replace(tmp, path)
                    size = os.path.getsize(path)
                except OSError:
                    return
                with self.lock:
                    self.disk_index.add(name)
                    self.disk_bytes += size
                    self.generation += 1
                if self.disk_bytes > self.disk_budget:
                    self._trim_disk()

    def _trim_disk(self):
        """Disk bütçesi aşılınca en eski dosyaları sil (%80'e kadar)."""
        try:
            files = [(e.stat().st_mtime, e.stat().st_size, e.path, e.name)
                     for e in os.scandir(self.disk_dir) if e.name.endswith(".jpg")]
        except OSError:
            return
        files.sort()
        total = sum(f[1] for f in files)
        for _m, size, path, name in files:
            if total <= self.disk_budget * 0.8:
                break
            try:
                os.remove(path)
            except OSError:
                continue
            total -= size
            with self.lock:
                self.disk_index.discard(name)
        with self.lock:
            self.disk_bytes = total
            self.generation += 1

    def clear(self, disk: bool = False):
        with self.lock:
            self.items.clear()
            self.bytes = 0
            self.generation += 1
        if disk and self.disk_dir:
            for name in list(self.disk_index):
                try:
                    os.remove(os.path.join(self.disk_dir, name))
                except OSError:
                    pass
            with self.lock:
                self.disk_index.clear()
                self.disk_bytes = 0

    def frame_bytes(self, w: int, h: int) -> int:
        return w * h * 4


# ------------------------------------------------------------ arka plan işçisi
class RenderWorker:
    """Kareleri arka planda hazırlar. Yeni bir iş eskisinin yerini alır."""

    def __init__(self, cache: FrameCache):
        self.cache = cache
        self.cond = threading.Condition()
        self.job = None
        self.job_id = 0
        self.stop_flag = False
        self._reset = False
        self.pool = None
        self.thread = threading.Thread(target=self._run, name="kurgu-render", daemon=True)
        self.thread.start()

    def submit(self, snapshot: Project, frames: list[int], w: int, h: int,
               progress=None, done=None, use_proxy: bool = False, persist: bool = False) -> int:
        with self.cond:
            self.job_id += 1
            self.job = (self.job_id, snapshot, list(frames), w, h, progress, done, use_proxy,
                        persist)
            self.cond.notify()
            return self.job_id

    def cancel(self):
        with self.cond:
            self.job_id += 1
            self.job = None

    def busy_with(self, job_id: int) -> bool:
        return self.job_id == job_id

    def reset_sources(self):
        """Yeni proje açılınca: çözücüleri işçi iş parçacığında kapat."""
        with self.cond:
            self.job_id += 1
            self.job = None
            self._reset = True
            self.cond.notify()

    def shutdown(self):
        with self.cond:
            self.stop_flag = True
            self.job = None
            self.cond.notify()

    def _run(self):
        while True:
            with self.cond:
                while self.job is None and not self.stop_flag and not self._reset:
                    self.cond.wait()
                if self.stop_flag:
                    break
                if self._reset:
                    self._reset = False
                    if self.pool is not None:
                        self.pool.close()
                        self.pool = None
                    continue
                job = self.job
                self.job = None
            jid, snap, frames, w, h, progress, done, use_proxy, persist = job
            if self.pool is None:  # medya kimlikleri kopyada korunur: çözücüler yeniden kullanılır
                self.pool = SourcePool(snap.sample_rate)
            self.pool.use_proxy = use_proxy
            comp = Compositor(snap, self.pool)
            sig = FrameSig(snap, use_proxy)
            n = len(frames)
            ok = True
            for i, fi in enumerate(frames):
                if self.job_id != jid or self.stop_flag:
                    ok = False
                    break
                t = fi / snap.fps
                key = (fi, sig(t), w, h)
                if not self.cache.has(key):
                    try:
                        self.cache.put(key, comp.compose(t, w, h), persist=persist)
                    except Exception:
                        pass
                if progress is not None:
                    progress(i + 1, n)
            if done is not None:
                done(ok)
